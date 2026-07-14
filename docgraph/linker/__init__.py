"""docgraph.linker —— 跨实体、跨文档的关系建边。"""
from docgraph.linker.entity_resolver import EntityResolver
from docgraph.linker.federation import FederationLinker
from docgraph.linker.runner import run_linker
from docgraph.linker.xref import XRefLinker

__all__ = [
    "EntityResolver",
    "FederationLinker",
    "XRefLinker",
    "run_linker",
]
