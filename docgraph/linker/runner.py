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
from docgraph.linker.xref import XRefLinker

log = get_logger(__name__)


@dataclass
class LinkReport:
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
) -> LinkReport:
    """跑全套 linker：xref → entity_resolver → federation。"""
    t0 = time.time()

    # 1. xref
    xref = XRefLinker().run(store, root=root)

    # 2. entity resolve
    er = EntityResolver().run(store, root=root)

    # 3. federation
    doc_priorities: dict[str, int] = {}
    for path_str, rec in manifest.files.items():
        if not rec.doc_id:
            continue
        meta = cfg.docs.metadata.get(path_str) or {}
        doc_priorities[rec.doc_id] = int(meta.get("priority", 10))
    fed = FederationLinker().run(store, doc_priorities=doc_priorities)

    rep = LinkReport(
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
        f"xref={rep.xref_edges} (unresolved {rep.xref_unresolved}), "
        f"alias={rep.alias_edges}, "
        f"supersedes={rep.supersedes_edges}"
    )
    return rep
