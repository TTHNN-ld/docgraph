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
    TocEntry,
)
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.graph.store import EdgeQuery, NodeQuery


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
    node_id2 = make_node_id("stm32f407", NodeKind.REGISTER, "TIM1.CR1", doc_id=doc_id)
    assert node_id2 == "stm32f407::reg:TIM1.CR1#stm32f407::datasheet@rev9"


def test_doc_type_inference_routes_subsystem_specs_to_protocol():
    from docgraph.core.config import DocGraphConfig, ProjectConfig
    from docgraph.core.pipeline import _infer_doc_metadata
    from docgraph.graph.schema import DocType

    cfg = DocGraphConfig(project=ProjectConfig(family="chip"))
    meta = _infer_doc_metadata(
        Path("/repo/case/PCIE Subsystem Spec_v3.21.pdf"),
        cfg,
        Path("/repo"),
    )

    assert meta.type == DocType.PROTOCOL


def test_doc_type_inference_keeps_trs_on_core_unknown_route():
    from docgraph.core.config import DocGraphConfig, ProjectConfig
    from docgraph.core.pipeline import _infer_doc_metadata
    from docgraph.graph.schema import DocType

    cfg = DocGraphConfig(project=ProjectConfig(family="chip"))
    meta = _infer_doc_metadata(
        Path("/repo/case/PCIe Subsystem TRS_r2p0.pdf"),
        cfg,
        Path("/repo"),
    )

    assert meta.type == DocType.UNKNOWN


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


def test_store_node_upsert_merges_multisource_l2_evidence(tmp_store: SQLiteGraphStore):
    table_node = Node(
        id="chip::signal:pcie_core#doc",
        kind=NodeKind.SIGNAL,
        name="pcie_core",
        aliases=["PCIE core"],
        doc_id="doc",
        location=Location(page=12, section_path="5.1"),
        evidence=Evidence(
            chunk_ids=["c-table"],
            pages=[12],
            extractor="table_entity@0.1",
            raw_snippet="Name | Width | Direction\npcie_core | 1 | input",
        ),
        attrs={
            "source": "table_entity:signal",
            "source_block_ids": ["b-table"],
            "source_chunk_ids": ["c-table"],
            "width": "1",
            "direction": "input",
        },
        summary="Signal from the interface table",
    )
    figure_node = Node(
        id=table_node.id,
        kind=NodeKind.SIGNAL,
        name="pcie_core",
        aliases=["PCIe Core"],
        doc_id="doc",
        location=Location(page=15, section_path="5.1"),
        evidence=Evidence(
            chunk_ids=["c-figure"],
            pages=[15],
            extractor="figure@0.5",
            raw_snippet="PCIE core is connected to PIPE",
        ),
        attrs={
            "source": "figure@0.5",
            "source_block_ids": ["b-figure"],
            "source_chunk_ids": ["c-figure"],
            "semantic_role": "block",
        },
        summary="Node seen in the system block diagram",
    )

    tmp_store.upsert_node(table_node)
    tmp_store.upsert_node(figure_node)

    got = tmp_store.get_node(table_node.id)
    assert got is not None
    assert got.attrs["source"] == "table_entity:signal"
    assert got.attrs["sources"] == ["table_entity:signal", "figure@0.5"]
    assert got.attrs["source_block_ids"] == ["b-table", "b-figure"]
    assert got.attrs["source_chunk_ids"] == ["c-table", "c-figure"]
    assert got.attrs["width"] == "1"
    assert got.attrs["direction"] == "input"
    assert got.attrs["semantic_role"] == "block"
    assert got.evidence.extractor == "table_entity@0.1+figure@0.5"
    assert got.evidence.chunk_ids == ["c-table", "c-figure"]
    assert got.evidence.pages == [12, 15]
    assert set(got.aliases) == {"PCIE core", "PCIe Core"}


def test_store_edge_and_neighbors(tmp_store: SQLiteGraphStore):
    reg = Node(
        id="stm32::reg:CR1",
        kind=NodeKind.REGISTER,
        name="CR1",
        doc_id="stm32::ds",
    )
    bf = Node(
        id="stm32::bf:CR1.CEN",
        kind=NodeKind.BITFIELD,
        name="CEN",
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


def test_store_preserves_zero_confidence_and_queries_edges(tmp_store: SQLiteGraphStore):
    for node_id in ("A", "B"):
        tmp_store.upsert_node(Node(id=node_id, kind=NodeKind.MODULE, name=node_id, doc_id="doc"))
    tmp_store.upsert_edge(
        Edge(
            src="A",
            dst="B",
            kind=EdgeKind.DEPENDS_ON,
            confidence=0.0,
            evidence=Evidence(extractor="test"),
        )
    )

    edges = tmp_store.search_edges(EdgeQuery(confidence_lt=0.1))

    assert len(edges) == 1
    assert edges[0].confidence == 0.0
    tmp_store.delete_edge("A", "B", EdgeKind.DEPENDS_ON)
    assert tmp_store.search_edges(EdgeQuery()) == []


def test_store_edge_upsert_merges_sources_without_lowering_confidence(
    tmp_store: SQLiteGraphStore,
):
    for node_id in ("A", "B"):
        tmp_store.upsert_node(Node(id=node_id, kind=NodeKind.MODULE, name=node_id, doc_id="doc"))
    tmp_store.upsert_edge(
        Edge(
            src="A",
            dst="B",
            kind=EdgeKind.CONNECTS_TO,
            confidence=0.9,
            evidence=Evidence(extractor="table_entity", chunk_ids=["table-chunk"], pages=[1]),
            attrs={"source": "table_entity", "source_chunk_ids": ["table-chunk"]},
        )
    )
    tmp_store.upsert_edge(
        Edge(
            src="A",
            dst="B",
            kind=EdgeKind.CONNECTS_TO,
            confidence=0.5,
            evidence=Evidence(extractor="llm_ie", chunk_ids=["text-chunk"], pages=[2]),
            attrs={"source": "llm_ie", "source_chunk_ids": ["text-chunk"]},
        )
    )

    edge = tmp_store.get_edge("A", "B", EdgeKind.CONNECTS_TO)

    assert edge is not None
    assert edge.confidence == 0.9
    assert edge.evidence.chunk_ids == ["table-chunk", "text-chunk"]
    assert edge.evidence.pages == [1, 2]
    assert edge.attrs["source"] == "table_entity"
    assert edge.attrs["sources"] == ["table_entity", "llm_ie"]


def test_neighbors_are_deduplicated_and_do_not_reference_omitted_nodes(
    tmp_store: SQLiteGraphStore,
):
    for node_id in ("A", "B", "C"):
        tmp_store.upsert_node(Node(id=node_id, kind=NodeKind.MODULE, name=node_id, doc_id="doc"))
    for src, dst in (("A", "B"), ("B", "C")):
        tmp_store.upsert_edge(
            Edge(
                src=src,
                dst=dst,
                kind=EdgeKind.CONTAINS,
                evidence=Evidence(extractor="test"),
            )
        )

    full = tmp_store.neighbors("A", depth=2, limit=10)
    bounded = tmp_store.neighbors("A", depth=2, limit=2)

    assert [(edge.src, edge.dst) for edge in full.edges] == [("A", "B"), ("B", "C")]
    bounded_ids = {node.id for node in bounded.nodes}
    assert bounded_ids == {"A", "B"}
    assert all(edge.src in bounded_ids and edge.dst in bounded_ids for edge in bounded.edges)


def test_entity_source_lookup_uses_exact_json_ids(tmp_store: SQLiteGraphStore):
    tmp_store.upsert_node(
        Node(
            id="long-ref",
            kind=NodeKind.TERM,
            name="long-ref",
            doc_id="doc",
            evidence=Evidence(extractor="test", chunk_ids=["chunk-10"]),
            attrs={"source_block_ids": ["block-10"]},
        )
    )
    tmp_store.upsert_node(
        Node(
            id="exact-ref",
            kind=NodeKind.TERM,
            name="exact-ref",
            doc_id="doc",
            evidence=Evidence(extractor="test", chunk_ids=["chunk-1"]),
            attrs={"source_block_ids": ["block-1"]},
        )
    )

    assert [node.id for node in tmp_store.get_entities_for_chunk("chunk-1")] == ["exact-ref"]
    assert [node.id for node in tmp_store.get_entities_for_block("block-1")] == ["exact-ref"]


def test_store_search(tmp_store: SQLiteGraphStore):
    for name in ("TIM1_CR1", "TIM1_CR2", "TIM2_CR1"):
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

    parsed = ParsedDoc(doc_id="stm32::ds", source_path="x.pdf", pages=[ParsedPage(page_no=1)])
    res = TableEntityExtractor().extract(parsed, ExtractContext(family="stm32"))
    assert res.nodes == []
