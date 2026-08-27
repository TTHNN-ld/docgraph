"""Linker runner —— pipeline 中调用的统一入口。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
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
    llm_ie_calls: int = 0
    duration_s: float = 0.0
    warnings: list[str] = field(default_factory=list)


LINKER_PRODUCERS = {
    RelationInferLinker.name,
    LLMIELinker.name,
    XRefLinker.name,
    EntityResolver.name,
    FederationLinker.name,
}


def linker_versions() -> dict[str, str]:
    return {
        cls.name: cls.version
        for cls in (
            RelationInferLinker,
            LLMIELinker,
            XRefLinker,
            EntityResolver,
            FederationLinker,
        )
    }


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

    doc_priorities = {
        rec.doc_id: int((cfg.docs.metadata.get(path_str) or {}).get("priority", 10))
        for path_str, rec in manifest.files.items()
        if rec.doc_id
    }
    doc_instances = {
        rec.doc_id: str((cfg.docs.metadata.get(path_str) or {}).get("chip_model"))
        for path_str, rec in manifest.files.items()
        if rec.doc_id and (cfg.docs.metadata.get(path_str) or {}).get("chip_model")
    }
    llm_ie = LLMIELinker()
    ie, ie_plan = llm_ie.prepare(store, llm_client=llm_client)
    if ie.failed:
        raise RuntimeError(f"LLM IE failed for {ie.failed} chunk(s)")

    with store.transaction():
        store.clear_derived_graph_items(LINKER_PRODUCERS)
        ri = RelationInferLinker().run(store)
        llm_ie.apply(store, ie, ie_plan)
        xref = XRefLinker().run(store, root=None)
        er = EntityResolver().run(
            store,
            root=None,
            doc_instances=doc_instances,
            doc_priorities=doc_priorities,
        )
        fed = FederationLinker().run(
            store,
            doc_priorities=doc_priorities,
            doc_instances=doc_instances,
        )

    llm_ie._log_report(ie)

    audit_warnings: list[str] = []
    for label, writer, records in (
        ("xref", XRefLinker._write_unresolved, xref.unresolved_records or []),
        ("entity_resolver", EntityResolver._write_audit, er.audit_records or []),
    ):
        try:
            writer(root, records)
        except Exception as exc:
            warning = f"{label} audit write failed: {exc}"
            audit_warnings.append(warning)
            log.warning(f"[link] {warning}")

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
        llm_ie_calls=ie.llm_calls,
        duration_s=round(time.time() - t0, 2),
        warnings=audit_warnings,
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
