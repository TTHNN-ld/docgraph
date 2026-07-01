"""docgraph.linker —— 跨实体、跨文档的关系建边。"""
from docgraph.linker.xref import XRefLinker
from docgraph.linker.entity_resolver import EntityResolver
from docgraph.linker.federation import FederationLinker
from docgraph.linker.runner import run_linker

__all__ = [
    "XRefLinker",
    "EntityResolver",
    "FederationLinker",
    "run_linker",
]
