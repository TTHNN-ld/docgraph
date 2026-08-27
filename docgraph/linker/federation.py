"""FederationLinker —— 同项目多文档的 SUPERSEDES / ALIAS_OF 边。

当前简化规则：
- runner 从 manifest / config.docs.metadata 传入每个文档的 priority
- 跨 doc 的同 qualified_name 节点之间：
  - 高 priority doc → 低 priority doc：建 SUPERSEDES 边
  - 同 priority：从选定的 primary 建 ALIAS_OF 边

`DocMetadata.supersedes` 目前尚未进入此判定路径。
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass

from docgraph.core.logger import get_logger
from docgraph.graph.schema import Edge, EdgeKind, Evidence, NodeKind
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.graph.traversal import iter_nodes

log = get_logger(__name__)


@dataclass
class FederationResult:
    supersedes_edges: int = 0
    alias_edges: int = 0


class FederationLinker:
    name = "federation"
    version = "0.2"
    TARGET_KINDS = (NodeKind.REGISTER, NodeKind.PIN, NodeKind.PARAMETER)

    def run(
        self,
        store: SQLiteGraphStore,
        *,
        doc_priorities: dict[str, int],
        doc_instances: dict[str, str] | None = None,
    ) -> FederationResult:
        """doc_priorities: doc_id → priority (越大越权威)。"""
        t0 = time.time()
        sup_edges = 0
        alias_edges = 0

        for kind in self.TARGET_KINDS:
            nodes = iter_nodes(store, kind)
            buckets: dict[tuple[str, str], list] = defaultdict(list)
            for n in nodes:
                name_key = (n.qualified_name or n.name).upper()
                instance = (doc_instances or {}).get(n.doc_id) or _family_from_doc_id(n.doc_id)
                buckets[(name_key, instance)].append(n)

            for (key, instance), group in buckets.items():
                if len(group) < 2:
                    continue
                # 同 family 不同 doc 才算联邦
                docs_in_group = {n.doc_id for n in group}
                if len(docs_in_group) < 2:
                    continue
                # 按 priority 排序
                sorted_group = sorted(
                    group,
                    key=lambda n: -doc_priorities.get(n.doc_id, 0),
                )
                primary = sorted_group[0]
                primary_prio = doc_priorities.get(primary.doc_id, 0)
                for other in sorted_group[1:]:
                    other_prio = doc_priorities.get(other.doc_id, 0)
                    if primary_prio > other_prio:
                        edge_kind = EdgeKind.SUPERSEDES
                        confidence = 0.95
                        sup_edges += 1
                    else:
                        edge_kind = EdgeKind.ALIAS_OF
                        confidence = 0.9
                        alias_edges += 1
                    store.upsert_edge(
                        Edge(
                            src=primary.id,
                            dst=other.id,
                            kind=edge_kind,
                            confidence=confidence,
                            evidence=Evidence(
                                extractor=f"{self.name}@{self.version}",
                                raw_snippet=f"federation merge for {key} instance={instance}",
                            ),
                        )
                    )

        log.info(
            f"[link] federation: {sup_edges} SUPERSEDES, {alias_edges} ALIAS_OF "
            f"({round(time.time() - t0, 2)}s)"
        )
        return FederationResult(supersedes_edges=sup_edges, alias_edges=alias_edges)


def _family_from_doc_id(doc_id: str) -> str:
    return doc_id.split("::", 1)[0] if "::" in doc_id else ""
