from __future__ import annotations

import json
from pathlib import Path

from docgraph.extractors.base import ExtractContext
from docgraph.extractors.figure import FigureExtractor
from docgraph.graph.schema import (
    Block,
    BlockKind,
    DocMetadata,
    EdgeKind,
    NodeKind,
    ParsedDoc,
    ParsedFigure,
    ParsedPage,
)
from docgraph.llm.client import LLMResponse


class FakeVLM:
    disabled = False

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def describe(self, image_path: Path, prompt: str, **kwargs) -> LLMResponse:
        self.prompts.append(prompt)
        return LLMResponse(
            text=json.dumps(self.payload),
            model="fake-vlm",
            tokens_in=1,
            tokens_out=1,
        )


def _image(tmp_path: Path) -> str:
    path = tmp_path / "figure.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    return str(path)


def test_chip_figure_vlm_materializes_semantic_nodes_and_edges(tmp_path):
    image_path = _image(tmp_path)
    payload = {
        "domain": "chip",
        "figure_type": "block",
        "summary": "PCIe 子系统通过 AXI 接口连接 DMA 和配置模块。",
        "modules": [
            {
                "name": "PCIe Subsystem",
                "role": "endpoint",
                "description": "PCIe 顶层子系统",
            },
            {
                "name": "DMA Engine",
                "role": "data mover",
                "description": "搬运入站和出站数据",
            },
        ],
        "signals": [
            {
                "name": "mstr_aclk",
                "direction": "IN",
                "width": "1",
                "description": "AXI master clock",
            },
        ],
        "interfaces": [
            {
                "name": "AXI Master",
                "protocol": "AXI",
                "role": "master",
                "width": "128",
                "description": "DMA data interface",
            },
        ],
        "clocks_resets": [],
        "address_regions": [],
        "connections": [
            {
                "source": "PCIe Subsystem",
                "target": "DMA Engine",
                "label": "TLP data",
                "kind": "connects_to",
            },
            {
                "source": "DMA Engine",
                "target": "AXI Master",
                "label": "AXI write/read",
                "kind": "connects_to",
            },
        ],
        "mermaid": "graph LR\n  PCIe-->DMA",
        "wavejson": None,
        "plantuml": None,
        "confidence": 0.86,
    }
    doc = ParsedDoc(
        doc_id="chip::doc",
        source_path="pcie_spec.pdf",
        metadata=DocMetadata(title="PCIe Subsystem Specification", family="pcie"),
        pages=[
            ParsedPage(
                page_no=3,
                blocks=[
                    Block(
                        id="chip::doc#p3#b1",
                        doc_id="chip::doc",
                        page=3,
                        kind=BlockKind.PARAGRAPH,
                        text="The PCIe subsystem uses AXI master/slave interfaces.",
                    ),
                    Block(
                        id="chip::doc#p3#b2",
                        doc_id="chip::doc",
                        page=3,
                        kind=BlockKind.FIGURE,
                        text="Figure 3-1 PCIe Subsystem Architecture",
                        image_path=image_path,
                    ),
                ],
                figures=[
                    ParsedFigure(
                        image_path=image_path,
                        caption="Figure 3-1 PCIe Subsystem Architecture",
                    )
                ],
            )
        ],
    )

    result = FigureExtractor().extract(
        doc,
        ExtractContext(
            family="chip",
            options={"vlm_client": FakeVLM(payload), "root": str(tmp_path)},
        ),
    )

    assert result.stats.llm_calls == 1
    figure = next(n for n in result.nodes if n.kind == NodeKind.FIGURE)
    assert figure.attrs["domain"] == "chip"
    assert figure.attrs["source_block_ids"] == ["chip::doc#p3#b2"]
    assert figure.attrs["source_chunk_ids"]
    assert figure.attrs["semantic_summary"].startswith("PCIe")
    assert figure.attrs["mermaid"]

    kinds = {n.name: n.kind for n in result.nodes}
    assert kinds["PCIe Subsystem"] == NodeKind.MODULE
    assert kinds["DMA Engine"] == NodeKind.MODULE
    assert kinds["mstr_aclk"] == NodeKind.SIGNAL
    assert kinds["AXI Master"] == NodeKind.INTERFACE
    for node in result.nodes:
        assert node.attrs.get("source_chunk_ids"), node.name

    assert any(e.kind == EdgeKind.ILLUSTRATED_BY and e.dst == figure.id for e in result.edges)
    assert all(e.attrs.get("source_chunk_ids") for e in result.edges)
    assert any(
        e.kind == EdgeKind.CONNECTS_TO and e.attrs.get("label") == "TLP data"
        for e in result.edges
    )


def test_general_figure_vlm_does_not_emit_chip_entities(tmp_path):
    image_path = _image(tmp_path)
    payload = {
        "domain": "general",
        "figure_type": "flow",
        "summary": "流程图展示申请、审批和归档步骤。",
        "entities": [
            {"name": "申请", "type": "step", "description": "提交申请"},
            {"name": "审批", "type": "step", "description": "审批申请"},
        ],
        "relationships": [
            {"source": "申请", "target": "审批", "label": "next", "kind": "connects_to"},
        ],
        "mermaid": "graph LR\n  A-->B",
        "confidence": 0.8,
    }
    doc = ParsedDoc(
        doc_id="general::doc",
        source_path="process.pdf",
        metadata=DocMetadata(title="Office Process Guide"),
        pages=[
            ParsedPage(
                page_no=1,
                blocks=[
                    Block(
                        id="general::doc#p1#b1",
                        doc_id="general::doc",
                        page=1,
                        kind=BlockKind.PARAGRAPH,
                        text="This page describes an approval workflow.",
                    ),
                    Block(
                        id="general::doc#p1#b2",
                        doc_id="general::doc",
                        page=1,
                        kind=BlockKind.FIGURE,
                        text="Approval workflow",
                        image_path=image_path,
                    ),
                ],
                figures=[ParsedFigure(image_path=image_path, caption="Approval workflow")],
            )
        ],
    )

    result = FigureExtractor().extract(
        doc,
        ExtractContext(
            family="general",
            options={"vlm_client": FakeVLM(payload), "root": str(tmp_path)},
        ),
    )

    assert result.stats.llm_calls == 1
    assert len(result.edges) == 0
    assert [n.kind for n in result.nodes] == [NodeKind.FIGURE]
    assert result.nodes[0].attrs["domain"] == "general"
    assert result.nodes[0].attrs["source_chunk_ids"]
    assert result.nodes[0].attrs["semantic_entities"]["entities"][0]["name"] == "申请"


def test_figure_extractor_skips_l0_decoration_blocks(tmp_path):
    image_path = _image(tmp_path)
    doc = ParsedDoc(
        doc_id="chip::doc",
        source_path="cover.pdf",
        metadata=DocMetadata(title="Cover"),
        pages=[
            ParsedPage(
                page_no=1,
                blocks=[
                    Block(
                        id="chip::doc#p1#b0",
                        doc_id="chip::doc",
                        page=1,
                        kind=BlockKind.FIGURE,
                        image_path=image_path,
                        attrs={"semantic_role": "decoration"},
                    )
                ],
                figures=[ParsedFigure(image_path=image_path)],
            )
        ],
    )

    result = FigureExtractor().extract(
        doc,
        ExtractContext(family="chip", options={"root": str(tmp_path)}),
    )

    assert result.nodes == []
    assert result.edges == []
