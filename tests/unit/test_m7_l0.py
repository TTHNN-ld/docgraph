"""M7-P1 L0 无损版面层测试。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


def test_block_table_data_models():
    from docgraph.graph.schema import Block, BlockKind, TableData

    t = TableData(headers=["A", "B"], rows=[["1", "2"]], n_rows=1, n_cols=2)
    b = Block(id="d#p1#b0", doc_id="d", page=1, kind=BlockKind.TABLE,
              reading_order=0, table=t)
    assert b.kind is BlockKind.TABLE
    assert b.table.n_rows == 1
    assert b.table.rows[0] == ["1", "2"]


def test_parsed_page_has_blocks_field():
    """ParsedPage 必须有 blocks（L0 一等公民）。"""
    from docgraph.graph.schema import ParsedPage
    p = ParsedPage(page_no=1)
    assert hasattr(p, "blocks")
    assert p.blocks == []


def test_store_blocks_roundtrip(tmp_path):
    from docgraph.graph.schema import Block, BlockKind, BBox, TableData
    from docgraph.graph.sqlite_store import SQLiteGraphStore

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    blocks = [
        Block(id="d#p1#b0", doc_id="d", page=1, kind=BlockKind.PARAGRAPH,
              reading_order=0, text="hello",
              bbox=BBox(x0=0, y0=0, x1=10, y1=10, page=1)),
        Block(id="d#p1#b1", doc_id="d", page=1, kind=BlockKind.TABLE,
              reading_order=1,
              table=TableData(headers=["A"], rows=[["x"]], n_rows=1, n_cols=1,
                              caption="Table 1")),
    ]
    store.upsert_blocks(blocks)
    assert store.count_blocks() == 2
    assert store.count_blocks(BlockKind.TABLE) == 1

    got = store.get_block("d#p1#b1")
    assert got is not None
    assert got.kind is BlockKind.TABLE
    assert got.table is not None
    assert got.table.headers == ["A"]
    assert got.table.rows == [["x"]]
    assert got.table.caption == "Table 1"

    # by page
    bs = store.blocks_for_page("d", 1)
    assert len(bs) == 2
    # batch get preserves order
    g2 = store.get_blocks(["d#p1#b1", "d#p1#b0"])
    assert g2[0].id == "d#p1#b1"
    assert g2[1].id == "d#p1#b0"
    store.close()


def test_store_delete_doc_clears_blocks(tmp_path):
    from docgraph.graph.schema import Node, NodeKind, Block, BlockKind
    from docgraph.graph.sqlite_store import SQLiteGraphStore

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    store.upsert_node(Node(id="d::reg:R", kind=NodeKind.REGISTER, name="R", doc_id="d"))
    store.upsert_blocks([Block(id="d#p1#b0", doc_id="d", page=1, kind=BlockKind.PARAGRAPH)])
    assert store.count_blocks() == 1
    store.delete_doc("d")
    assert store.count_blocks() == 0
    assert store.count_nodes() == 0
    store.close()


def test_pymupdf_extracts_real_tables():
    """PyMuPDFParser 现在产出保留单元格的 table block。"""
    import pymupdf
    from docgraph.parsers.pymupdf_parser import PyMuPDFParser
    from docgraph.parsers.base import ParseContext
    from docgraph.graph.schema import BlockKind

    with tempfile.TemporaryDirectory() as d:
        pdf = Path(d) / "t.pdf"
        doc = pymupdf.open()
        page = doc.new_page(width=600, height=300)
        # 画一张表
        for y in [60, 90, 120, 150]:
            page.draw_line((40, y), (560, y))
        for x in [40, 200, 560]:
            page.draw_line((x, 60), (x, 150))
        page.insert_text((50, 78), "Bit"); page.insert_text((210, 78), "Name")
        page.insert_text((50, 108), "0"); page.insert_text((210, 108), "EN")
        doc.save(str(pdf)); doc.close()

        parsed = PyMuPDFParser().parse(
            pdf, ParseContext(doc_id="t", cache_dir=Path(d))
        )
    table_blocks = [b for b in parsed.pages[0].blocks if b.kind == BlockKind.TABLE]
    assert len(table_blocks) >= 1
    tbl = table_blocks[0].table
    assert tbl is not None
    assert tbl.n_cols >= 1
    # 至少抓到表格的某些单元格文本
    all_cells = [c for r in tbl.rows for c in r] + tbl.headers
    assert any("EN" in c for c in all_cells)


def test_quality_skips_vlm_when_l0_has_table():
    """L0 已抽到表格 → 不再触发 register_with_table VLM 兜底。"""
    import pymupdf
    from docgraph.parsers.pymupdf_parser import PyMuPDFParser
    from docgraph.parsers.base import ParseContext

    with tempfile.TemporaryDirectory() as d:
        pdf = Path(d) / "t.pdf"
        doc = pymupdf.open()
        page = doc.new_page(width=600, height=300)
        for y in [60, 90, 120, 150]:
            page.draw_line((40, y), (560, y))
        for x in [40, 200, 560]:
            page.draw_line((x, 60), (x, 150))
        page.insert_text((50, 78), "Bit"); page.insert_text((210, 78), "Name")
        page.insert_text((50, 108), "0"); page.insert_text((210, 108), "EN")
        page.insert_text((40, 30), "Register bit assignments Table 1")
        doc.save(str(pdf)); doc.close()

        parsed = PyMuPDFParser().parse(
            pdf, ParseContext(doc_id="t", cache_dir=Path(d))
        )
    q = parsed.pages[0].quality
    assert q.register_keyword_hits >= 1
    # 因为 L0 已经抽到表格 → 不该再 register_with_table
    assert "register_with_table" not in q.vlm_reasons


def test_mineru_decorative_table_image_detection(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw

    from docgraph.parsers.mineru_parser import _is_decorative_table_image

    decorative = tmp_path / "decorative.png"
    image = Image.new("RGB", (320, 220), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 90, 320, 130), fill=(70, 190, 205))
    draw.rectangle((190, 0, 230, 220), fill=(70, 190, 205))
    for x in range(0, 321, 40):
        draw.line((x, 0, x, 220), fill=(225, 225, 225))
    for y in range(0, 221, 40):
        draw.line((0, y, 320, y), fill=(225, 225, 225))
    image.save(decorative)

    real_table = tmp_path / "real_table.png"
    image = Image.new("RGB", (320, 220), "white")
    draw = ImageDraw.Draw(image)
    for x in [20, 160, 300]:
        draw.line((x, 40, x, 180), fill="black", width=2)
    for y in [40, 80, 120, 180]:
        draw.line((20, y, 300, y), fill="black", width=2)
    draw.text((35, 55), "Bit", fill="black")
    draw.text((175, 55), "Name", fill="black")
    draw.text((35, 95), "0", fill="black")
    draw.text((175, 95), "EN", fill="black")
    image.save(real_table)

    kwargs = {
        "caption": None,
        "headers": [],
        "rows": [],
        "html": None,
        "raw_text": "",
    }
    assert _is_decorative_table_image(image_path=str(decorative), **kwargs) is True
    assert _is_decorative_table_image(image_path=str(real_table), **kwargs) is False
