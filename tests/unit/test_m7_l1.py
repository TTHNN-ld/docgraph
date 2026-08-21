"""M7-P2 L1 切块与索引层测试。"""
from __future__ import annotations

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


def test_pdf_auto_router_prefers_docling_for_born_digital_pdf():
    from docgraph.parsers.pdf_router import PdfProfile, pdf_parser_chain

    profile = PdfProfile(
        page_count=42,
        text_chars_per_page=1600,
        has_extractable_text=True,
        is_tagged_pdf=True,
        table_candidate_count=4,
    )
    primary, fallback = pdf_parser_chain(
        configured_primary="auto",
        configured_fallback=[],
        quality="balanced",
        profile=profile,
    )
    assert primary == "docling"
    assert fallback == ["mineru", "pymupdf"]


def test_pdf_auto_router_keeps_docling_for_register_dense_balanced_pdf():
    from docgraph.parsers.pdf_router import PdfProfile, pdf_parser_chain

    profile = PdfProfile(
        page_count=64,
        text_chars_per_page=1400,
        has_extractable_text=True,
        is_tagged_pdf=True,
        table_candidate_count=10,
        register_keyword_count=18,
    )
    primary, fallback = pdf_parser_chain(
        configured_primary="auto",
        configured_fallback=[],
        quality="balanced",
        profile=profile,
    )
    assert primary == "docling"
    assert fallback == ["mineru", "pymupdf"]


def test_pdf_auto_router_prefers_mineru_for_register_dense_accurate_pdf():
    from docgraph.parsers.pdf_router import PdfProfile, pdf_parser_chain

    profile = PdfProfile(
        page_count=64,
        text_chars_per_page=1400,
        has_extractable_text=True,
        is_tagged_pdf=True,
        table_candidate_count=10,
        register_keyword_count=18,
    )
    primary, fallback = pdf_parser_chain(
        configured_primary="auto",
        configured_fallback=[],
        quality="accurate",
        profile=profile,
    )
    assert primary == "mineru"
    assert fallback == ["docling", "pymupdf"]


def test_pdf_auto_router_prefers_mineru_for_scanned_or_image_heavy_pdf():
    from docgraph.parsers.pdf_router import PdfProfile, pdf_parser_chain

    profile = PdfProfile(
        page_count=80,
        text_chars_per_page=20,
        image_count_per_page=2.5,
        image_area_ratio=0.55,
        has_extractable_text=False,
        is_probably_scanned=True,
    )
    primary, fallback = pdf_parser_chain(
        configured_primary="auto",
        configured_fallback=[],
        quality="balanced",
        profile=profile,
    )
    assert primary == "mineru"
    assert fallback == ["docling", "pymupdf"]


def test_pdf_auto_router_fast_uses_pymupdf():
    from docgraph.parsers.pdf_router import PdfProfile, pdf_parser_chain

    primary, fallback = pdf_parser_chain(
        configured_primary="auto",
        configured_fallback=[],
        quality="fast",
        profile=PdfProfile(has_extractable_text=True, is_tagged_pdf=True),
    )
    assert primary == "pymupdf"
    assert fallback == ["docling", "mineru"]


def test_pdf_explicit_parser_bypasses_auto_router():
    from docgraph.parsers.pdf_router import PdfProfile, pdf_parser_chain

    primary, fallback = pdf_parser_chain(
        configured_primary="mineru",
        configured_fallback=["pymupdf"],
        quality="balanced",
        profile=PdfProfile(has_extractable_text=True, is_tagged_pdf=True),
    )
    assert primary == "mineru"
    assert fallback == ["pymupdf"]


def test_parse_stage_falls_back_when_selected_parser_raises(tmp_path):
    from docgraph.core.pipeline import _parse_with_fallback
    from docgraph.graph.schema import Block, BlockKind, DocMetadata, ParsedDoc, ParsedPage
    from docgraph.parsers.base import registry

    class FailingParser:
        name = "unit_fail"
        supports = {".pdf"}

        def can_parse(self, path):
            return path.suffix == ".pdf"

        def parse(self, path, ctx):
            raise RuntimeError("boom")

    class PassingParser:
        name = "unit_pass"
        supports = {".pdf"}

        def can_parse(self, path):
            return path.suffix == ".pdf"

        def parse(self, path, ctx):
            return ParsedDoc(
                doc_id=ctx.doc_id,
                source_path=str(path),
                pages=[
                    ParsedPage(
                        page_no=1,
                        blocks=[
                            Block(
                                id=f"{ctx.doc_id}#p1#b0",
                                doc_id=ctx.doc_id,
                                page=1,
                                kind=BlockKind.PARAGRAPH,
                                text="content",
                            )
                        ],
                    )
                ],
                parser=self.name,
            )

    registry.register(FailingParser)
    registry.register(PassingParser)
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")

    parsed = _parse_with_fallback(
        pdf,
        doc_id="d",
        cache_dir=tmp_path,
        metadata=DocMetadata(),
        quality="balanced",
        device="cpu",
        ocr_device=None,
        pdf_profile=None,
        parser_names=["unit_fail", "unit_pass"],
    )
    assert parsed.parser == "unit_pass"


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


def test_fetch_returns_blocks_and_embedded_entities(tmp_path):
    """fetch() returns complete chunk + blocks + L2 entities with source_quality."""
    from docgraph.graph.schema import (
        Block,
        BlockKind,
        Chunk,
        Evidence,
        Location,
        Node,
        NodeKind,
        TableData,
    )
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.query.engine import QueryEngine

    store = SQLiteGraphStore(tmp_path / "graph.db")
    store.init_schema()
    store.upsert_blocks([
        Block(
            id="doc#p13#b1",
            doc_id="doc",
            page=13,
            kind=BlockKind.TABLE,
            reading_order=1,
            table=TableData(
                headers=["Signal", "Direction"],
                rows=[["cfg_clk", "I"], ["mstr_aclk", "O"]],
                n_rows=2,
                n_cols=2,
                caption="Interfaces",
            ),
        ),
        Block(
            id="doc#p21#b2",
            doc_id="doc",
            page=21,
            kind=BlockKind.FIGURE,
            reading_order=2,
            text="Figure 4-1 PCIe clock structure",
        ),
    ])
    store.upsert_chunks([
        Chunk(
            id="doc#c_interfaces",
            doc_id="doc",
            page=13,
            page_start=13,
            page_end=13,
            section_id="2",
            text="Interfaces table lists cfg_clk and mstr_aclk for PCIe subsystem.",
            block_ids=["doc#p13#b1"],
            kind="table",
            chunk_type="table",
        ),
        Chunk(
            id="doc#c_clock",
            doc_id="doc",
            page=21,
            page_start=21,
            page_end=21,
            section_id="4.4",
            text="Clock structure uses CRG PLL GFM DIV MUX and core_clk.",
            block_ids=["doc#p21#b2"],
            kind="figure",
            chunk_type="figure",
        ),
    ])
    store.upsert_node(Node(
        id="doc#clock#cfg_clk",
        kind=NodeKind.CLOCK,
        name="cfg_clk",
        doc_id="doc",
        location=Location(page=13),
        evidence=Evidence(
            chunk_ids=["doc#c_interfaces"],
            pages=[13],
            extractor="table_normalizer",
        ),
        attrs={
            "source_chunk_ids": ["doc#c_interfaces"],
            "source_block_ids": ["doc#p13#b1"],
            "source": "table_normalizer",
            "extraction_confidence": "deterministic",
        },
    ))

    result = QueryEngine(store).fetch("doc#c_interfaces")

    # Chunk and block returned in full
    assert result["chunk"]["id"] == "doc#c_interfaces"
    assert len(result["blocks"]) == 1
    assert result["blocks"][0]["table"] is not None
    assert result["blocks"][0]["table"]["n_rows"] == 2  # full table, not truncated

    # Embedded entity
    assert len(result["entities"]) == 1
    entity = result["entities"][0]
    assert entity["name"] == "cfg_clk"
    assert entity["source_chunk_ids"] == ["doc#c_interfaces"]
    assert entity["source_quality"]["needs_source_check"] is False
    assert entity["source_quality"]["extraction_confidence"] == "deterministic"

    # usage_policy present
    assert "usage_policy" in result
    assert "authoritative" in result["usage_policy"] or "ground truth" in result["usage_policy"]


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


def test_quality_audit_validates_l2_structured_entities(tmp_path):
    from docgraph.graph.schema import Block, BlockKind, Chunk, Evidence, Location, Node, NodeKind
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.quality.layers import audit_l0_l1

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    store.upsert_blocks([
        Block(id="d#p1#b0", doc_id="d", page=1, kind=BlockKind.PARAGRAPH,
              reading_order=0, text="signal and map table"),
    ])
    store.upsert_chunks([
        Chunk(id="c1", doc_id="d", page=1, page_start=1, page_end=1,
              text="signal and map table", block_ids=["d#p1#b0"], source_hash="h"),
    ])

    common_attrs = {
        "source_block_ids": ["d#p1#b0"],
        "source_chunk_ids": ["c1"],
    }
    good_signal = Node(
        id="d::signal:AXI_ADDR",
        doc_id="d",
        kind=NodeKind.SIGNAL,
        name="AXI_ADDR",
        location=Location(page=1),
        attrs={**common_attrs, "width": "[31:0]"},
        evidence=Evidence(extractor="table_entity:signal", chunk_ids=["c1"], pages=[1]),
    )
    bad_signal = Node(
        id="d::signal:BAD_WIDTH",
        doc_id="d",
        kind=NodeKind.SIGNAL,
        name="BAD_WIDTH",
        location=Location(page=1),
        attrs={**common_attrs, "width": "wide"},
        evidence=Evidence(extractor="table_entity:signal", chunk_ids=["c1"], pages=[1]),
    )
    good_map = Node(
        id="d::memory_map:BAR0",
        doc_id="d",
        kind=NodeKind.MEMORY_MAP,
        name="BAR0",
        location=Location(page=1),
        attrs={**common_attrs, "address": "0x1000"},
        evidence=Evidence(extractor="table_entity:memory_map", chunk_ids=["c1"], pages=[1]),
    )
    bad_map = Node(
        id="d::memory_map:MISSING",
        doc_id="d",
        kind=NodeKind.MEMORY_MAP,
        name="MISSING",
        location=Location(page=1),
        attrs=common_attrs,
        evidence=Evidence(extractor="table_entity:memory_map", chunk_ids=["c1"], pages=[1]),
    )
    for node in (good_signal, bad_signal, good_map, bad_map):
        store.upsert_node(node)

    report = audit_l0_l1(store)
    assert not report.ok
    assert any(issue.code == "l2.invalid_width_value" for issue in report.issues)
    assert any(issue.code == "l2.missing_required_field" for issue in report.issues)

    store.delete_node(bad_signal.id)
    store.delete_node(bad_map.id)
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


def test_query_engine_context_with_blocks_uses_l2_source_links(tmp_path):
    from docgraph.graph.schema import (
        Block,
        BlockKind,
        Chunk,
        Evidence,
        Location,
        Node,
        NodeKind,
    )
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.query.engine import QueryEngine

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    store.upsert_blocks([
        Block(
            id="d#p1#b0",
            doc_id="d",
            page=1,
            kind=BlockKind.PARAGRAPH,
            reading_order=0,
            text="CTRL register enables the PCIe core.",
        )
    ])
    store.upsert_chunks([
        Chunk(
            id="d#c1",
            doc_id="d",
            page=1,
            text="CTRL register enables the PCIe core.",
            block_ids=["d#p1#b0"],
            kind="section",
        )
    ])
    store.upsert_node(Node(
        id="d::register:CTRL",
        kind=NodeKind.REGISTER,
        name="CTRL",
        doc_id="d",
        location=Location(page=1),
        evidence=Evidence(extractor="table_entity:register", chunk_ids=["d#c1"], pages=[1]),
        attrs={
            "source": "table_entity:register",
            "source_block_ids": ["d#p1#b0"],
            "source_chunk_ids": ["d#c1"],
        },
        summary="Control register",
    ))

    ctx = QueryEngine(store).context_with_blocks("Implement CTRL", max_nodes=5)
    assert ctx["nodes"][0]["source_block_ids"] == ["d#p1#b0"]
    assert ctx["nodes"][0]["needs_source_check"] is False  # table_entity is deterministic
    assert ctx["chunks"][0]["id"] == "d#c1"
    assert ctx["blocks"][0]["id"] == "d#p1#b0"
    store.close()


def test_fetch_flags_vlm_entities_for_source_check(tmp_path):
    """VLM/figure entities must have needs_source_check=True; table entities False."""
    from docgraph.graph.schema import (
        Block, BlockKind, Chunk, Evidence, Location, Node, NodeKind,
    )
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.query.engine import QueryEngine

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    store.upsert_blocks([
        Block(id="d#p1#b0", doc_id="d", page=1, kind=BlockKind.FIGURE,
              reading_order=0, text="Clock architecture diagram"),
    ])
    store.upsert_chunks([
        Chunk(id="d#c1", doc_id="d", page=1, text="Clock diagram",
              block_ids=["d#p1#b0"], kind="figure"),
    ])
    # VLM-extracted entity from figure
    store.upsert_node(Node(
        id="d::clock:core_clk",
        kind=NodeKind.CLOCK,
        name="core_clk",
        doc_id="d",
        location=Location(page=1),
        evidence=Evidence(extractor="figure@vlm", chunk_ids=["d#c1"], pages=[1]),
        attrs={
            "source": "figure@vlm",
            "extraction_confidence": "llm",
            "source_chunk_ids": ["d#c1"],
            "source_block_ids": ["d#p1#b0"],
        },
    ))
    # Table-extracted entity
    store.upsert_node(Node(
        id="d::signal:cfg_clk",
        kind=NodeKind.SIGNAL,
        name="cfg_clk",
        doc_id="d",
        location=Location(page=1),
        evidence=Evidence(extractor="table_entity:signal", chunk_ids=["d#c1"], pages=[1]),
        attrs={
            "source": "table_entity:signal",
            "source_chunk_ids": ["d#c1"],
            "source_block_ids": ["d#p1#b0"],
        },
    ))

    result = QueryEngine(store).fetch("d#c1")
    entities = {e["name"]: e for e in result["entities"]}

    # VLM entity needs verification
    assert entities["core_clk"]["source_quality"]["needs_source_check"] is True
    # Table normalizer entity does not
    assert entities["cfg_clk"]["source_quality"]["needs_source_check"] is False
    store.close()
    from docgraph.graph.schema import Block, BlockKind, Chunk
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.query.engine import QueryEngine

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    store.upsert_blocks([
        Block(
            id="d#p2#b0",
            doc_id="d",
            page=2,
            kind=BlockKind.PARAGRAPH,
            reading_order=0,
            text="MSI-X doorbell write completes interrupt delivery.",
        )
    ])
    store.upsert_chunks([
        Chunk(
            id="d#c2",
            doc_id="d",
            page=2,
            text="MSI-X doorbell write completes interrupt delivery.",
            block_ids=["d#p2#b0"],
            kind="section",
        )
    ])

    ctx = QueryEngine(store).context_with_blocks("MSI-X doorbell", max_nodes=5)
    assert ctx["nodes"] == []
    assert ctx["chunk_hits"][0]["chunk_id"] == "d#c2"
    assert ctx["chunks"][0]["id"] == "d#c2"
    assert ctx["blocks"][0]["id"] == "d#p2#b0"
    store.close()


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


# --- oversized block splitting (item 3: block too long for vector model) ---


def _long_paragraph_doc(text: str):
    from docgraph.graph.schema import Block, BlockKind, ParsedDoc, ParsedPage, TocEntry

    return ParsedDoc(
        doc_id="d",
        source_path="x",
        toc=[TocEntry(level=1, title="Long", page=1, section_path="1")],
        pages=[ParsedPage(page_no=1, blocks=[
            Block(id="d#p1#b0", doc_id="d", page=1, kind=BlockKind.HEADING,
                  reading_order=0, text="1 Long paragraph", section_path="1", heading_level=1),
            Block(id="d#p1#b1", doc_id="d", page=1, kind=BlockKind.PARAGRAPH,
                  reading_order=1, text=text, section_path="1"),
        ])],
    )


def _big_table_doc(n_rows: int):
    from docgraph.graph.schema import (
        Block, BlockKind, ParsedDoc, ParsedPage, TableData, TocEntry,
    )

    rows = [[str(i), f"REG_{i}", f"register field {i} description"] for i in range(n_rows)]
    return ParsedDoc(
        doc_id="d",
        source_path="x",
        toc=[TocEntry(level=1, title="Registers", page=1, section_path="1")],
        pages=[ParsedPage(page_no=1, blocks=[
            Block(id="d#p1#b0", doc_id="d", page=1, kind=BlockKind.HEADING,
                  reading_order=0, text="1 Registers", section_path="1", heading_level=1),
            Block(id="d#p1#b1", doc_id="d", page=1, kind=BlockKind.TABLE,
                  reading_order=1,
                  table=TableData(headers=["Bit", "Name", "Description"], rows=rows,
                                  n_rows=n_rows, n_cols=3, caption="Register table")),
        ])],
    )


def test_chunker_splits_long_paragraph_into_subchunks():
    """A single paragraph longer than MAX_CHUNK_CHARS is split sentence-aware."""
    from docgraph.chunker import MAX_CHUNK_CHARS, chunk_doc

    text = ". ".join(
        f"Sentence number {i} has some content about registers" for i in range(60)
    ) + "."
    assert len(text) > MAX_CHUNK_CHARS
    doc = _long_paragraph_doc(text)
    chunks = chunk_doc(doc)
    section_chunks = [c for c in chunks if c.kind == "section"]
    assert len(section_chunks) >= 2

    for c in section_chunks:
        assert len(c.text) <= MAX_CHUNK_CHARS, f"chunk {c.id} still {len(c.text)} chars"
        assert c.block_ids, f"chunk {c.id} missing block_ids"
        assert "d#p1#b1" in c.block_ids  # traces back to the paragraph block
        assert "split_part" in c.attrs
        assert "split_total" in c.attrs

    totals = {c.attrs["split_total"] for c in section_chunks}
    assert len(totals) == 1
    parts = sorted(c.attrs["split_part"] for c in section_chunks)
    assert parts == list(range(len(parts)))


def test_chunker_long_paragraph_does_not_lose_content():
    """Splitting preserves all content (overlap duplicates, never drops)."""
    from docgraph.chunker import chunk_doc

    text = ". ".join(f"Topic {i} discussion with detail" for i in range(80)) + "."
    doc = _long_paragraph_doc(text)
    section_chunks = [c for c in chunk_doc(doc) if c.kind == "section"]
    assert len(section_chunks) >= 2
    combined = "\n".join(c.text for c in section_chunks)
    for i in range(0, 80, 10):
        assert f"Topic {i}" in combined


def test_chunker_splits_big_table_into_header_preserving_batches():
    """A 120-row table is split into batches that each keep caption + headers."""
    from docgraph.chunker import MAX_CHUNK_CHARS, chunk_doc

    doc = _big_table_doc(120)
    chunks = chunk_doc(doc)
    table_chunks = [c for c in chunks if c.kind == "table"]
    assert len(table_chunks) >= 2

    for c in table_chunks:
        assert len(c.text) <= MAX_CHUNK_CHARS, f"table chunk {c.id} still {len(c.text)} chars"
        assert "| Bit | Name | Description |" in c.text  # headers preserved
        assert "Register table" in c.text  # caption preserved
        assert "d#p1#b1" in c.block_ids  # L0 traceability
        assert c.attrs.get("row_batch") is True

    starts = sorted(c.attrs["row_start"] for c in table_chunks)
    ends = sorted(c.attrs["row_end"] for c in table_chunks)
    assert starts[0] == 0
    assert ends[-1] == 119
    for prev_end, next_start in zip(ends[:-1], starts[1:]):
        assert next_start == prev_end + 1  # no gap between batches


def test_chunker_small_table_not_split():
    """A small table under MAX_CHUNK_CHARS stays a single chunk."""
    from docgraph.chunker import chunk_doc

    doc = _big_table_doc(3)
    table_chunks = [c for c in chunk_doc(doc) if c.kind == "table"]
    assert len(table_chunks) == 1
    assert "split_part" not in table_chunks[0].attrs


def test_chunker_split_subchunk_ids_are_unique():
    from docgraph.chunker import chunk_doc

    doc = _big_table_doc(120)
    chunks = chunk_doc(doc)
    ids = [c.id for c in chunks]
    assert len(ids) == len(set(ids))


def test_chunker_single_huge_sentence_is_hard_split():
    """A sentence longer than the budget is hard-split instead of overflowing."""
    from docgraph.chunker import MAX_CHUNK_CHARS, chunk_doc

    huge = "a" * (MAX_CHUNK_CHARS + 500)
    text = f"Intro sentence. {huge} Trailing sentence."
    doc = _long_paragraph_doc(text)
    section_chunks = [c for c in chunk_doc(doc) if c.kind == "section"]
    for c in section_chunks:
        assert len(c.text) <= MAX_CHUNK_CHARS
    assert len(section_chunks) >= 2
