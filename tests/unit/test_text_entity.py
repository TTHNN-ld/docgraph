"""TextEntityExtractor + pin/timing normalizer 测试。"""
from __future__ import annotations

from docgraph.extractors.base import ExtractContext
from docgraph.extractors.table_entity import TableEntityExtractor
from docgraph.extractors.text_entity import TextEntityExtractor
from docgraph.graph.schema import (
    Block,
    BlockKind,
    NodeKind,
    ParsedDoc,
    ParsedPage,
    TableData,
)


def _ctx():
    return ExtractContext(family="test", llm_client=None)


# ---------------------------------------------------------------------------
# TextEntityExtractor: requirement 正文抽取
# ---------------------------------------------------------------------------


def test_requirement_extraction_basic():
    text = (
        "REQ_PCIE_TRS_004: PCIE 只会在 1 个 DIE 上使能"
        "REQ_PCIE_TRS_006: PCIE 只支持 EP MODE ONLY"
    )
    doc = ParsedDoc(
        doc_id="d", source_path="x.pdf",
        pages=[ParsedPage(page_no=1, blocks=[
            Block(id="d#p1#b0", doc_id="d", page=1, kind=BlockKind.PARAGRAPH,
                  reading_order=0, text=text),
        ])],
    )
    res = TextEntityExtractor().extract(doc, _ctx())
    ids = {n.name for n in res.nodes}
    assert "REQ_PCIE_TRS_004" in ids
    assert "REQ_PCIE_TRS_006" in ids
    # 每个带 source_block_ids 回溯
    for n in res.nodes:
        assert n.attrs["source_block_ids"] == ["d#p1#b0"]
        assert n.attrs["extraction_confidence"] == "deterministic"


def test_requirement_no_match_when_no_id():
    """没有 REQ_ 编号的段落不该抽。"""
    text = "这是一个普通段落，描述一些功能，但没有任何需求编号。"
    doc = ParsedDoc(doc_id="d", source_path="x.pdf",
                   pages=[ParsedPage(page_no=1, blocks=[
                       Block(id="d#p1#b0", doc_id="d", page=1,
                             kind=BlockKind.PARAGRAPH, reading_order=0, text=text),
                   ])])
    res = TextEntityExtractor().extract(doc, _ctx())
    assert res.nodes == []


def test_errata_extraction():
    text = "Errata ERR012345: 在特定条件下 DMA 传输可能丢失数据。ERR099888 另一个勘误"
    doc = ParsedDoc(doc_id="d", source_path="x.pdf",
                   pages=[ParsedPage(page_no=1, blocks=[
                       Block(id="d#p1#b0", doc_id="d", page=1,
                             kind=BlockKind.PARAGRAPH, reading_order=0, text=text),
                   ])])
    res = TextEntityExtractor().extract(doc, _ctx())
    err_ids = {n.name for n in res.nodes if n.kind == NodeKind.ERRATA}
    assert "ERR012345" in err_ids
    assert "ERR099888" in err_ids


# ---------------------------------------------------------------------------
# Pin normalizer
# ---------------------------------------------------------------------------


def test_pin_normalizer_standard_table():
    table = TableData(
        headers=["Pin Name", "Pin No", "Direction", "Type", "Description"],
        rows=[
            ["CLK_IN", "A1", "IN", "CLK", "输入时钟"],
            ["DATA0", "B2", "IO", "DATA", "数据线"],
        ],
        n_rows=2, n_cols=5,
    )
    res = TableEntityExtractor._extract_pins_from_table(table)
    assert res is not None
    assert len(res) == 2
    assert res[0].name == "CLK_IN"
    assert res[0].direction == "IN"
    assert res[0].pin_no == "A1"
    assert res[1].name == "DATA0"


def test_pin_normalizer_rejects_non_pin_table():
    """没有 pin 关键词的表不该被 pin normalizer 处理。"""
    table = TableData(
        headers=["Signal", "Width", "Description"],
        rows=[["sig_a", "8", "信号"]],
        n_rows=1, n_cols=3,
    )
    res = TableEntityExtractor._extract_pins_from_table(table)
    assert res is None


# ---------------------------------------------------------------------------
# Timing normalizer
# ---------------------------------------------------------------------------


def test_timing_normalizer_standard_table():
    table = TableData(
        headers=["Symbol", "Min", "Typ", "Max", "Unit", "Condition"],
        rows=[
            ["t_setup", "2", "5", "10", "ns", "正常模式"],
            ["t_hold", "1", "3", "8", "ns", ""],
        ],
        n_rows=2, n_cols=6,
    )
    res = TableEntityExtractor._extract_timing_from_table(table)
    assert res is not None
    assert len(res) == 2
    assert res[0].symbol == "t_setup"
    assert res[0].min == "2"
    assert res[0].max == "10"
    assert res[0].unit == "ns"


def test_timing_normalizer_requires_value_columns():
    """只有 symbol 没有 min/typ/max 不算时序表。"""
    table = TableData(
        headers=["Symbol", "Description"],
        rows=[["t_setup", "某个参数"]],
        n_rows=1, n_cols=2,
    )
    res = TableEntityExtractor._extract_timing_from_table(table)
    assert res is None
