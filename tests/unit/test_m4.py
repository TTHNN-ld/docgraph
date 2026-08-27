"""M4 测试：register 改进 + VLM 通用化 + embedding factory + 导出。"""
from __future__ import annotations

import tempfile
from importlib.util import find_spec
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


def test_embedding_factory_openai_requires_configured_key(monkeypatch):
    """A configured provider must not silently change retrieval semantics."""
    from docgraph.core.config import EmbeddingsConfig
    from docgraph.embeddings.factory import build_encoder

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="requires an API key"):
        build_encoder(EmbeddingsConfig(provider="openai_compat"))


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


def test_table_entity_table_matches_backend_constraints():
    from docgraph.extractors.table_entity import TableEntityExtractor
    from docgraph.extractors.schema_registry import get_schema
    from docgraph.graph.schema import TableData

    ex = TableEntityExtractor()
    timing_schema = get_schema("constraint")
    physical_schema = get_schema("physical_constraint")
    assert timing_schema is not None
    assert physical_schema is not None

    sdc = TableData(
        caption="STA constraint summary",
        headers=["Constraint", "Target", "Value", "Unit", "Corner"],
        rows=[],
    )
    floorplan = TableData(
        caption="Floorplan constraints",
        headers=["Rule", "Object", "Layer", "Region", "Spacing"],
        rows=[],
    )
    register_like = TableData(
        headers=["Register", "Bits", "Reset", "Description"],
        rows=[],
    )

    assert ex._table_matches(sdc, timing_schema)
    assert ex._table_matches(floorplan, physical_schema)
    assert not ex._table_matches(register_like, timing_schema)
    assert not ex._table_matches(register_like, physical_schema)


def test_table_entity_rejects_interrupt_feature_summary_tables():
    from docgraph.extractors.table_entity import TableEntityExtractor
    from docgraph.extractors.schema_registry import get_schema
    from docgraph.graph.schema import TableData

    schema = get_schema("interrupt")
    assert schema is not None
    ex = TableEntityExtractor()

    priority_summary = TableData(
        headers=["Interrupt priority levels", "8 to 256 priority levels"],
        rows=[
            ["Wake-up interrupt controller", "Optional"],
            ["Sleep modes", "Integrated WFI and WFE Instructions"],
            ["Debug", "Optional JTAG and serial wire debug ports"],
        ],
    )
    nvic_block_summary = TableData(
        headers=["Nested vectored interrupt controller", "Nested vectored interrupt controller", ""],
        rows=[
            ["CPU Armv6-M", "", ""],
            ["Memory protection unit", "", ""],
            ["Fast I/O port", "", "Serial wire"],
        ],
    )
    irq_source_list = TableData(
        caption="Interrupt source list",
        headers=["type", "irq_src信号", "位宽", "Description"],
        rows=[["function", "radm_cpl_timeout", "1", "completion timeout"]],
    )

    assert not ex._table_matches(priority_summary, schema)
    assert not ex._table_matches(nvic_block_summary, schema)
    assert ex._table_matches(irq_source_list, schema)


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


def test_table_entity_deterministically_extracts_register_field_table_without_llm():
    from docgraph.extractors.base import ExtractContext
    from docgraph.extractors.table_entity import TableEntityExtractor
    from docgraph.graph.schema import (
        Block,
        BlockKind,
        NodeKind,
        ParsedDoc,
        ParsedPage,
        TableData,
    )

    doc = ParsedDoc(
        doc_id="chip::doc::demo",
        source_path="demo.pdf",
        pages=[ParsedPage(page_no=1, blocks=[
            Block(
                id="chip::doc::demo#p1#b0",
                doc_id="chip::doc::demo",
                page=1,
                kind=BlockKind.TABLE,
                reading_order=0,
                table=TableData(
                    headers=[
                        "Reg name", "reg_num", "Field", "Msb", "Lsb",
                        "SWaccess", "HWaccess", "Default", "Description",
                    ],
                    rows=[
                        ["CTRL", "1", "EN", "0", "0", "RW", "RO", "0x0", "enable"],
                        ["CTRL", "1", "MODE", "3", "1", "RW", "RO", "0x0", "mode"],
                    ],
                ),
            )
        ])],
    )

    result = TableEntityExtractor(schema_names=["register"]).extract(
        doc,
        ExtractContext(family="chip"),
    )

    assert result.stats.llm_calls == 0
    register = next(n for n in result.nodes if n.kind == NodeKind.REGISTER)
    bitfields = [n for n in result.nodes if n.kind == NodeKind.BITFIELD]
    assert register.name == "CTRL"
    assert [n.name for n in bitfields] == ["EN", "MODE"]
    assert [(bf.attrs["bit_high"], bf.attrs["bit_low"]) for bf in bitfields] == [(0, 0), (3, 1)]
    assert all(bf.attrs["source_block_ids"] == ["chip::doc::demo#p1#b0"] for bf in bitfields)


def test_table_entity_recovers_shifted_register_name_in_field_table():
    from docgraph.extractors.base import ExtractContext
    from docgraph.extractors.table_entity import TableEntityExtractor
    from docgraph.graph.schema import Block, BlockKind, NodeKind, ParsedDoc, ParsedPage, TableData

    doc = ParsedDoc(
        doc_id="chip::doc::demo",
        source_path="demo.pdf",
        pages=[ParsedPage(page_no=1, blocks=[
            Block(
                id="chip::doc::demo#p1#b0",
                doc_id="chip::doc::demo",
                page=1,
                kind=BlockKind.TABLE,
                table=TableData(
                    headers=[
                        "Reg name", "reg_num", "Field", "Msb", "Lsb",
                        "SWaccess", "HWaccess", "Default", "Description",
                    ],
                    rows=[
                        ["cfg_dbg_sel_sig0", "1", "bit_0", "7", "0", "RW", "RO", "0x0", "PAD0"],
                        ["cfg_dbg_sel_sig0", "1", "bit_1", "15", "8", "RW", "RO", "0x0", "PAD1"],
                        ["cfg_dbg_sel_sig0", "cfg_dbg_sel_sig1 1", "bit_4", "", "0", "RW", "RO", "0x0", "PAD4"],
                        ["1", "", "bit_5", "15", "RW", "", "RO", "0x0", "PAD5"],
                    ],
                ),
            )
        ])],
    )

    result = TableEntityExtractor(schema_names=["register"]).extract(
        doc,
        ExtractContext(family="chip"),
    )

    registers = {n.name: n for n in result.nodes if n.kind == NodeKind.REGISTER}
    assert set(registers) == {"cfg_dbg_sel_sig0", "cfg_dbg_sel_sig1"}
    sig1_fields = [
        n for n in result.nodes
        if n.kind == NodeKind.BITFIELD and n.attrs["register_id"] == registers["cfg_dbg_sel_sig1"].id
    ]
    assert [n.name for n in sig1_fields] == ["bit_4", "bit_5"]
    assert [(n.attrs["bit_high"], n.attrs["bit_low"]) for n in sig1_fields] == [(7, 0), (15, 8)]


def test_table_entity_deterministically_extracts_memory_map_without_llm():
    from docgraph.extractors.base import ExtractContext
    from docgraph.extractors.table_entity import TableEntityExtractor
    from docgraph.graph.schema import Block, BlockKind, NodeKind, ParsedDoc, ParsedPage, TableData

    doc = ParsedDoc(
        doc_id="chip::doc::demo",
        source_path="demo.pdf",
        pages=[ParsedPage(page_no=1, blocks=[
            Block(
                id="chip::doc::demo#p1#b0",
                doc_id="chip::doc::demo",
                page=1,
                kind=BlockKind.TABLE,
                table=TableData(
                    headers=["NoC Master", "NoC Slave", "Offset", "Size", "Description"],
                    rows=[
                        ["noc", "Top CFG", "0x00000000", "1MB", "top registers"],
                        ["noc", "CRG CFG", "0x00100000", "1MB", "clock registers"],
                    ],
                ),
            )
        ])],
    )

    result = TableEntityExtractor(schema_names=["memory_map"]).extract(
        doc,
        ExtractContext(family="chip"),
    )

    maps = [n for n in result.nodes if n.kind == NodeKind.MEMORY_MAP]
    assert [n.name for n in maps] == ["Top CFG", "CRG CFG"]
    assert [n.attrs["address"] for n in maps] == ["0x00000000", "0x00100000"]
    assert all(n.attrs["source_block_ids"] == ["chip::doc::demo#p1#b0"] for n in maps)


def test_table_entity_deterministically_extracts_interrupts_without_llm():
    from docgraph.extractors.base import ExtractContext
    from docgraph.extractors.table_entity import TableEntityExtractor
    from docgraph.graph.schema import Block, BlockKind, NodeKind, ParsedDoc, ParsedPage, TableData

    doc = ParsedDoc(
        doc_id="chip::doc::demo",
        source_path="demo.pdf",
        pages=[ParsedPage(page_no=1, blocks=[
            Block(
                id="chip::doc::demo#p1#b0",
                doc_id="chip::doc::demo",
                page=1,
                kind=BlockKind.TABLE,
                table=TableData(
                    caption="Interrupt source list",
                    headers=["type", "irq_src信号", "位宽", "Description"],
                    rows=[
                        ["function", "radm_cpl_timeout", "1", "completion timeout"],
                        ["error", "edma_int", "32", "DMA interrupt"],
                    ],
                ),
            )
        ])],
    )

    result = TableEntityExtractor(schema_names=["interrupt", "signal"]).extract(
        doc,
        ExtractContext(family="chip"),
    )

    interrupts = [n for n in result.nodes if n.kind == NodeKind.INTERRUPT]
    signals = [n for n in result.nodes if n.kind == NodeKind.SIGNAL]
    assert [n.name for n in interrupts] == ["radm_cpl_timeout", "edma_int"]
    assert [n.attrs["type"] for n in interrupts] == ["function", "error"]
    assert signals == []


def test_table_entity_extracts_interface_instance_name_and_protocol():
    from docgraph.extractors.base import ExtractContext
    from docgraph.extractors.table_entity import TableEntityExtractor
    from docgraph.graph.schema import Block, BlockKind, NodeKind, ParsedDoc, ParsedPage, TableData

    doc = ParsedDoc(
        doc_id="chip::doc::r82",
        source_path="r82.pdf",
        pages=[ParsedPage(page_no=10, blocks=[
            Block(
                id="chip::doc::r82#p10#b0",
                doc_id="chip::doc::r82",
                page=10,
                kind=BlockKind.TABLE,
                table=TableData(
                    headers=["Name", "Protocol", "Width", "Details"],
                    rows=[
                        [
                            "Generic Interrupt Controller (GIC) Stream interface",
                            "AMBA 4 AXI4-Stream",
                            "32-bit",
                            "AXI-4 Stream interface for interrupts.",
                        ],
                        ["DebugBlock", "AMBA 4 APB", "32-bit", "APB debug interface."],
                    ],
                ),
            )
        ])],
    )

    result = TableEntityExtractor(schema_names=["interface"]).extract(
        doc,
        ExtractContext(family="chip"),
    )

    interfaces = [n for n in result.nodes if n.kind == NodeKind.INTERFACE]
    assert [n.name for n in interfaces] == [
        "Generic Interrupt Controller (GIC) Stream interface",
        "DebugBlock",
    ]
    assert [n.attrs["protocol"] for n in interfaces] == ["AMBA 4 AXI4-Stream", "AMBA 4 APB"]


def test_table_entity_rejects_interface_group_and_address_map_tables():
    from docgraph.extractors.schema_registry import get_schema
    from docgraph.extractors.table_entity import TableEntityExtractor
    from docgraph.graph.schema import TableData

    schema = get_schema("interface")
    assert schema is not None
    ex = TableEntityExtractor()

    interface_group = TableData(
        caption="Table 2-1 PCIe 外围接口",
        headers=["Interface Group", "方向", "Description"],
        rows=[
            ["Clock/Reset", "Clock/Reset", "Clock/Reset"],
            ["mstr_aclk", "0", "AXI master的NoC接口时钟"],
            ["AXI Master接口", "", "标准AXI4 接口，512bit 数据位宽"],
            ["Interrupts", "", "其他配置接口的转换"],
            ["TXx_P/N", "10", "16 lane 差分串行输出数据"],
        ],
    )
    address_map = TableData(
        caption="Table 4-2 本地 NoC 的地址映射",
        headers=["NoC Master", "NoC Slave", "Offset", "Size", "Description"],
        rows=[
            ["pcie_ss_noc", "Top CFG", "0x00000000", "1MB", "寄存器空间"],
            ["pcie_ss_noc", "PHY1 CFG", "0x00300000", "1MB", "PHY寄存器空间"],
        ],
    )
    real_interface = TableData(
        headers=["Name", "Protocol", "Width", "Details"],
        rows=[["DebugBlock", "AMBA 4 APB", "32-bit", "APB debug interface"]],
    )

    assert not ex._table_matches(interface_group, schema)
    assert not ex._extract_interfaces_from_table(interface_group)
    assert not ex._table_matches(address_map, schema)
    assert not ex._extract_interfaces_from_table(address_map)
    assert ex._table_matches(real_interface, schema)


def test_table_entity_deterministically_extracts_backend_timing_constraints_without_llm():
    from docgraph.extractors.base import ExtractContext
    from docgraph.extractors.table_entity import TableEntityExtractor
    from docgraph.graph.schema import Block, BlockKind, NodeKind, ParsedDoc, ParsedPage, TableData

    doc = ParsedDoc(
        doc_id="chip::doc::backend_timing",
        source_path="backend_timing_spec.pdf",
        pages=[ParsedPage(page_no=1, blocks=[
            Block(
                id="chip::doc::backend_timing#p1#b0",
                doc_id="chip::doc::backend_timing",
                page=1,
                kind=BlockKind.TABLE,
                table=TableData(
                    caption="STA / SDC constraint summary",
                    headers=["Constraint", "Target", "Value", "Unit", "Corner", "Description"],
                    rows=[
                        ["clock_uncertainty", "core_clk", "0.08", "ns", "SSG_0p72V_125C", "setup margin"],
                        ["max_transition", "all_outputs", "0.20", "ns", "all", "route slew limit"],
                    ],
                ),
            )
        ])],
    )

    result = TableEntityExtractor(schema_names=["constraint"]).extract(
        doc,
        ExtractContext(family="chip"),
    )

    constraints = [n for n in result.nodes if n.kind == NodeKind.REQUIREMENT]
    assert [n.name for n in constraints] == ["clock_uncertainty", "max_transition"]
    assert constraints[0].attrs["entity_type"] == "constraint"
    assert constraints[0].attrs["target"] == "core_clk"
    assert constraints[0].attrs["value"] == "0.08"
    assert constraints[0].attrs["unit"] == "ns"
    assert constraints[0].attrs["condition"] == "SSG_0p72V_125C"
    assert constraints[0].attrs["source_block_ids"] == ["chip::doc::backend_timing#p1#b0"]
    assert result.stats.llm_calls == 0


def test_table_entity_deterministically_extracts_backend_physical_constraints_without_llm():
    from docgraph.extractors.base import ExtractContext
    from docgraph.extractors.table_entity import TableEntityExtractor
    from docgraph.graph.schema import Block, BlockKind, NodeKind, ParsedDoc, ParsedPage, TableData

    doc = ParsedDoc(
        doc_id="chip::doc::backend_physical",
        source_path="physical_implementation_spec.pdf",
        pages=[ParsedPage(page_no=1, blocks=[
            Block(
                id="chip::doc::backend_physical#p1#b0",
                doc_id="chip::doc::backend_physical",
                page=1,
                kind=BlockKind.TABLE,
                table=TableData(
                    caption="Floorplan and routing constraints",
                    headers=["Rule", "Object", "Type", "Layer", "Region", "Spacing", "Description"],
                    rows=[
                        ["SRAM_keepout", "u_sram0", "keepout", "M2-M6", "CORE_NW", "5um", "macro halo"],
                        ["PG_strap_width", "VDD", "power grid", "M8", "core", "2um", "minimum strap width"],
                    ],
                ),
            )
        ])],
    )

    result = TableEntityExtractor(schema_names=["physical_constraint"]).extract(
        doc,
        ExtractContext(family="chip"),
    )

    constraints = [n for n in result.nodes if n.kind == NodeKind.REQUIREMENT]
    assert [n.name for n in constraints] == ["SRAM_keepout", "PG_strap_width"]
    assert constraints[0].attrs["entity_type"] == "physical_constraint"
    assert constraints[0].attrs["object"] == "u_sram0"
    assert constraints[0].attrs["layer"] == "M2-M6"
    assert constraints[0].attrs["region"] == "CORE_NW"
    assert constraints[0].attrs["value"] == "5um"
    assert constraints[0].attrs["source_block_ids"] == ["chip::doc::backend_physical#p1#b0"]
    assert result.stats.llm_calls == 0


def test_table_entity_normalizes_ocr_repeated_signal_width():
    from docgraph.extractors.base import ExtractContext
    from docgraph.extractors.table_entity import TableEntityExtractor
    from docgraph.graph.schema import Block, BlockKind, NodeKind, ParsedDoc, ParsedPage, TableData

    doc = ParsedDoc(
        doc_id="chip::doc::demo",
        source_path="demo.pdf",
        pages=[ParsedPage(page_no=1, blocks=[
            Block(
                id="chip::doc::demo#p1#b0",
                doc_id="chip::doc::demo",
                page=1,
                kind=BlockKind.TABLE,
                table=TableData(
                    caption="Signal list",
                    headers=["Signal", "位宽", "Description"],
                    rows=[["phy_plllock_int", "1 1", "PLL lock interrupt"]],
                ),
            )
        ])],
    )

    result = TableEntityExtractor(schema_names=["signal"]).extract(
        doc,
        ExtractContext(family="chip"),
    )

    signals = [n for n in result.nodes if n.kind == NodeKind.SIGNAL]
    assert signals[0].attrs["width"] == "1"


def test_table_entity_deterministically_extracts_signals_without_llm():
    from docgraph.extractors.base import ExtractContext
    from docgraph.extractors.table_entity import TableEntityExtractor
    from docgraph.graph.schema import Block, BlockKind, NodeKind, ParsedDoc, ParsedPage, TableData

    doc = ParsedDoc(
        doc_id="chip::doc::demo",
        source_path="demo.pdf",
        pages=[ParsedPage(page_no=1, blocks=[
            Block(
                id="chip::doc::demo#p1#b0",
                doc_id="chip::doc::demo",
                page=1,
                kind=BlockKind.TABLE,
                table=TableData(
                    headers=["Signal", "Width", "Direction", "Description"],
                    rows=[
                        ["clk", "1", "IN", "clock"],
                        ["axi_awaddr", "32", "OUT", "AXI address"],
                    ],
                ),
            )
        ])],
    )

    result = TableEntityExtractor(schema_names=["signal"]).extract(
        doc,
        ExtractContext(family="chip"),
    )

    signals = [n for n in result.nodes if n.kind == NodeKind.SIGNAL]
    assert [n.name for n in signals] == ["clk", "axi_awaddr"]
    assert [n.attrs["width"] for n in signals] == ["1", "32"]
    assert [n.attrs["direction"] for n in signals] == ["IN", "OUT"]


def test_table_entity_extracts_signal_names_from_interface_group_table():
    from docgraph.extractors.base import ExtractContext
    from docgraph.extractors.table_entity import TableEntityExtractor
    from docgraph.graph.schema import Block, BlockKind, NodeKind, ParsedDoc, ParsedPage, TableData

    doc = ParsedDoc(
        doc_id="chip::doc::demo",
        source_path="demo.pdf",
        pages=[ParsedPage(page_no=1, blocks=[
            Block(
                id="chip::doc::demo#p1#b0",
                doc_id="chip::doc::demo",
                page=1,
                kind=BlockKind.TABLE,
                table=TableData(
                    caption="Table 2-1 IP peripheral interfaces",
                    headers=["Interface Group", "方向", "Description"],
                    rows=[
                        ["Clock/Reset", "Clock/Reset", "Clock/Reset"],
                        ["mstr_aclk", "0", "AXI master NoC clock"],
                        ["mstr_rst_n", "0", "AXI master NoC reset"],
                        ["slv_aclk", "0", "AXI slave NoC clock"],
                    ],
                    n_cols=3,
                ),
            )
        ])],
    )

    result = TableEntityExtractor(schema_names=["signal"]).extract(
        doc,
        ExtractContext(family="chip"),
    )

    signals = [n for n in result.nodes if n.kind == NodeKind.SIGNAL]
    assert [n.name for n in signals] == ["mstr_aclk", "mstr_rst_n", "slv_aclk"]
    assert "Clock/Reset" not in {n.name for n in signals}
    assert [n.attrs["direction"] for n in signals] == [None, None, None]


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
    assert p.can_parse(Path("x.pdf")) is (find_spec("marker") is not None)
    assert not p.can_parse(Path("x.docx"))


def test_mineru_parser_basics(monkeypatch):
    from docgraph.parsers import mineru_parser
    from docgraph.parsers.mineru_parser import MinerUParser

    monkeypatch.setattr(mineru_parser, "find_spec", lambda name: object() if name == "mineru" else None)
    monkeypatch.setattr(mineru_parser.shutil, "which", lambda name: "/bin/mineru" if name == "mineru" else None)
    p = MinerUParser()
    assert p.name == "mineru"
    assert p.can_parse(Path("x.pdf"))
    assert not p.can_parse(Path("x.docx"))
