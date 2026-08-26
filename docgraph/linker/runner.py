"""Linker runner —— pipeline 中调用的统一入口。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from docgraph.core.config import DocGraphConfig
from docgraph.core.logger import get_logger
from docgraph.core.manifest import Manifest
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.linker.entity_resolver import EntityResolver
from docgraph.linker.federation import FederationLinker
from docgraph.linker.llm_ie import LLMIELinker
from docgraph.linker.relation_infer import RelationInferLinker
from docgraph.linker.xref import XRefLinker

log = get_logger(__name__)


@dataclass
class LinkReport:
    belongs_to_edges: int = 0
    contained_in_edges: int = 0
    llm_ie_edges: int = 0
    xref_edges: int = 0
    xref_unresolved: int = 0
    alias_edges: int = 0
    merge_groups: int = 0
    supersedes_edges: int = 0
    fed_alias_edges: int = 0
    duration_s: float = 0.0


def run_linker(
    root: Path,
    cfg: DocGraphConfig,
    store: SQLiteGraphStore,
    manifest: Manifest,
    *,
    llm_client=None,
) -> LinkReport:
    """跑全套 linker：relation_infer → llm_ie → xref → entity_resolver → federation。"""
    t0 = time.time()

    # 0. 确定性关系推断（ADR-015 B 层）：章节归属 + 地址 join
    ri = RelationInferLinker().run(store)

    # 1. LLM 开放 IE（ADR-015 C 层）：补 B 未覆盖的语义关系
    ie = LLMIELinker().run(store, llm_client=llm_client)

    # 2. xref
    xref = XRefLinker().run(store, root=root)

    # 3. entity resolve
    er = EntityResolver().run(store, root=root)

    # 4. federation
    doc_priorities: dict[str, int] = {}
    for path_str, rec in manifest.files.items():
        if not rec.doc_id:
            continue
        meta = cfg.docs.metadata.get(path_str) or {}
        doc_priorities[rec.doc_id] = int(meta.get("priority", 10))
    fed = FederationLinker().run(store, doc_priorities=doc_priorities)

    rep = LinkReport(
        belongs_to_edges=ri.belongs_to_edges,
        contained_in_edges=ri.contained_in_edges,
        llm_ie_edges=ie.edges_created,
        xref_edges=xref.edges_added,
        xref_unresolved=xref.unresolved,
        alias_edges=er.alias_edges,
        merge_groups=er.groups,
        supersedes_edges=fed.supersedes_edges,
        fed_alias_edges=fed.alias_edges,
        duration_s=round(time.time() - t0, 2),
    )
    log.info(
        f"[link] done in {rep.duration_s}s — "
        f"belongs_to={rep.belongs_to_edges} "
        f"contained_in={rep.contained_in_edges} "
        f"llm_ie={rep.llm_ie_edges} "
        f"xref={rep.xref_edges} (unresolved {rep.xref_unresolved}), "
        f"alias={rep.alias_edges}, "
        f"supersedes={rep.supersedes_edges}"
    )
    return rep
