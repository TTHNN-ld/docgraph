"""XRefLinker —— 解析正文中的 'see Section 3.2'/'Figure 4-1'/'Table 7-2' 等引用。

策略：
- 扫描所有节点的 summary + attrs.description 中的 xref 模式
- 在图谱中查找匹配的 Section/Figure/Table 节点
- 建 REFERENCES 边；找不到的写入 .docgraph/entities/linker.unresolved.jsonl
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from docgraph.core.logger import get_logger
from docgraph.graph.schema import Edge, EdgeKind, Evidence, NodeKind
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.graph.store import NodeQuery

log = get_logger(__name__)


# 正则
_XREF_PATTERNS: list[tuple[re.Pattern, NodeKind]] = [
    # Section 3.2 / Section 3.2.1
    (re.compile(r"\b(?:see\s+)?(?:section|sec\.|§|章节)\s+(\d+(?:\.\d+){0,4})", re.I), NodeKind.SECTION),
    # Chapter 5
    (re.compile(r"\b(?:see\s+)?(?:chapter|chap\.|第)\s*(\d+(?:\.\d+){0,2})\s*章?", re.I), NodeKind.SECTION),
    # Figure 3-2 / Fig. 4
    (re.compile(r"\b(?:see\s+)?(?:figure|fig\.?|图)\s*([\dA-Z]+(?:[-.]\d+){0,2})", re.I), NodeKind.FIGURE),
    # Table 7-2 / 表 7-2
    (re.compile(r"\b(?:see\s+)?(?:table|tbl\.?|表)\s*([\dA-Z]+(?:[-.]\d+){0,2})", re.I), NodeKind.TABLE),
]


@dataclass
class XRefResult:
    edges_added: int = 0
    unresolved: int = 0


class XRefLinker:
    name = "xref"
    version = "0.1"
    UNRESOLVED_REL = "entities/linker.unresolved.jsonl"

    def run(self, store: SQLiteGraphStore, root: Path | None = None) -> XRefResult:
        t0 = time.time()
        edges_added = 0
        unresolved: list[dict] = []

        # 我们只扫描章节、寄存器、位域、参数等"语义节点"的 summary + description
        # 来源节点候选
        source_kinds = [
            NodeKind.SECTION,
            NodeKind.REGISTER,
            NodeKind.BITFIELD,
            NodeKind.PARAMETER,
            NodeKind.PIN,
        ]

        for kind in source_kinds:
            nodes = store.search_nodes(NodeQuery(kind=kind, limit=10000))
            for src in nodes:
                text_pool = " ".join(
                    [
                        src.summary or "",
                        str(src.attrs.get("description", "")),
                        str(src.attrs.get("condition", "")),
                    ]
                )
                if not text_pool.strip():
                    continue
                for pattern, target_kind in _XREF_PATTERNS:
                    for m in pattern.finditer(text_pool):
                        target_key = m.group(1)
                        target = self._find_target(
                            store, target_kind, target_key, src.doc_id
                        )
                        if target is None:
                            unresolved.append(
                                {
                                    "src": src.id,
                                    "kind": target_kind.value,
                                    "key": target_key,
                                    "context": text_pool[: m.start()][-60:] + "…",
                                }
                            )
                            continue
                        if target.id == src.id:
                            continue
                        try:
                            store.upsert_edge(
                                Edge(
                                    src=src.id,
                                    dst=target.id,
                                    kind=EdgeKind.REFERENCES,
                                    confidence=0.85,
                                    evidence=Evidence(
                                        extractor=f"{self.name}@{self.version}",
                                        raw_snippet=m.group(0),
                                    ),
                                )
                            )
                            edges_added += 1
                        except Exception:
                            pass

        if unresolved and root is not None:
            self._write_unresolved(root, unresolved)

        log.info(
            f"[link] xref: {edges_added} edges added, {len(unresolved)} unresolved "
            f"({round(time.time() - t0, 2)}s)"
        )
        return XRefResult(edges_added=edges_added, unresolved=len(unresolved))

    @staticmethod
    def _find_target(
        store: SQLiteGraphStore,
        kind: NodeKind,
        key: str,
        prefer_doc: str | None,
    ):
        # 1. 精确按 section_path（section）
        if kind == NodeKind.SECTION:
            # section_path 命中
            results = store.search_nodes(
                NodeQuery(kind=kind, fuzzy=key, limit=10)
            )
            # 优先 qualified_name 以 key 开头的，再优先同 doc
            results.sort(
                key=lambda n: (
                    n.doc_id != prefer_doc,
                    not (n.qualified_name or "").startswith(key),
                )
            )
            return results[0] if results else None

        # Figure / Table 名字里可能带 "Figure 3-2 caption"，做模糊
        results = store.search_nodes(
            NodeQuery(kind=kind, fuzzy=key, limit=10)
        )
        if not results:
            return None
        results.sort(key=lambda n: (n.doc_id != prefer_doc,))
        return results[0]

    @staticmethod
    def _write_unresolved(root: Path, records: list[dict]) -> None:
        p = root / ".docgraph" / "entities" / "linker.unresolved.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
