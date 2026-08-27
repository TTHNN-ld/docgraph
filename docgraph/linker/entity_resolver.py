"""EntityResolver —— 基于名称和来源范围的实体消歧。

策略：
- Stage 1：完全相同的 qualified_name + 同 family → merge 候选
- Stage 2：归一后相同（去前缀 / 大小写 / 下划线↔连字符）→ alias
- LLM 关系推断由独立 LLMIELinker 负责

输出：在图里写 ALIAS_OF 边；同时写 .docgraph/entities/linker.merged.jsonl 审计日志。
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from docgraph.core.ids import doc_name_from_doc_id, infer_chip_model
from docgraph.core.logger import get_logger
from docgraph.graph.schema import Edge, EdgeKind, Evidence, Node, NodeKind
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.graph.traversal import iter_nodes
from docgraph.linker.common import write_jsonl_atomic

log = get_logger(__name__)


@dataclass
class ResolveResult:
    alias_edges: int = 0
    groups: int = 0
    audit_records: list[dict] | None = None


_NORM_RE = re.compile(r"[\s_\-./]+")


def normalize(name: str) -> str:
    return _NORM_RE.sub("", name).upper()


def _instance_key(doc_id: str) -> str:
    """跨文档消歧的实例键：chip_model 相同才算"同一实例"。

    从 doc_id 提取文档名 → 推断 chip_model（如 cortex-m4 / pcie-subsystem）。
    推断不出时回退到 family（doc_id 第一段），兼容未配置 chip_model 的旧项目：
    旧项目所有文档同 family，同名实体仍会合并（与改造前行为一致）。
    无 family 标记的裸 doc_id（如测试用的 "d1"）统一归为空串，按同组处理。
    """
    doc_name = doc_name_from_doc_id(doc_id)
    chip = infer_chip_model(doc_name)
    if chip:
        return chip
    if "::" in doc_id:
        return doc_id.split("::")[0]  # family 段
    return ""  # 无 family 标记：归为同一组（兼容简化场景）


class EntityResolver:
    name = "entity_resolver"
    version = "0.2"

    # 哪些 kind 参与归并（"硬名称"实体，跨文档同义时建 ALIAS_OF）
    # 扩展覆盖：interrupt/memory_map/interface/module 也参与，因为跨文档
    # （datasheet ↔ TRM ↔ errata）同一中断/地址空间/接口常有不同叫法。
    TARGET_KINDS = (
        NodeKind.REGISTER,
        NodeKind.BITFIELD,
        NodeKind.PIN,
        NodeKind.PARAMETER,
        NodeKind.SIGNAL,
        NodeKind.INTERRUPT,
        NodeKind.MEMORY_MAP,
        NodeKind.INTERFACE,
        NodeKind.MODULE,
    )

    AUDIT_REL = "entities/linker.merged.jsonl"

    def run(
        self,
        store: SQLiteGraphStore,
        root: Path | None = None,
        *,
        doc_instances: dict[str, str] | None = None,
        doc_priorities: dict[str, int] | None = None,
    ) -> ResolveResult:
        t0 = time.time()
        alias_edges = 0
        audit: list[dict] = []

        for kind in self.TARGET_KINDS:
            nodes = iter_nodes(store, kind)
            buckets: dict[tuple[str, str], list[Node]] = defaultdict(list)
            for n in nodes:
                name_key = normalize(n.qualified_name or n.name)
                # 消歧实例键：chip_model（推断得出）相同才算"同一实例"。
                # chip_model 推断不出时回退到 family（doc_id 前缀），兼容旧项目。
                inst = (doc_instances or {}).get(n.doc_id) or _instance_key(n.doc_id)
                buckets[(name_key, inst)].append(n)
            for (name_key, inst), group in buckets.items():
                if len(group) < 2:
                    continue
                primary = sorted(
                    group,
                    key=lambda n: (
                        -(doc_priorities or {}).get(n.doc_id, 10),
                        n.location.page or 9999,
                        len(n.name),
                        n.id,
                    ),
                )[0]
                for other in group:
                    if other.id == primary.id:
                        continue
                    # ALIAS_OF 双向
                    store.upsert_edge(
                        Edge(
                            src=other.id,
                            dst=primary.id,
                            kind=EdgeKind.ALIAS_OF,
                            confidence=0.95,
                            evidence=Evidence(
                                extractor=f"{self.name}@{self.version}:rule",
                                raw_snippet=f"normalized={name_key} instance={inst}",
                            ),
                        )
                    )
                    alias_edges += 1
                audit.append(
                    {
                        "name_key": name_key,
                        "instance": inst,
                        "primary": primary.id,
                        "members": [n.id for n in group],
                        "kind": kind.value,
                    }
                )

        if root is not None:
            self._write_audit(root, audit)

        log.info(
            f"[link] entity-resolve: {alias_edges} alias edges, "
            f"{len(audit)} merge groups ({round(time.time() - t0, 2)}s)"
        )
        return ResolveResult(alias_edges=alias_edges, groups=len(audit), audit_records=audit)

    @staticmethod
    def _write_audit(root: Path, records: list[dict]) -> None:
        write_jsonl_atomic(root / ".docgraph" / EntityResolver.AUDIT_REL, records)
