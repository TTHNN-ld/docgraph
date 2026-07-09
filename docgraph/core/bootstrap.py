"""注册表 bootstrap —— 内置组件 + entry_points 发现。"""
from __future__ import annotations

from docgraph.core.plugins import discover_entry_points, mark_builtin
from docgraph.embeddings.base import registry as embed_registry
from docgraph.embeddings.hash_encoder import BgeM3Encoder, HashEncoder
from docgraph.extractors.base import registry as extractor_registry
from docgraph.extractors.figure import FigureExtractor
from docgraph.extractors.glossary import GlossaryExtractor
from docgraph.extractors.section import SectionExtractor
from docgraph.extractors.table_entity import TableEntityExtractor
from docgraph.extractors.text_entity import TextEntityExtractor
from docgraph.parsers.base import registry as parser_registry
from docgraph.parsers.docling_parser import DoclingParser
from docgraph.parsers.docx_parser import DocxParser
from docgraph.parsers.markdown_parser import MarkdownParser
from docgraph.parsers.mineru_parser import MinerUParser
from docgraph.parsers.pymupdf_parser import PyMuPDFParser
from docgraph.parsers.xlsx_parser import XlsxParser

_BOOTSTRAPPED = False


def bootstrap(*, disabled_plugins: set[str] | None = None) -> None:
    """注册所有组件：先内置，再 entry_points。

    Args:
        disabled_plugins: 形如 {"docgraph.extractors:foo"} 的禁用集合。
    """
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return

    # 内置 Parsers
    for cls, name in [
        (PyMuPDFParser, "pymupdf"),
        (DoclingParser, "docling"),
        (MinerUParser, "mineru"),
        (DocxParser, "docx"),
        (MarkdownParser, "markdown"),
        (XlsxParser, "xlsx"),
    ]:
        parser_registry.register(cls)
        mark_builtin("docgraph.parsers", name, f"{cls.__module__}:{cls.__name__}")

    # 内置 Extractors —— 统一走 TableEntityExtractor + schema registry
    for cls, name in [
        (SectionExtractor, "section"),
        (TableEntityExtractor, "table_entity"),
        (GlossaryExtractor, "glossary"),
        (TextEntityExtractor, "text_entity"),
        (FigureExtractor, "figure"),
    ]:
        extractor_registry.register(cls)
        mark_builtin("docgraph.extractors", name, f"{cls.__module__}:{cls.__name__}")

    # 内置 Embeddings
    for cls, name in [
        (HashEncoder, "hash"),
        (BgeM3Encoder, "bge_m3"),
    ]:
        embed_registry.register(cls)
        mark_builtin("docgraph.embeddings", name, f"{cls.__module__}:{cls.__name__}")

    # entry_points 发现
    discover_entry_points(disabled=disabled_plugins or set())

    _BOOTSTRAPPED = True
