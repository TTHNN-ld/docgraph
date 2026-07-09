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


def test_chip_figure_normalizes_protocol_only_interface_names(tmp_path):
    image_path = _image(tmp_path)
    payload = {
        "domain": "chip",
        "figure_type": "block",
        "summary": "APB connects to debug registers.",
        "modules": [],
        "signals": [],
        "interfaces": [
            {
                "name": "APB",
                "protocol": "APB",
                "role": "slave",
                "description": "debug register access",
            },
            {
                "name": "PCIe",
                "protocol": "PCIe",
                "description": "generic protocol label without role",
            },
        ],
        "clocks_resets": [],
        "address_regions": [],
        "connections": [],
        "confidence": 0.8,
    }
    doc = ParsedDoc(
        doc_id="chip::doc",
        source_path="pcie_spec.pdf",
        metadata=DocMetadata(title="PCIe Subsystem Specification", family="pcie"),
        pages=[
            ParsedPage(
                page_no=9,
                blocks=[
                    Block(
                        id="chip::doc#p9#b1",
                        doc_id="chip::doc",
                        page=9,
                        kind=BlockKind.FIGURE,
                        text="debug block",
                        image_path=image_path,
                    )
                ],
                figures=[ParsedFigure(image_path=image_path, caption="debug block")],
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

    interfaces = [n for n in result.nodes if n.kind == NodeKind.INTERFACE]
    assert [n.name for n in interfaces] == ["APB slave"]
    assert interfaces[0].attrs["protocol"] == "APB"


def test_chip_figure_zero_model_confidence_gets_conservative_edge_confidence(tmp_path):
    image_path = _image(tmp_path)
    payload = {
        "domain": "chip",
        "figure_type": "block",
        "summary": "PCIe core connects to DMA.",
        "modules": [
            {"name": "PCIe Core", "role": "endpoint", "description": "core"},
            {"name": "DMA", "role": "engine", "description": "dma"},
        ],
        "signals": [],
        "interfaces": [],
        "clocks_resets": [],
        "address_regions": [],
        "connections": [
            {"source": "PCIe Core", "target": "DMA", "kind": "connects_to"},
        ],
        "confidence": 0.0,
    }
    doc = ParsedDoc(
        doc_id="chip::doc",
        source_path="pcie_spec.pdf",
        metadata=DocMetadata(title="PCIe Subsystem Specification", family="pcie"),
        pages=[ParsedPage(page_no=3, blocks=[
            Block(
                id="chip::doc#p3#b2",
                doc_id="chip::doc",
                page=3,
                kind=BlockKind.FIGURE,
                text="Figure 3-1 PCIe Subsystem Architecture",
                image_path=image_path,
            ),
        ])],
    )

    result = FigureExtractor().extract(
        doc,
        ExtractContext(
            family="chip",
            options={"vlm_client": FakeVLM(payload), "root": str(tmp_path)},
        ),
    )

    assert result.edges
    assert all(edge.confidence >= 0.65 for edge in result.edges)


def test_figure_extractor_skips_weak_semantic_and_captionless_duplicate(tmp_path):
    image_path = _image(tmp_path)
    payload = {
        "domain": "chip",
        "figure_type": "other",
        "summary": "Figure 6-5 ECC Protection range",
        "modules": [],
        "signals": [],
        "interfaces": [],
        "clocks_resets": [],
        "address_regions": [],
        "connections": [],
        "confidence": 0.0,
    }
    doc = ParsedDoc(
        doc_id="chip::doc",
        source_path="pcie_spec.pdf",
        metadata=DocMetadata(title="PCIe Subsystem Specification", family="pcie"),
        pages=[ParsedPage(page_no=39, blocks=[
            Block(
                id="chip::doc#p39#b1",
                doc_id="chip::doc",
                page=39,
                kind=BlockKind.FIGURE,
                text="Figure 6-5 ECC Protection range",
                image_path=image_path,
            ),
            Block(
                id="chip::doc#p39#b2",
                doc_id="chip::doc",
                page=39,
                kind=BlockKind.FIGURE,
                text="",
                image_path=image_path,
            ),
        ])],
    )

    result = FigureExtractor().extract(
        doc,
        ExtractContext(
            family="chip",
            options={"vlm_client": FakeVLM(payload), "root": str(tmp_path)},
        ),
    )

    assert [n.kind for n in result.nodes] == [NodeKind.FIGURE]
    assert result.nodes[0].attrs["quality_flags"] == ["weak_semantic"]
    assert result.edges == []


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


def _doc_with_figures(tmp_path: Path, n: int) -> ParsedDoc:
    """n captioned figure blocks, each on its own page with a real image file."""
    pages = []
    for i in range(n):
        img = tmp_path / f"fig_{i}.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")
        pages.append(
            ParsedPage(
                page_no=i + 1,
                blocks=[
                    Block(
                        id=f"doc::d#p{i + 1}#b0",
                        doc_id="doc::d",
                        page=i + 1,
                        kind=BlockKind.FIGURE,
                        text=f"Figure {i} block diagram",
                        image_path=str(img),
                    )
                ],
            )
        )
    return ParsedDoc(
        doc_id="doc::d",
        source_path="spec.pdf",
        metadata=DocMetadata(title="Spec"),
        pages=pages,
    )


def test_vlm_figure_limit_from_config(tmp_path, monkeypatch):
    """llm.vlm.figure_limit (threaded via options) caps per-doc VLM calls."""
    monkeypatch.delenv("DOCGRAPH_VLM_FIGURE_LIMIT", raising=False)
    doc = _doc_with_figures(tmp_path, 12)
    vlm = FakeVLM({"domain": "general", "summary": "ok", "confidence": 0.5})
    result = FigureExtractor().extract(
        doc,
        ExtractContext(
            family="fam",
            options={"vlm_client": vlm, "root": str(tmp_path), "vlm_figure_limit": 5},
        ),
    )
    assert len(vlm.prompts) == 5
    assert result.stats.llm_calls == 5


def test_vlm_figure_limit_env_overrides_config(tmp_path, monkeypatch):
    """DOCGRAPH_VLM_FIGURE_LIMIT env var overrides the config value."""
    monkeypatch.setenv("DOCGRAPH_VLM_FIGURE_LIMIT", "3")
    doc = _doc_with_figures(tmp_path, 12)
    vlm = FakeVLM({"domain": "general", "summary": "ok", "confidence": 0.5})
    FigureExtractor().extract(
        doc,
        ExtractContext(
            family="fam",
            options={"vlm_client": vlm, "root": str(tmp_path), "vlm_figure_limit": 5},
        ),
    )
    assert len(vlm.prompts) == 3


def test_vlm_figure_limit_default_when_unset(tmp_path, monkeypatch):
    """No env, no config -> DEFAULT_VLM_FIGURE_LIMIT (8)."""
    monkeypatch.delenv("DOCGRAPH_VLM_FIGURE_LIMIT", raising=False)
    doc = _doc_with_figures(tmp_path, 12)
    vlm = FakeVLM({"domain": "general", "summary": "ok", "confidence": 0.5})
    FigureExtractor().extract(
        doc,
        ExtractContext(family="fam", options={"vlm_client": vlm, "root": str(tmp_path)}),
    )
    assert len(vlm.prompts) == FigureExtractor.DEFAULT_VLM_FIGURE_LIMIT == 8


# --- VLM diagram output validation ---


def _one_chip_figure(tmp_path: Path, *, caption: str, payload: dict) -> ParsedDoc:
    image_path = _image(tmp_path)
    return ParsedDoc(
        doc_id="chip::doc",
        source_path="spec.pdf",
        metadata=DocMetadata(title="PCIe Subsystem Specification", family="pcie"),
        pages=[
            ParsedPage(
                page_no=3,
                blocks=[
                    Block(
                        id="chip::doc#p3#b2",
                        doc_id="chip::doc",
                        page=3,
                        kind=BlockKind.FIGURE,
                        text=caption,
                        image_path=image_path,
                    ),
                ],
                figures=[ParsedFigure(image_path=image_path, caption=caption)],
            )
        ],
    ), image_path


def _extract_one(tmp_path: Path, payload: dict) -> object:
    doc, _ = _one_chip_figure(tmp_path, caption="Figure 3-1 block diagram", payload=payload)
    result = FigureExtractor().extract(
        doc,
        ExtractContext(
            family="chip",
            options={"vlm_client": FakeVLM(payload), "root": str(tmp_path)},
        ),
    )
    return next(n for n in result.nodes if n.kind == NodeKind.FIGURE)


def _chip_payload(**overrides) -> dict:
    base = {
        "domain": "chip",
        "figure_type": "block",
        "summary": "block diagram of the subsystem",
        "modules": [],
        "signals": [],
        "interfaces": [],
        "clocks_resets": [],
        "address_regions": [],
        "connections": [],
        "mermaid": None,
        "wavejson": None,
        "plantuml": None,
        "confidence": 0.8,
    }
    base.update(overrides)
    return base


def test_valid_mermaid_wavejson_plantuml_pass_through(tmp_path):
    figure = _extract_one(
        tmp_path,
        _chip_payload(
            mermaid="graph LR\n  A-->B",
            wavejson={"signal": [{"name": "clk", "wave": "p."}]},
            plantuml="@startuml\nstate Idle\nIdle --> Running : start\n@enduml",
        ),
    )
    assert figure.attrs["mermaid"] == "graph LR\n  A-->B"
    assert figure.attrs["wavejson"] == {"signal": [{"name": "clk", "wave": "p."}]}
    assert "@startuml" in figure.attrs["plantuml"]
    assert "malformed_mermaid" not in figure.attrs["quality_flags"]
    assert "malformed_wavejson" not in figure.attrs["quality_flags"]
    assert "malformed_plantuml" not in figure.attrs["quality_flags"]


def test_wavejson_json_string_with_signal_key_is_accepted(tmp_path):
    figure = _extract_one(
        tmp_path,
        _chip_payload(wavejson='{"signal": [{"name": "clk", "wave": "p."}]}'),
    )
    assert figure.attrs["wavejson"] == '{"signal": [{"name": "clk", "wave": "p."}]}'
    assert "malformed_wavejson" not in figure.attrs["quality_flags"]


def test_malformed_mermaid_prose_prefix_is_nulled_and_flagged(tmp_path):
    figure = _extract_one(
        tmp_path,
        _chip_payload(mermaid="Here is the diagram:\ngraph LR\n  A-->B"),
    )
    assert figure.attrs["mermaid"] is None
    assert "malformed_mermaid" in figure.attrs["quality_flags"]
    # semantic_entities stays consistent with the rendering attr
    assert figure.attrs["semantic_entities"]["mermaid"] is None


def test_malformed_mermaid_empty_string_is_nulled_and_flagged(tmp_path):
    figure = _extract_one(tmp_path, _chip_payload(mermaid="   "))
    assert figure.attrs["mermaid"] is None
    assert "malformed_mermaid" in figure.attrs["quality_flags"]


def test_malformed_wavejson_invalid_json_is_nulled_and_flagged(tmp_path):
    figure = _extract_one(tmp_path, _chip_payload(wavejson="not valid json {"))
    assert figure.attrs["wavejson"] is None
    assert "malformed_wavejson" in figure.attrs["quality_flags"]


def test_malformed_wavejson_missing_wavedrom_key_is_nulled_and_flagged(tmp_path):
    figure = _extract_one(tmp_path, _chip_payload(wavejson='{"foo": "bar"}'))
    assert figure.attrs["wavejson"] is None
    assert "malformed_wavejson" in figure.attrs["quality_flags"]


def test_malformed_plantuml_plain_text_is_nulled_and_flagged(tmp_path):
    figure = _extract_one(
        tmp_path,
        _chip_payload(plantuml="this is a state machine description"),
    )
    assert figure.attrs["plantuml"] is None
    assert "malformed_plantuml" in figure.attrs["quality_flags"]


def test_multiple_malformed_outputs_all_flagged(tmp_path):
    figure = _extract_one(
        tmp_path,
        _chip_payload(
            mermaid="prose not mermaid",
            wavejson="broken{",
            plantuml="plain text",
        ),
    )
    assert figure.attrs["mermaid"] is None
    assert figure.attrs["wavejson"] is None
    assert figure.attrs["plantuml"] is None
    flags = figure.attrs["quality_flags"]
    assert "malformed_mermaid" in flags
    assert "malformed_wavejson" in flags
    assert "malformed_plantuml" in flags


def test_missing_diagram_outputs_produce_no_flags(tmp_path):
    """None (= VLM did not produce the field) is not a failure."""
    figure = _extract_one(tmp_path, _chip_payload())  # all three None
    assert figure.attrs["mermaid"] is None
    assert figure.attrs["wavejson"] is None
    assert figure.attrs["plantuml"] is None
    assert figure.attrs["quality_flags"] == []


def test_unwrapped_plantuml_state_keyword_is_accepted(tmp_path):
    figure = _extract_one(
        tmp_path,
        _chip_payload(plantuml="state Idle\nstate Running\nIdle --> Running : go"),
    )
    assert figure.attrs["plantuml"] is not None
    assert "malformed_plantuml" not in figure.attrs["quality_flags"]
