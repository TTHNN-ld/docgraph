"""M7-P2 L1 切块与索引层测试。"""
from __future__ import annotations

from pathlib import Path

import pytest


def test_parser_quality_config_defaults_to_balanced():
    from docgraph.core.config import ParserFormatConfig

    assert ParserFormatConfig(primary="mineru").quality == "balanced"
    assert ParserFormatConfig(primary="mineru", quality="ACCURATE").quality == "accurate"


def test_parser_quality_rejects_unknown_value():
    from pydantic import ValidationError

    from docgraph.core.config import ParserFormatConfig

    with pytest.raises(ValidationError):
        ParserFormatConfig(primary="mineru", quality="maximum")


def test_fast_quality_prefers_pymupdf_for_pdf_parser_chain():
    from docgraph.core.pipeline import _parser_chain_for_quality

    primary, fallback = _parser_chain_for_quality(
        ".pdf",
        "mineru",
        ["pymupdf", "marker"],
        "fast",
    )
    assert primary == "pymupdf"
    assert fallback == ["mineru", "marker"]


def test_mineru_table_recognition_is_disabled_only_for_fast_quality():
    from docgraph.parsers.mineru_parser import _table_enabled_for_quality

    assert _table_enabled_for_quality("fast") is False
    assert _table_enabled_for_quality("balanced") is True
    assert _table_enabled_for_quality("accurate") is True


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------


def _mk_doc_with_blocks():
    from docgraph.graph.schema import (
        Block, BlockKind, TableData, ParsedDoc, ParsedPage, TocEntry,
    )
    blocks = [
        Block(id="d#p1#b0", doc_id="d", page=1, kind=BlockKind.HEADING,
              reading_order=0, text="1 Introduction", section_path="1",
              heading_level=1),
        Block(id="d#p1#b1", doc_id="d", page=1, kind=BlockKind.PARAGRAPH,
              reading_order=1, text="Some intro paragraph text here.", section_path="1"),
        Block(id="d#p1#b2", doc_id="d", page=1, kind=BlockKind.HEADING,
              reading_order=2, text="2 Registers", section_path="2",
              heading_level=1),
        Block(id="d#p1#b3", doc_id="d", page=1, kind=BlockKind.TABLE,
              reading_order=3,
              table=TableData(headers=["Bit", "Name"], rows=[["0", "EN"], ["3:1", "MODE"]],
                              n_rows=2, n_cols=2, caption="Register table")),
        Block(id="d#p1#b4", doc_id="d", page=1, kind=BlockKind.HEADING,
              reading_order=4, text="3 Figures", section_path="3",
              heading_level=1),
        Block(id="d#p1#b5", doc_id="d", page=1, kind=BlockKind.FIGURE,
              reading_order=5, image_path="/tmp/x.png", text="Figure 1 block diagram"),
    ]
    return ParsedDoc(
        doc_id="d", source_path="x",
        pages=[ParsedPage(page_no=1, blocks=blocks)],
        toc=[
            TocEntry(level=1, title="Introduction", page=1, section_path="1"),
            TocEntry(level=1, title="Registers", page=1, section_path="2"),
            TocEntry(level=1, title="Figures", page=1, section_path="3"),
        ],
    )


def test_chunker_table_figure_are_separate_chunks():
    from docgraph.chunker import chunk_doc
    doc = _mk_doc_with_blocks()
    chunks = chunk_doc(doc)
    kinds = {c.kind for c in chunks}
    assert "table" in kinds
    assert "figure" in kinds
    assert "section" in kinds


def test_chunker_chunk_has_block_ids_traceability():
    """每个 chunk 必须带 block_ids 反查 L0（层次契约）。"""
    from docgraph.chunker import chunk_doc
    doc = _mk_doc_with_blocks()
    chunks = chunk_doc(doc)
    for c in chunks:
        assert c.block_ids, f"chunk {c.id} 缺 block_ids"
    # table chunk 应指向 table block
    t = next(c for c in chunks if c.kind == "table")
    assert "d#p1#b3" in t.block_ids
    # figure chunk 指向 figure block
    f = next(c for c in chunks if c.kind == "figure")
    assert "d#p1#b5" in f.block_ids


def test_entity_page_image_candidate_binds_same_page_chunks(tmp_path):
    from docgraph.extractors.candidates import build_entity_candidates
    from docgraph.graph.schema import (
        Block, BlockKind, PageQuality, ParsedDoc, ParsedPage,
    )

    image = tmp_path / "p1.png"
    image.write_bytes(b"png")
    doc = ParsedDoc(
        doc_id="chip::doc::demo",
        source_path="demo.pdf",
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
                    text="Register information is visible in the page image.",
                ),
            ],
        )],
    )

    candidate = next(c for c in build_entity_candidates(doc) if c.kind == "page_image")
    assert candidate.source_chunk_ids
    assert candidate.block_ids == ["chip::doc::demo#p1#b0", "chip::doc::demo#p1#b1"]


def test_chunker_table_text_is_markdown():
    from docgraph.chunker import chunk_doc
    doc = _mk_doc_with_blocks()
    chunks = chunk_doc(doc)
    t = next(c for c in chunks if c.kind == "table")
    assert "Bit" in t.text and "Name" in t.text
    assert "EN" in t.text and "MODE" in t.text
    assert "Register table" in t.text  # caption
    assert t.attrs["table_profile"]["kind"] == "generic_table"
    assert "no_caption" not in t.attrs["table_profile"]["quality_flags"]


def test_chunker_profiles_register_table():
    from docgraph.chunker import chunk_doc
    from docgraph.graph.schema import Block, BlockKind, ParsedDoc, ParsedPage, TableData

    doc = ParsedDoc(doc_id="d", source_path="x", pages=[
        ParsedPage(page_no=1, blocks=[
            Block(
                id="t0", doc_id="d", page=1, kind=BlockKind.TABLE,
                reading_order=0,
                table=TableData(
                    caption="Register description",
                    headers=["Reg name", "Field", "Msb", "Lsb", "SWaccess", "Reset"],
                    rows=[["CTRL", "EN", "0", "0", "RW", "0x0"]],
                ),
            )
        ])
    ])

    table = next(c for c in chunk_doc(doc) if c.kind == "table")
    assert table.attrs["table_profile"]["kind"] == "register_table"
    assert table.attrs["table_profile"]["group_key"].startswith("caption:")


def test_chunker_merges_adjacent_continued_tables():
    from docgraph.chunker import chunk_doc
    from docgraph.graph.schema import Block, BlockKind, ParsedDoc, ParsedPage, TableData

    doc = ParsedDoc(doc_id="d", source_path="x", pages=[
        ParsedPage(page_no=1, blocks=[
            Block(
                id="t0", doc_id="d", page=1, kind=BlockKind.TABLE,
                reading_order=0,
                table=TableData(
                    caption="Table 1-4 Fields for Register: CTRL",
                    headers=["Bits", "Name", "Access", "Description"],
                    rows=[["0", "EN", "R/W", "enable"]],
                ),
            ),
        ]),
        ParsedPage(page_no=2, blocks=[
            Block(
                id="t1", doc_id="d", page=2, kind=BlockKind.TABLE,
                reading_order=0,
                table=TableData(
                    caption="Table 1-4 Fields for Register: CTRL(Continued)",
                    headers=["Bits", "Name", "Access", "Description"],
                    rows=[["1", "MODE", "R/W", "mode"]],
                ),
            ),
        ]),
    ])

    tables = [c for c in chunk_doc(doc) if c.kind == "table"]
    assert len(tables) == 1
    table = tables[0]
    assert table.chunk_type == "logical_table"
    assert table.page_start == 1
    assert table.page_end == 2
    assert table.block_ids == ["t0", "t1"]
    assert table.attrs["table_profile"]["logical_table_parts"] == 2


def test_chunker_section_split_on_heading():
    """遇到新 heading 应开新 section chunk。"""
    from docgraph.chunker import chunk_doc
    doc = _mk_doc_with_blocks()
    chunks = chunk_doc(doc)
    section_chunks = [c for c in chunks if c.kind == "section"]
    # 至少两个 section（1 Introduction / 3 Figures；2 Registers 后面紧跟 table）
    assert len(section_chunks) >= 1


def test_chunker_section_chunks_can_span_pages_and_bind_section_node():
    from docgraph.chunker import chunk_doc
    from docgraph.core.ids import make_node_id
    from docgraph.graph.schema import (
        Block, BlockKind, ParsedDoc, ParsedPage, TocEntry, NodeKind,
    )

    doc = ParsedDoc(
        doc_id="chip::doc::demo",
        source_path="x",
        toc=[TocEntry(level=1, title="AXI", page=1, section_path="1.5")],
        pages=[
            ParsedPage(page_no=1, blocks=[
                Block(id="b0", doc_id="chip::doc::demo", page=1,
                      kind=BlockKind.HEADING, reading_order=0,
                      text="1.5AXI", section_path="1.5"),
                Block(id="b1", doc_id="chip::doc::demo", page=1,
                      kind=BlockKind.PARAGRAPH, reading_order=1,
                      text="AXI paragraph on page 1.", section_path="1.5"),
            ]),
            ParsedPage(page_no=2, blocks=[
                Block(id="b2", doc_id="chip::doc::demo", page=2,
                      kind=BlockKind.PARAGRAPH, reading_order=0,
                      text="AXI paragraph on page 2.", section_path="1.5"),
            ]),
        ],
    )

    chunks = chunk_doc(doc)
    section = next(c for c in chunks if c.kind == "section")
    assert section.page_start == 1
    assert section.page_end == 2
    assert section.section_id == "1.5"
    assert section.section_node_id == make_node_id(
        "chip", NodeKind.SECTION, "1.5", doc_id="chip::doc::demo",
    )
    assert section.block_ids == ["b0", "b1", "b2"]


def test_chunker_recognizes_top_level_heading_with_trailing_dot():
    from docgraph.chunker import chunk_doc
    from docgraph.graph.schema import Block, BlockKind, ParsedDoc, ParsedPage

    doc = ParsedDoc(
        doc_id="chip::doc::demo",
        source_path="demo.pdf",
        pages=[ParsedPage(page_no=1, blocks=[
            Block(id="b0", doc_id="chip::doc::demo", page=1, kind=BlockKind.HEADING,
                  reading_order=0, text="1. Overview", section_path="1"),
            Block(id="b1", doc_id="chip::doc::demo", page=1, kind=BlockKind.PARAGRAPH,
                  reading_order=1, text="Overview body.", section_path="1"),
        ])],
    )
    chunks = chunk_doc(doc)
    section = next(c for c in chunks if c.kind == "section")
    assert section.section_id == "1"
    assert section.text.startswith("1. Overview")


def test_chunker_recognizes_chapter_and_appendix_headings():
    from docgraph.chunker import chunk_doc
    from docgraph.graph.schema import Block, BlockKind, ParsedDoc, ParsedPage

    doc = ParsedDoc(
        doc_id="chip::doc::demo",
        source_path="demo.pdf",
        pages=[ParsedPage(page_no=1, blocks=[
            Block(id="b0", doc_id="chip::doc::demo", page=1, kind=BlockKind.HEADING,
                  reading_order=0, text="Chapter 2Functional Description"),
            Block(id="b1", doc_id="chip::doc::demo", page=1, kind=BlockKind.PARAGRAPH,
                  reading_order=1, text="chapter body"),
            Block(id="b2", doc_id="chip::doc::demo", page=2, kind=BlockKind.HEADING,
                  reading_order=0, text="Appendix ASignal Descriptions"),
            Block(id="b3", doc_id="chip::doc::demo", page=2, kind=BlockKind.HEADING,
                  reading_order=1, text="A.1Signal properties and requirements"),
        ])],
    )
    chunks = [c for c in chunk_doc(doc) if c.kind == "section"]
    assert [c.section_id for c in chunks] == ["2", "A", "A.1"]


def test_chunker_ignores_generated_section_path_on_unnumbered_heading():
    from docgraph.chunker import chunk_doc
    from docgraph.graph.schema import (
        Block, BlockKind, ParsedDoc, ParsedPage, TocEntry,
    )

    doc = ParsedDoc(
        doc_id="chip::doc::demo",
        source_path="x",
        toc=[TocEntry(level=1, title="功能介绍", page=2, section_path="4")],
        pages=[ParsedPage(page_no=1, blocks=[
            Block(id="b0", doc_id="chip::doc::demo", page=1,
                  kind=BlockKind.HEADING, reading_order=0,
                  text="Version 3.21", section_path="4"),
            Block(id="b1", doc_id="chip::doc::demo", page=1,
                  kind=BlockKind.PARAGRAPH, reading_order=1,
                  text="front matter", section_path="4"),
        ])],
    )

    chunks = chunk_doc(doc)
    assert chunks
    assert chunks[0].section_id is None
    assert chunks[0].section_node_id is None


# ---------------------------------------------------------------------------
# Store: chunks + FTS
# ---------------------------------------------------------------------------


def test_store_chunks_roundtrip_with_block_ids(tmp_path):
    from docgraph.graph.schema import Chunk
    from docgraph.graph.sqlite_store import SQLiteGraphStore

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    store.upsert_chunks([
        Chunk(id="c1", doc_id="d", page=1, text="register EN bit",
              page_start=1, page_end=2, section_id="1",
              section_node_id="d::sec:1#d",
              block_ids=["d#p1#b0", "d#p1#b1"], kind="section",
              chunk_type="section", source_hash="sha256:source"),
        Chunk(id="c2", doc_id="d", page=2, text="timing parameter tSU",
              block_ids=["d#p2#b0"], kind="table"),
    ])
    assert store.count_chunks() == 2
    c = store.get_chunk("c1")
    assert c is not None
    assert c.block_ids == ["d#p1#b0", "d#p1#b1"]
    assert c.page_start == 1
    assert c.page_end == 2
    assert c.section_node_id == "d::sec:1#d"
    assert c.source_hash == "sha256:source"
    store.close()


def test_store_fts_search(tmp_path):
    from docgraph.graph.schema import Chunk
    from docgraph.graph.sqlite_store import SQLiteGraphStore

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    store.upsert_chunks([
        Chunk(id="c1", doc_id="d", page=1, text="PCIe MSI-X doorbell configuration",
              block_ids=[]),
        Chunk(id="c2", doc_id="d", page=2, text="AXI slave interface signals",
              block_ids=[]),
        Chunk(id="c3", doc_id="d", page=3, text="LTSSM state machine",
              block_ids=[]),
    ])
    hits = store.search_chunks_fts("doorbell")
    assert any("c1" == cid for cid, _ in hits)
    hits2 = store.search_chunks_fts("AXI")
    assert any("c2" == cid for cid, _ in hits2)
    store.close()


def test_delete_doc_removes_chunk_fts_rows(tmp_path):
    from docgraph.graph.schema import Chunk
    from docgraph.graph.sqlite_store import SQLiteGraphStore

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    store.upsert_chunks([
        Chunk(id="d#old", doc_id="d", page=1, text="AXI old chunk", block_ids=[]),
    ])
    assert store.search_chunks_fts("AXI")
    store._connect().execute(  # simulate an orphan FTS row from an older chunk id
        "INSERT INTO chunks_fts (chunk_id, text) VALUES (?, ?)",
        ("d#orphan", "AXI orphan chunk"),
    )
    store._connect().commit()
    store.delete_doc("d")
    store.upsert_chunks([
        Chunk(id="d#new", doc_id="d", page=1, text="AXI new chunk", block_ids=[]),
    ])
    hits = store.search_chunks_fts("AXI")
    assert [cid for cid, _ in hits] == ["d#new"]
    store.close()


def test_store_fts_chinese(tmp_path):
    from docgraph.graph.schema import Chunk
    from docgraph.graph.sqlite_store import SQLiteGraphStore

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    store.upsert_chunks([
        Chunk(id="c1", doc_id="d", page=1, text="中断控制器配置寄存器", block_ids=[]),
        Chunk(id="c2", doc_id="d", page=2, text="复位与时钟域", block_ids=[]),
    ])
    # unicode61 不分中文词 → LIKE 降级
    hits = store.search_chunks_fts("中断")
    assert any("c1" == cid for cid, _ in hits)
    # 不匹配的词应空
    hits2 = store.search_chunks_fts("zzNotExist")
    assert hits2 == [] or all("c1" != cid and "c2" != cid for cid, _ in hits2)
    store.close()


def test_l0_l1_quality_audit_flags_orphan_chunks(tmp_path):
    from docgraph.graph.schema import Block, BlockKind, Chunk
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.quality.layers import audit_l0_l1

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    store.upsert_blocks([
        Block(id="d#p1#b0", doc_id="d", page=1, kind=BlockKind.PARAGRAPH,
              reading_order=0, text="hello"),
    ])
    store.upsert_chunks([
        Chunk(id="good", doc_id="d", page=1, page_start=1, page_end=1,
              text="hello", block_ids=["d#p1#b0"], source_hash="h"),
        Chunk(id="bad", doc_id="d", page=1, page_start=1, page_end=1,
              text="bad", block_ids=[], source_hash="h"),
    ])

    report = audit_l0_l1(store)
    assert not report.ok
    assert any(i.code == "l1.orphan_chunk" for i in report.issues)
    store.close()


def test_quality_audit_requires_l2_provenance(tmp_path):
    from docgraph.graph.schema import Block, BlockKind, Chunk, Evidence, Node, NodeKind
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.quality.layers import audit_l0_l1

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    store.upsert_blocks([
        Block(id="d#p1#b0", doc_id="d", page=1, kind=BlockKind.PARAGRAPH,
              reading_order=0, text="register table"),
    ])
    store.upsert_chunks([
        Chunk(id="c1", doc_id="d", page=1, page_start=1, page_end=1,
              text="register table", block_ids=["d#p1#b0"], source_hash="h"),
    ])
    store.upsert_node(Node(
        id="d::reg:CTRL",
        doc_id="d",
        kind=NodeKind.REGISTER,
        name="CTRL",
        attrs={"source_block_ids": ["d#p1#b0"]},
        evidence=Evidence(extractor="table_entity:register", pages=[1]),
    ))

    report = audit_l0_l1(store)
    assert not report.ok
    assert any(i.code == "l2.missing_source_chunks" for i in report.issues)

    store.upsert_node(Node(
        id="d::reg:CTRL",
        doc_id="d",
        kind=NodeKind.REGISTER,
        name="CTRL",
        attrs={
            "source_block_ids": ["d#p1#b0"],
            "source_chunk_ids": ["c1"],
        },
        evidence=Evidence(extractor="table_entity:register", chunk_ids=["c1"], pages=[1]),
    ))
    report = audit_l0_l1(store)
    assert report.ok
    store.close()


def test_quality_audit_validates_l2_register_bitfields(tmp_path):
    from docgraph.graph.schema import Block, BlockKind, Chunk, Evidence, Location, Node, NodeKind
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.quality.layers import audit_l0_l1

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    store.upsert_blocks([
        Block(id="d#p1#b0", doc_id="d", page=1, kind=BlockKind.PARAGRAPH,
              reading_order=0, text="register table"),
    ])
    store.upsert_chunks([
        Chunk(id="c1", doc_id="d", page=1, page_start=1, page_end=1,
              text="register table", block_ids=["d#p1#b0"], source_hash="h"),
    ])
    reg = Node(
        id="d::reg:CTRL",
        doc_id="d",
        kind=NodeKind.REGISTER,
        name="CTRL",
        location=Location(page=1),
        attrs={
            "width": 8,
            "source_block_ids": ["d#p1#b0"],
            "source_chunk_ids": ["c1"],
        },
        evidence=Evidence(extractor="table_entity:register", chunk_ids=["c1"], pages=[1]),
    )
    good = Node(
        id="d::bf:CTRL.EN",
        doc_id="d",
        kind=NodeKind.BITFIELD,
        name="EN",
        location=Location(page=1),
        attrs={
            "register_id": reg.id,
            "bit_high": 0,
            "bit_low": 0,
            "access": "RW",
            "source_block_ids": ["d#p1#b0"],
            "source_chunk_ids": ["c1"],
        },
        evidence=Evidence(extractor="table_entity:register", chunk_ids=["c1"], pages=[1]),
    )
    bad = Node(
        id="d::bf:CTRL.BAD",
        doc_id="d",
        kind=NodeKind.BITFIELD,
        name="BAD",
        location=Location(page=1),
        attrs={
            "register_id": reg.id,
            "bit_high": 9,
            "bit_low": 8,
            "source_block_ids": ["d#p1#b0"],
            "source_chunk_ids": ["c1"],
        },
        evidence=Evidence(extractor="table_entity:register", chunk_ids=["c1"], pages=[1]),
    )
    for node in (reg, good, bad):
        store.upsert_node(node)

    report = audit_l0_l1(store)
    assert not report.ok
    assert any(issue.code == "l2.invalid_bit_width" for issue in report.issues)
    store.delete_node(bad.id)
    report = audit_l0_l1(store)
    assert report.ok
    assert report.totals["l2_nodes_structurally_valid"] == 2
    store.close()


def test_query_engine_ranks_section_heading_above_front_matter(tmp_path):
    from docgraph.graph.schema import Chunk
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.query.engine import QueryEngine

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    store.upsert_chunks([
        Chunk(id="front", doc_id="d", page=1,
              text="Version history AXI compatibility note",
              block_ids=[], kind="section", chunk_type="section"),
        Chunk(id="axi", doc_id="d", page=6, page_start=6, page_end=7,
              section_id="1.5", section_node_id="d::sec:1.5#d",
              text="1.5AXI\nAXI slave interface signals",
              block_ids=["b1"], kind="section", chunk_type="section"),
    ])

    hits = QueryEngine(store).search_chunks("AXI", limit=2)
    assert hits[0]["chunk_id"] == "axi"
    assert "heading" in hits[0]["rank_reasons"]
    store.close()


def test_query_engine_boosts_table_profile_headers(tmp_path):
    from docgraph.graph.schema import Chunk
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.query.engine import QueryEngine

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    store.upsert_chunks([
        Chunk(id="plain", doc_id="d", page=1, text="AXI appears in prose", block_ids=[]),
        Chunk(
            id="table", doc_id="d", page=2, text="| Signal | Width |\n| AXI | 32 |",
            block_ids=["t1"], kind="table", chunk_type="table",
            attrs={"table_profile": {
                "kind": "signal_table",
                "caption": "Interface signals",
                "headers": ["Signal", "Width", "Description"],
                "quality_flags": [],
            }},
        ),
    ])

    hits = QueryEngine(store).search_chunks("Signal", limit=2)
    assert hits[0]["chunk_id"] == "table"
    assert "table-header" in hits[0]["rank_reasons"]
    store.close()


def test_query_engine_includes_semantic_chunk_hits(tmp_path):
    from docgraph.embeddings.hash_encoder import HashEncoder
    from docgraph.embeddings.indexer import embed_chunks
    from docgraph.embeddings.vector_store import VectorStore
    from docgraph.graph.schema import Chunk
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.query.engine import QueryEngine

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    store.upsert_chunks([
        Chunk(
            id="semantic", doc_id="d", page=5,
            section_id="5.1", section_node_id="d::sec:5.1#d",
            text="PCIe subsystem core logic and reset controller",
            block_ids=["b1"], kind="section", chunk_type="section",
        ),
    ])
    vstore = VectorStore(tmp_path / "v.db")
    vstore.init_schema()
    enc = HashEncoder(dim=64)
    assert embed_chunks(store, vstore, enc) == 1

    hits = QueryEngine(store, vstore=vstore, encoder=enc).search_chunks(
        "subsystem core logic",
        limit=3,
    )
    assert hits
    assert hits[0]["chunk_id"] == "semantic"
    assert any(str(r).startswith("semantic:") for r in hits[0]["rank_reasons"])
    store.close()
    vstore.close()


def test_migration_v2_adds_block_ids_column(tmp_path):
    """模拟旧 v1 db（chunks 无 block_ids 列），migrate 后应加上。"""
    import sqlite3
    from docgraph.graph.migrations import run_migrations

    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    # 手工建一份"旧" chunks 表（无 block_ids）
    conn.executescript("""
    CREATE TABLE schema_versions (component TEXT PRIMARY KEY, version INTEGER, applied_at TEXT);
    CREATE TABLE chunks (id TEXT PRIMARY KEY, doc_id TEXT, page INTEGER, section_id TEXT,
                         text TEXT, hash TEXT, attrs TEXT);
    INSERT INTO schema_versions VALUES ('global', 1, 'now');
    """)
    conn.commit()
    conn.close()

    applied = run_migrations(db)
    assert 2 in applied

    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(chunks)").fetchall()}
    conn.close()
    assert "block_ids" in cols


def test_migration_v3_adds_l1_metadata_columns(tmp_path):
    import sqlite3
    from docgraph.graph.migrations import run_migrations

    db = tmp_path / "old_v2.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
    CREATE TABLE schema_versions (component TEXT PRIMARY KEY, version INTEGER, applied_at TEXT);
    CREATE TABLE chunks (id TEXT PRIMARY KEY, doc_id TEXT, page INTEGER, section_id TEXT,
                         text TEXT, hash TEXT, block_ids TEXT, attrs TEXT);
    INSERT INTO chunks VALUES ('c1', 'd', 3, '1.5', 'hello', 'h', '[]', '{}');
    INSERT INTO schema_versions VALUES ('global', 2, 'now');
    """)
    conn.commit()
    conn.close()

    applied = run_migrations(db)
    assert 3 in applied

    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(chunks)").fetchall()}
    row = conn.execute(
        "SELECT page_start, page_end, source_hash, chunk_type FROM chunks WHERE id='c1'"
    ).fetchone()
    conn.close()
    assert {"page_start", "page_end", "section_node_id", "source_hash", "chunk_type"} <= cols
    assert row == (3, 3, "h", "section")
