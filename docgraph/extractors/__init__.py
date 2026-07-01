"""Extractor 层。"""
from docgraph.extractors.base import Extractor, ExtractContext, ExtractorRegistry, registry
from docgraph.extractors.figure import FigureExtractor
from docgraph.extractors.glossary import GlossaryExtractor
from docgraph.extractors.section import SectionExtractor
from docgraph.extractors.table_entity import TableEntityExtractor

__all__ = [
    "Extractor",
    "ExtractContext",
    "ExtractorRegistry",
    "registry",
    "SectionExtractor",
    "TableEntityExtractor",
    "GlossaryExtractor",
    "FigureExtractor",
]
