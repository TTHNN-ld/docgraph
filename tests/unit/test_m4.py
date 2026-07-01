"""M4 测试：register 改进 + VLM 通用化 + embedding factory + 导出。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Embedding factory
# ---------------------------------------------------------------------------


def test_embedding_factory_hash():
    from docgraph.core.config import EmbeddingsConfig
    from docgraph.embeddings.factory import build_encoder
    from docgraph.embeddings.hash_encoder import HashEncoder

    enc = build_encoder(EmbeddingsConfig(provider="hash", dim=128))
    assert isinstance(enc, HashEncoder)
    assert enc.dim == 128


def test_embedding_factory_openai_fallback(monkeypatch):
    """OpenAI 没 API key → fallback 到 hash。"""
    from docgraph.core.config import EmbeddingsConfig
    from docgraph.embeddings.factory import build_encoder
    from docgraph.embeddings.hash_encoder import HashEncoder

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    enc = build_encoder(EmbeddingsConfig(provider="openai_compat"))
    assert isinstance(enc, HashEncoder)


# ---------------------------------------------------------------------------
# VLM provider factory
# ---------------------------------------------------------------------------


def test_vlm_provider_factory(monkeypatch):
    from docgraph.llm.vlm import (
        AnthropicVLMProvider,
        OpenAICompatVLMProvider,
        make_vlm_provider,
    )

    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    p = make_vlm_provider("openai_compat",
                          api_key_env="OPENAI_API_KEY",
                          base_url_env="OPENAI_BASE_URL")
    assert isinstance(p, OpenAICompatVLMProvider)
    p2 = make_vlm_provider("anthropic")
    assert isinstance(p2, AnthropicVLMProvider)
    with pytest.raises(ValueError):
        make_vlm_provider("does-not-exist")


# ---------------------------------------------------------------------------
# Register extractor: new candidate sources
# ---------------------------------------------------------------------------


def test_table_entity_table_matches_register():
    """TableEntityExtractor._table_matches 应匹配 register 表头。"""
    from docgraph.extractors.table_entity import TableEntityExtractor
    from docgraph.extractors.schema_registry import get_schema
    from docgraph.graph.schema import TableData

    schema = get_schema("register")
    assert schema is not None
    ex = TableEntityExtractor()
    tbl = TableData(headers=["Bits", "Name", "Access", "Description"], rows=[])
    assert ex._table_matches(tbl, schema)
    tbl2 = TableData(headers=["Version", "Date"], rows=[])
    assert not ex._table_matches(tbl2, schema)


def test_table_entity_table_matches_pin():
    """TableEntityExtractor._table_matches 应匹配 pin 表头。"""
    from docgraph.extractors.table_entity import TableEntityExtractor
    from docgraph.extractors.schema_registry import get_schema
    from docgraph.graph.schema import TableData

    schema = get_schema("pin")
    assert schema is not None
    ex = TableEntityExtractor()
    tbl = TableData(headers=["Pin", "Direction", "Function"], rows=[])
    assert ex._table_matches(tbl, schema)


def test_table_entity_register_uses_l1_candidate_provenance():
    from docgraph.extractors.base import ExtractContext
    from docgraph.extractors.schema_registry import RegisterDefList
    from docgraph.extractors.table_entity import TableEntityExtractor
    from docgraph.graph.schema import (
        BitFieldDef,
        Block,
        BlockKind,
        DocMetadata,
        DocType,
        NodeKind,
        ParsedDoc,
        ParsedPage,
        RegisterDef,
        TableData,
    )

    class FakeLLM:
        def json(self, *args, **kwargs):
            return RegisterDefList(registers=[
                RegisterDef(
                    name="CTRL",
                    offset="0x00",
                    access="RW",
                    bitfields=[
                        BitFieldDef(
                            name="EN",
                            bit_high=0,
                            bit_low=0,
                            access="RW",
                            description="enable",
                        )
                    ],
                )
            ])

    doc = ParsedDoc(
        doc_id="chip::doc::demo",
        source_path="demo.pdf",
        metadata=DocMetadata(type=DocType.DATASHEET, family="chip"),
        pages=[ParsedPage(page_no=1, blocks=[
            Block(
                id="chip::doc::demo#p1#b0",
                doc_id="chip::doc::demo",
                page=1,
                kind=BlockKind.TABLE,
                reading_order=0,
                table=TableData(
                    caption="Register table",
                    headers=["Bits", "Name", "Access", "Description"],
                    rows=[["0", "EN", "RW", "enable"]],
                ),
                attrs={"table_source": "cells"},
            )
        ])],
    )

    result = TableEntityExtractor(schema_names=["register"]).extract(
        doc,
        ExtractContext(family="chip", llm_client=FakeLLM()),
    )

    register = next(n for n in result.nodes if n.kind == NodeKind.REGISTER)
    bitfield = next(n for n in result.nodes if n.kind == NodeKind.BITFIELD)
    assert register.attrs["source_block_ids"] == ["chip::doc::demo#p1#b0"]
    assert register.attrs["source_chunk_ids"]
    assert register.attrs["candidate_id"].endswith("#candidate_table")
    assert bitfield.attrs["source_chunk_ids"] == register.attrs["source_chunk_ids"]


def test_table_entity_register_drops_overlapping_bitfields():
    from docgraph.extractors.base import ExtractContext
    from docgraph.extractors.schema_registry import RegisterDefList
    from docgraph.extractors.table_entity import TableEntityExtractor
    from docgraph.graph.schema import (
        BitFieldDef,
        Block,
        BlockKind,
        DocMetadata,
        DocType,
        NodeKind,
        ParsedDoc,
        ParsedPage,
        RegisterDef,
        TableData,
    )

    class FakeLLM:
        def json(self, *args, **kwargs):
            return RegisterDefList(registers=[
                RegisterDef(
                    name="CTRL",
                    offset="0x00",
                    access="RW",
                    width=16,
                    bitfields=[
                        BitFieldDef(name="WHOLE", bit_high=15, bit_low=0),
                        BitFieldDef(name="LOW", bit_high=7, bit_low=0),
                        BitFieldDef(name="HIGH", bit_high=15, bit_low=8),
                    ],
                )
            ])

    doc = ParsedDoc(
        doc_id="chip::doc::demo",
        source_path="demo.pdf",
        metadata=DocMetadata(type=DocType.DATASHEET, family="chip"),
        pages=[ParsedPage(page_no=1, blocks=[
            Block(
                id="chip::doc::demo#p1#b0",
                doc_id="chip::doc::demo",
                page=1,
                kind=BlockKind.TABLE,
                reading_order=0,
                table=TableData(
                    caption="Register table",
                    headers=["Bits", "Name", "Access", "Description"],
                    rows=[["15:0", "WHOLE", "RW", "merged row"]],
                ),
            )
        ])],
    )

    result = TableEntityExtractor(schema_names=["register"]).extract(
        doc,
        ExtractContext(family="chip", llm_client=FakeLLM()),
    )

    register = next(n for n in result.nodes if n.kind == NodeKind.REGISTER)
    bitfields = [n for n in result.nodes if n.kind == NodeKind.BITFIELD]
    assert [n.name for n in bitfields] == ["LOW", "HIGH"]
    assert register.attrs["bitfield_ids"] == [n.id for n in bitfields]
    assert register.attrs["dropped_bitfields"] == [{
        "name": "WHOLE",
        "bit_high": 15,
        "bit_low": 0,
        "reason": "overlap",
    }]


def test_table_entity_page_vlm_uses_l1_candidate_provenance(tmp_path, monkeypatch):
    from docgraph.extractors.base import ExtractContext
    from docgraph.extractors.schema_registry import RegisterDefList
    from docgraph.extractors.table_entity import TableEntityExtractor
    from docgraph.graph.schema import (
        Block,
        BlockKind,
        DocMetadata,
        DocType,
        NodeKind,
        PageQuality,
        ParsedDoc,
        ParsedPage,
        RegisterDef,
    )

    class FakeLLM:
        pass

    class FakeVLM:
        pass

    def fake_vlm_extract(**kwargs):
        return RegisterDefList(registers=[
            RegisterDef(name="IMG_CTRL", offset="0x10", access="RW")
        ])

    monkeypatch.setattr("docgraph.extractors.table_entity.vlm_extract", fake_vlm_extract)
    monkeypatch.setenv("DOCGRAPH_VLM_PAGE_LIMIT", "2")

    image = tmp_path / "page.png"
    image.write_bytes(b"png")
    doc = ParsedDoc(
        doc_id="chip::doc::demo",
        source_path="demo.pdf",
        metadata=DocMetadata(type=DocType.DATASHEET, family="chip"),
        pages=[ParsedPage(
            page_no=1,
            rendered_image_path=str(image),
            quality=PageQuality(needs_vlm=True, vlm_reasons=["register_with_table"]),
            blocks=[
                Block(
                    id="chip::doc::demo#p1#b0",
                    doc_id="chip::doc::demo",
                    page=1,
                    kind=BlockKind.HEADING,
                    reading_order=0,
                    text="1 Registers",
                ),
                Block(
                    id="chip::doc::demo#p1#b1",
                    doc_id="chip::doc::demo",
                    page=1,
                    kind=BlockKind.PARAGRAPH,
                    reading_order=1,
                    text="This page contains a rendered register table image.",
                ),
            ],
        )],
    )

    result = TableEntityExtractor(schema_names=["register"]).extract(
        doc,
        ExtractContext(
            family="chip",
            llm_client=FakeLLM(),
            options={"vlm_client": FakeVLM()},
        ),
    )

    register = next(n for n in result.nodes if n.kind == NodeKind.REGISTER)
    assert register.name == "IMG_CTRL"
    assert register.attrs["source_block_ids"] == [
        "chip::doc::demo#p1#b0",
        "chip::doc::demo#p1#b1",
    ]
    assert register.attrs["source_chunk_ids"]
    assert register.attrs["candidate_id"].endswith("#candidate_page_image_p1")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_store_with_register():
    from docgraph.graph.schema import (
        Edge, EdgeKind, Evidence, Node, NodeKind, Location,
    )
    from docgraph.graph.sqlite_store import SQLiteGraphStore

    with tempfile.TemporaryDirectory() as d:
        store = SQLiteGraphStore(Path(d) / "g.db")
        store.init_schema()
        reg = Node(
            id="t::reg:FOO_CTRL", kind=NodeKind.REGISTER,
            name="FOO_CTRL", qualified_name="FOO_CTRL",
            doc_id="t::d", location=Location(page=1),
            summary="FOO control register.",
            attrs={"address": "0x40000000", "offset": "0x00",
                   "width": 32, "access": "RW", "reset_value": "0x0"},
        )
        bf0 = Node(
            id="t::bf:FOO_CTRL.EN", kind=NodeKind.BITFIELD,
            name="EN", qualified_name="FOO_CTRL.EN",
            doc_id="t::d",
            attrs={"bit_high": 0, "bit_low": 0, "access": "RW",
                   "reset": "0", "description": "Enable bit."},
        )
        bf1 = Node(
            id="t::bf:FOO_CTRL.MODE", kind=NodeKind.BITFIELD,
            name="MODE", qualified_name="FOO_CTRL.MODE",
            doc_id="t::d",
            attrs={"bit_high": 3, "bit_low": 1, "access": "RW",
                   "reset": "0b000", "description": "Mode select."},
        )
        for n in (reg, bf0, bf1):
            store.upsert_node(n)
        for bf in (bf0, bf1):
            store.upsert_edge(Edge(
                src=reg.id, dst=bf.id, kind=EdgeKind.HAS_BITFIELD,
                confidence=1.0, evidence=Evidence(extractor="test"),
            ))
        yield store
        store.close()


def test_export_ipxact(tmp_store_with_register, tmp_path):
    from docgraph.export import export_ipxact

    out = tmp_path / "out.xml"
    r = export_ipxact(tmp_store_with_register, out, family="t", component="cmp")
    assert out.is_file()
    content = out.read_text("utf-8")
    assert "<ipxact:component" in content
    assert "<ipxact:name>FOO_CTRL</ipxact:name>" in content
    assert "<ipxact:bitOffset>1</ipxact:bitOffset>" in content
    assert "<ipxact:bitWidth>3</ipxact:bitWidth>" in content
    assert r.registers == 1 and r.bitfields == 2


def test_export_systemrdl(tmp_store_with_register, tmp_path):
    from docgraph.export import export_systemrdl

    out = tmp_path / "out.rdl"
    r = export_systemrdl(tmp_store_with_register, out, family="t", component="cmp")
    assert out.is_file()
    content = out.read_text("utf-8")
    assert "addrmap cmp_top" in content
    assert "reg {" in content
    assert "FOO_CTRL" in content
    assert "EN[0:0]" in content
    assert "MODE[3:1]" in content
    assert r.registers == 1 and r.bitfields == 2


def test_export_filtered_to_one_register(tmp_store_with_register, tmp_path):
    from docgraph.export import export_ipxact

    r = export_ipxact(
        tmp_store_with_register, tmp_path / "one.xml",
        family="t", register_name="FOO_CTRL",
    )
    assert r.registers == 1


# ---------------------------------------------------------------------------
# Review TUI helpers
# ---------------------------------------------------------------------------


def test_review_gather_low_confidence():
    from docgraph.graph.schema import (
        Edge, EdgeKind, Evidence, Node, NodeKind,
    )
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.review import gather_low_confidence

    with tempfile.TemporaryDirectory() as d:
        store = SQLiteGraphStore(Path(d) / "g.db")
        store.init_schema()
        a = Node(id="A", kind=NodeKind.REGISTER, name="A", doc_id="d")
        b = Node(id="B", kind=NodeKind.BITFIELD, name="B", doc_id="d")
        c = Node(id="C", kind=NodeKind.SECTION, name="C", doc_id="d")
        for n in (a, b, c):
            store.upsert_node(n)
        # 一条高置信，一条低置信
        store.upsert_edge(Edge(src="A", dst="B", kind=EdgeKind.HAS_BITFIELD,
                               confidence=0.95, evidence=Evidence(extractor="t")))
        store.upsert_edge(Edge(src="C", dst="A", kind=EdgeKind.REFERENCES,
                               confidence=0.5, evidence=Evidence(extractor="t")))
        items = gather_low_confidence(store, min_confidence=0.85)
        assert len(items) == 1
        assert items[0].confidence == 0.5
        store.close()


# ---------------------------------------------------------------------------
# Marker / MinerU parsers: lazy import + can_parse
# ---------------------------------------------------------------------------


def test_marker_parser_basics():
    from docgraph.parsers.marker_parser import MarkerParser
    p = MarkerParser()
    assert p.name == "marker"
    assert p.can_parse(Path("x.pdf"))
    assert not p.can_parse(Path("x.docx"))


def test_mineru_parser_basics():
    from docgraph.parsers.mineru_parser import MinerUParser
    p = MinerUParser()
    assert p.name == "mineru"
    assert p.can_parse(Path("x.pdf"))
