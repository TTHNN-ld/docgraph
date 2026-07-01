"""EntityResolver —— 实体消歧（M2：Stage 1+2 规则）。

策略：
- Stage 1：完全相同的 qualified_name + 同 family → merge 候选
- Stage 2：归一后相同（去前缀 / 大小写 / 下划线↔连字符）→ alias
- Stage 3：LLM 兜底（M3 实施）

输出：在图里写 ALIAS_OF 边；同时写 .docgraph/entities/linker.merged.jsonl 审计日志。
"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from docgraph.core.logger import get_logger
from docgraph.graph.schema import Edge, EdgeKind, Evidence, Node, NodeKind
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.graph.store import NodeQuery

log = get_logger(__name__)


@dataclass
class ResolveResult:
    alias_edges: int = 0
    groups: int = 0


_NORM_RE = re.compile(r"[\s_\-./]+")


def normalize(name: str) -> str:
    return _NORM_RE.sub("", name).upper()


class EntityResolver:
    name = "entity_resolver"
    version = "0.1"

    # 哪些 kind 参与归并（registers/pins/signals 等"硬名称"）
    TARGET_KINDS = (NodeKind.REGISTER, NodeKind.PIN, NodeKind.PARAMETER, NodeKind.SIGNAL)

    AUDIT_REL = "entities/linker.merged.jsonl"

    def run(self, store: SQLiteGraphStore, root: Path | None = None) -> ResolveResult:
        t0 = time.time()
        alias_edges = 0
        audit: list[dict] = []

        for kind in self.TARGET_KINDS:
            nodes = store.search_nodes(NodeQuery(kind=kind, limit=10000))
            buckets: dict[str, list[Node]] = defaultdict(list)
            for n in nodes:
                key = normalize(n.qualified_name or n.name)
                buckets[key].append(n)

            for key, group in buckets.items():
                if len(group) < 2:
                    continue
                # 主节点：按 doc 的 priority 决定（无法直接拿到 priority，用 page 较前 + 名字较短）
                primary = sorted(
                    group, key=lambda n: (n.location.page or 9999, len(n.name))
                )[0]
                for other in group:
                    if other.id == primary.id:
                        continue
                    # ALIAS_OF 双向
                    try:
                        store.upsert_edge(
                            Edge(
                                src=other.id,
                                dst=primary.id,
                                kind=EdgeKind.ALIAS_OF,
                                confidence=0.95,
                                evidence=Evidence(
                                    extractor=f"{self.name}@{self.version}:rule",
                                    raw_snippet=f"normalized key: {key}",
                                ),
                            )
                        )
                        alias_edges += 1
                    except Exception:
                        pass
                audit.append(
                    {
                        "key": key,
                        "primary": primary.id,
                        "members": [n.id for n in group],
                        "kind": kind.value,
                    }
                )

        if audit and root is not None:
            self._write_audit(root, audit)

        log.info(
            f"[link] entity-resolve: {alias_edges} alias edges, "
            f"{len(audit)} merge groups ({round(time.time() - t0, 2)}s)"
        )
        return ResolveResult(alias_edges=alias_edges, groups=len(audit))

    @staticmethod
    def _write_audit(root: Path, records: list[dict]) -> None:
        p = root / ".docgraph" / "entities" / "linker.merged.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
