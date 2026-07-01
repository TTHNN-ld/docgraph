"""Smoke tests for schema + SQLite store + extractors."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from docgraph.core.ids import make_doc_id, make_node_id
from docgraph.extractors.base import ExtractContext
from docgraph.extractors.section import SectionExtractor
from docgraph.graph.schema import (
    DocMetadata,
    Edge,
    EdgeKind,
    Evidence,
    Location,
    Node,
    NodeKind,
    ParsedDoc,
    ParsedPage,
    TextBlock,
    TocEntry,
)
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.graph.store import NodeQuery


@pytest.fixture()
def tmp_store() -> SQLiteGraphStore:
    with tempfile.TemporaryDirectory() as d:
        store = SQLiteGraphStore(Path(d) / "graph.db")
        store.init_schema()
        yield store
        store.close()


def test_make_ids():
    doc_id = make_doc_id("stm32f407", "datasheet", "rev9")
    assert doc_id == "stm32f407::datasheet@rev9"
    node_id = make_node_id("stm32f407", NodeKind.REGISTER, "TIM1.CR1")
    assert node_id == "stm32f407::reg:TIM1.CR1"
    node_id2 = make_node_id(
        "stm32f407", NodeKind.REGISTER, "TIM1.CR1", doc_id=doc_id
    )
    assert node_id2 == "stm32f407::reg:TIM1.CR1#stm32f407::datasheet@rev9"


def test_store_node_roundtrip(tmp_store: SQLiteGraphStore):
    node = Node(
        id="stm32::reg:CR1",
        kind=NodeKind.REGISTER,
        name="CR1",
        qualified_name="TIM1.CR1",
        aliases=["TIM1_CR1"],
        doc_id="stm32::datasheet@rev9",
        location=Location(page=562, section_path="17.4.1"),
        attrs={"address": "0x40010000", "width": 16},
        summary="TIM1 control register 1",
    )
    tmp_store.upsert_node(node)
    got = tmp_store.get_node("stm32::reg:CR1")
    assert got is not None
    assert got.name == "CR1"
    assert "TIM1_CR1" in got.aliases
    assert got.attrs["address"] == "0x40010000"


def test_store_edge_and_neighbors(tmp_store: SQLiteGraphStore):
    reg = Node(
        id="stm32::reg:CR1", kind=NodeKind.REGISTER, name="CR1",
        doc_id="stm32::ds",
    )
    bf = Node(
        id="stm32::bf:CR1.CEN", kind=NodeKind.BITFIELD, name="CEN",
        doc_id="stm32::ds",
    )
    tmp_store.upsert_node(reg)
    tmp_store.upsert_node(bf)
    tmp_store.upsert_edge(
        Edge(
            src=reg.id,
            dst=bf.id,
            kind=EdgeKind.HAS_BITFIELD,
            evidence=Evidence(extractor="test@0"),
        )
    )
    sub = tmp_store.neighbors(reg.id, depth=1)
    assert {n.id for n in sub.nodes} == {reg.id, bf.id}
    assert any(e.kind == EdgeKind.HAS_BITFIELD for e in sub.edges)


def test_store_search(tmp_store: SQLiteGraphStore):
    for i, name in enumerate(["TIM1_CR1", "TIM1_CR2", "TIM2_CR1"]):
        tmp_store.upsert_node(
            Node(
                id=f"stm32::reg:{name}",
                kind=NodeKind.REGISTER,
                name=name,
                doc_id="d",
            )
        )
    fuzzy = tmp_store.search_nodes(NodeQuery(fuzzy="TIM1"))
    assert len(fuzzy) == 2
    by_name = tmp_store.search_nodes(NodeQuery(name="TIM2_CR1"))
    assert len(by_name) == 1


def test_section_extractor_uses_toc():
    parsed = ParsedDoc(
        doc_id="stm32::ds",
        source_path="x.pdf",
        pages=[ParsedPage(page_no=1)],
        toc=[
            TocEntry(level=1, title="Chapter 1", page=1, section_path="1"),
            TocEntry(level=2, title="Sub 1.1", page=2, section_path="1.1"),
            TocEntry(level=2, title="Sub 1.2", page=5, section_path="1.2"),
        ],
        metadata=DocMetadata(family="stm32"),
    )
    ex = SectionExtractor()
    result = ex.extract(parsed, ExtractContext(family="stm32"))
    assert len(result.nodes) == 3
    # 父子边：第一个章节 → 子章节
    contains = [e for e in result.edges if e.kind == EdgeKind.CONTAINS]
    assert len(contains) == 2


def test_table_entity_extractor_counts():
    """无 LLM 时 TableEntityExtractor 返回空结果，不崩溃。"""
    from docgraph.extractors.table_entity import TableEntityExtractor
    parsed = ParsedDoc(doc_id="stm32::ds", source_path="x.pdf",
                        pages=[ParsedPage(page_no=1)])
    res = TableEntityExtractor().extract(parsed, ExtractContext(family="stm32"))
    assert res.nodes == []
