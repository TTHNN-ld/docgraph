"""FigureExtractor —— figure/image L2 extraction and optional VLM semantics.

Parser output is treated as L0 evidence: image path, caption, page and bbox are
preserved even when no VLM is configured. VLM enrichment is optional and routed
by document/figure context:

- chip-like specs use a chip semantics prompt and can emit MODULE/SIGNAL/etc.
- non-chip documents use a generic figure prompt and only enrich the FIGURE node.
"""
from __future__ import annotations

import re
import os
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from docgraph.core.ids import content_hash, make_node_id
from docgraph.core.logger import get_logger
from docgraph.extractors.base import ExtractContext
from docgraph.extractors.candidates import EntityCandidate, build_entity_candidates
from docgraph.graph.schema import (
    BlockKind,
    Edge,
    EdgeKind,
    Evidence,
    ExtractResult,
    ExtractStats,
    Location,
    Node,
    NodeKind,
    ParsedDoc,
)

log = get_logger(__name__)


_FIG_TYPE_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"timing|waveform|时序|波形|sequence|protocol", re.I), "timing"),
    (re.compile(r"state\s*machine|fsm|ltssm|状态机", re.I), "fsm"),
    (
        re.compile(r"address\s*map|memory\s*map|bar|寄存器映射|地址映射", re.I),
        "address_map",
    ),
    (re.compile(r"clock|reset|power|pll|时钟|复位|电源", re.I), "clock_reset"),
    (
        re.compile(r"data\s*path|inbound|outbound|pipeline|path|数据通路", re.I),
        "data_path",
    ),
    (
        re.compile(
            r"block\s*diagram|architecture|system\s*level|diagram|框图|结构图|架构",
            re.I,
        ),
        "block",
    ),
    (re.compile(r"flow\s*chart|flow|流程图", re.I), "flow"),
    (re.compile(r"schematic|电路图", re.I), "schematic"),
]

_CHIP_DOMAIN_RE = re.compile(
    r"\b("
    r"soc|asic|fpga|rtl|ip|ip[-_ ]?xact|register|bitfield|signal|interface|"
    r"clock|reset|bus|axi|apb|ahb|pcie|pipe|serdes|dma|irq|interrupt|"
    r"address\s*map|memory\s*map|bar|csr|gpio|spi|i2c|uart|usb|ethernet|"
    r"module|subsystem|controller|clock\s*domain|reset\s*domain"
    r")\b|"
    r"芯片|寄存器|位域|管脚|引脚|信号|接口|总线|"
    r"时钟|复位|中断|地址映射|模块|子系统",
    re.I,
)

_OUTPUT_FORMAT: dict[str, str] = {
    "timing": "WaveJSON plus extracted signals/events",
    "fsm": "PlantUML state diagram plus states/transitions",
    "block": "Mermaid graph plus modules/interfaces/connections",
    "data_path": "Mermaid graph plus data-flow connections",
    "address_map": "address regions plus Mermaid graph if useful",
    "clock_reset": "clock/reset domains plus connections",
    "flow": "Mermaid flowchart plus steps/edges",
    "schematic": "structured component and connection list",
}


def _figure_blocks(page) -> list:
    return [
        block
        for block in sorted(page.blocks, key=lambda b: b.reading_order)
        if block.kind == BlockKind.FIGURE
    ]


class FigureConnectionDef(BaseModel):
    source: str
    target: str
    label: str | None = None
    kind: Literal["connects_to", "depends_on", "controls", "references"] = "connects_to"
    description: str = ""


class FigureModuleDef(BaseModel):
    name: str
    role: str | None = None
    description: str = ""


class FigureSignalDef(BaseModel):
    name: str
    direction: str | None = None
    width: str | None = None
    description: str = ""


class FigureInterfaceDef(BaseModel):
    name: str
    protocol: str | None = None
    role: str | None = None
    width: str | None = None
    description: str = ""


class FigureClockResetDef(BaseModel):
    name: str
    type: Literal["clock", "reset", "power"] = "clock"
    domain: str | None = None
    polarity: str | None = None
    frequency: str | None = None
    description: str = ""


class FigureAddressRegionDef(BaseModel):
    name: str
    address: str | None = None
    size: str | None = None
    target: str | None = None
    description: str = ""


class ChipFigureSemantic(BaseModel):
    domain: Literal["chip"] = "chip"
    figure_type: Literal[
        "timing", "waveform", "fsm", "block", "data_path", "address_map",
        "clock_reset", "flow", "schematic", "other",
    ] = "other"
    summary: str = Field(default="", description="Concise chip-relevant summary.")
    modules: list[FigureModuleDef] = Field(default_factory=list)
    signals: list[FigureSignalDef] = Field(default_factory=list)
    interfaces: list[FigureInterfaceDef] = Field(default_factory=list)
    clocks_resets: list[FigureClockResetDef] = Field(default_factory=list)
    address_regions: list[FigureAddressRegionDef] = Field(default_factory=list)
    connections: list[FigureConnectionDef] = Field(default_factory=list)
    mermaid: str | None = None
    wavejson: dict | str | None = None
    plantuml: str | None = None
    confidence: float = 0.75


class GeneralFigureEntity(BaseModel):
    name: str
    type: str | None = None
    description: str = ""


class GeneralFigureSemantic(BaseModel):
    domain: Literal["general"] = "general"
    figure_type: Literal[
        "chart", "diagram", "flow", "table_image", "photo", "screenshot",
        "timeline", "map", "other",
    ] = "other"
    summary: str = Field(default="", description="Concise factual summary.")
    entities: list[GeneralFigureEntity] = Field(default_factory=list)
    relationships: list[FigureConnectionDef] = Field(default_factory=list)
    mermaid: str | None = None
    confidence: float = 0.7


class FigureExtractor:
    name = "figure"
    kinds = {NodeKind.FIGURE}
    requires = {"section"}
    version = "0.5"

    MAX_FIGURES_PER_DOC = 200
    DEFAULT_VLM_FIGURE_LIMIT = 8

    def extract(self, doc: ParsedDoc, ctx: ExtractContext) -> ExtractResult:
        t0 = time.time()
        nodes: list[Node] = []
        edges: list[Edge] = []
        vlm_calls = 0
        vlm_attempts = 0
        failed = 0

        vlm_client = ctx.options.get("vlm_client") if ctx.options else None
        domain = self._infer_domain(doc)
        vlm_limit = int(
            os.environ.get("DOCGRAPH_VLM_FIGURE_LIMIT", self.DEFAULT_VLM_FIGURE_LIMIT)
        )
        count = 0

        candidates = [c for c in build_entity_candidates(doc) if c.kind == "figure"]
        block_by_id = {
            block.id: block
            for page in doc.pages
            for block in page.blocks
            if block.kind == BlockKind.FIGURE
        }
        for candidate in candidates:
            page_context = self._page_context(doc, candidate.page)
            for block_id in candidate.block_ids:
                block = block_by_id.get(block_id)
                if block is None:
                    continue
                if count >= self.MAX_FIGURES_PER_DOC:
                    break
                count += 1

                if block.attrs.get("semantic_role") == "decoration":
                    continue
                source_block_ids = [block.id]
                source_chunk_ids = [candidate.chunk_id] if candidate.chunk_id else []
                caption = block.text or ""
                fig_type = self._infer_type(" ".join([caption, page_context]))
                fig_node = self._make_node(
                    block, candidate.page, fig_type, ctx, doc.doc_id,
                    domain=domain,
                    source_block_ids=source_block_ids,
                    source_chunk_ids=source_chunk_ids,
                    candidate_id=candidate.id,
                )

                can_call_vlm = (
                    vlm_client
                    and vlm_attempts < vlm_limit
                    and not getattr(vlm_client, "disabled", False)
                    and block.image_path
                )
                if can_call_vlm:
                    image_path = self._resolve_image_path(block.image_path, ctx)
                    if image_path is not None:
                        vlm_attempts += 1
                        try:
                            log.info(
                                f"[figure] VLM start page={candidate.page} "
                                f"type={fig_type} image={image_path.name}"
                            )
                            semantic = self._analyze_figure(
                                image_path=image_path,
                                domain=domain,
                                fig_type=fig_type,
                                caption=caption,
                                page_context=page_context,
                                vlm_client=vlm_client,
                            )
                            vlm_calls += 1
                            log.info(
                                f"[figure] VLM done page={candidate.page} "
                                f"type={semantic.figure_type}"
                            )
                            self._apply_semantic_to_figure(fig_node, semantic)
                            if isinstance(semantic, ChipFigureSemantic):
                                extra = self._materialize_chip_semantics(
                                    semantic=semantic,
                                    fig_node=fig_node,
                                    page_no=candidate.page,
                                    ctx=ctx,
                                    doc_id=doc.doc_id,
                                    source_block_ids=source_block_ids,
                                    source_chunk_ids=source_chunk_ids,
                                )
                                nodes.extend(extra["nodes"])
                                edges.extend(extra["edges"])
                        except Exception as e:
                            failed += 1
                            if not getattr(vlm_client, "disabled", False):
                                log.warning(
                                    f"[figure] VLM analysis failed for {block.image_path}: {e}"
                                )

                nodes.append(fig_node)
            if count >= self.MAX_FIGURES_PER_DOC:
                break

        return ExtractResult(
            nodes=nodes,
            edges=edges,
            stats=ExtractStats(
                nodes_emitted=len(nodes),
                edges_emitted=len(edges),
                duration_s=round(time.time() - t0, 3),
                llm_calls=vlm_calls,
                failed=failed,
            ),
        )

    # ------- routing -------

    @staticmethod
    def _infer_type(text: str) -> str:
        for pat, kind in _FIG_TYPE_HINTS:
            if pat.search(text):
                return kind
        return "other"

    @staticmethod
    def _infer_domain(doc: ParsedDoc) -> Literal["chip", "general"]:
        parts = [
            doc.metadata.title or "",
            doc.metadata.family or "",
            doc.metadata.type.value,
            doc.source_path,
        ]
        for page in doc.pages[:8]:
            parts.extend((b.text or "") for b in (page.blocks or [])[:30])
            parts.extend(b.text or "" for b in _figure_blocks(page)[:10])
        return "chip" if _CHIP_DOMAIN_RE.search("\n".join(parts)) else "general"

    @staticmethod
    def _page_context(doc: ParsedDoc, page_no: int, max_chars: int = 1600) -> str:
        page = next((p for p in doc.pages if p.page_no == page_no), None)
        if page is None:
            return ""
        texts = []
        for b in page.blocks:
            if b.kind in {BlockKind.HEADING, BlockKind.PARAGRAPH, BlockKind.CAPTION} and b.text:
                texts.append(b.text)
        return "\n".join(texts)[:max_chars]

    # ------- node construction -------

    def _make_node(
        self,
        fig: "Block",
        page_no: int,
        fig_type: str,
        ctx: ExtractContext,
        doc_id: str,
        *,
        domain: str,
        source_block_ids: list[str],
        source_chunk_ids: list[str],
        candidate_id: str | None = None,
    ) -> Node:
        caption = fig.text or None
        key = caption or fig.image_path or f"fig_p{page_no}"
        node_id = make_node_id(
            ctx.family,
            NodeKind.FIGURE,
            f"p{page_no}_{content_hash(key).split(':')[-1][:10]}",
            doc_id=doc_id,
        )
        return Node(
            id=node_id,
            kind=NodeKind.FIGURE,
            name=caption or f"figure_p{page_no}",
            qualified_name=caption or node_id,
            doc_id=doc_id,
            location=Location(page=page_no, bbox=fig.bbox),
            evidence=Evidence(
                chunk_ids=source_chunk_ids,
                pages=[page_no],
                bboxes=[fig.bbox] if fig.bbox else [],
                extractor=f"{self.name}@{self.version}",
                raw_snippet=caption,
            ),
            attrs={
                "figure_type": fig_type,
                "domain": domain,
                "image_path": fig.image_path,
                "caption": caption,
                "source": f"{self.name}@{self.version}",
                "source_block_ids": source_block_ids,
                "source_chunk_ids": source_chunk_ids,
                "candidate_id": candidate_id,
                "vlm_desc": None,
                "semantic_summary": None,
                "semantic_entities": [],
                "mermaid": None,
                "wavejson": None,
                "plantuml": None,
            },
            summary=(caption or "")[:120] or None,
            hash=content_hash(key),
        )

    @staticmethod
    def _resolve_image_path(image_path: str, ctx: ExtractContext) -> Path | None:
        path = Path(image_path)
        if path.is_file():
            return path
        root = None
        if ctx.options:
            raw_root = ctx.options.get("root")
            root = Path(raw_root) if raw_root else None
        if root is not None:
            candidate = root / image_path
            if candidate.is_file():
                return candidate
        return None

    # ------- VLM analysis -------

    def _analyze_figure(
        self,
        *,
        image_path: Path,
        domain: Literal["chip", "general"],
        fig_type: str,
        caption: str,
        page_context: str,
        vlm_client,
    ) -> ChipFigureSemantic | GeneralFigureSemantic:
        if domain == "chip":
            prompt = self._chip_prompt(fig_type, caption, page_context)
            schema = ChipFigureSemantic
            cache_key = f"figure-v{self.version}-chip-{fig_type}"
        else:
            prompt = self._general_prompt(fig_type, caption, page_context)
            schema = GeneralFigureSemantic
            cache_key = f"figure-v{self.version}-general-{fig_type}"

        resp = vlm_client.describe(
            image_path,
            prompt,
            extractor=self.name,
            max_tokens=8192,
            cache_key_extra=cache_key,
        )
        from docgraph.llm.client import _extract_json

        try:
            data = _extract_json(resp.text)
            return schema.model_validate(data)
        except Exception as e:
            text = (resp.text or "").strip()
            if text:
                log.info(
                    f"[figure] VLM returned non-JSON text for {domain} figure; "
                    "using summary fallback"
                )
                if domain == "chip":
                    return ChipFigureSemantic(
                        figure_type=fig_type if fig_type in ChipFigureSemantic.model_fields[
                            "figure_type"
                        ].annotation.__args__ else "other",
                        summary=text[:500],
                        confidence=0.65,
                    )
                return GeneralFigureSemantic(
                    summary=text[:500],
                    confidence=0.65,
                )
            raise RuntimeError(
                f"invalid VLM JSON for {domain} figure: {str(e)[:160]}"
            ) from e

    @staticmethod
    def _chip_prompt(fig_type: str, caption: str, page_context: str) -> str:
        target_format = _OUTPUT_FORMAT.get(fig_type, "chip semantic JSON")
        return (
            "你是芯片/SoC/IP spec 图语义抽取器。目标不是泛泛描述图片，"
            "而是提取对 RTL、验证、软件驱动、系统集成有用的芯片语义。\n"
            f"图类型初判：{fig_type}（如果判断错请在输出中纠正）\n"
            f"目标输出结构：{target_format}\n"
            f"Caption：{caption[:300] or '无'}\n"
            f"同页上下文：\n{page_context[:1600] or '无'}\n\n"
            "识别规则：\n"
            "1. 只抽图中或上下文明确出现的信息，不要臆测。\n"
            "2. 模块名、接口名、信号名、协议名保留原文，尤其是英文缩写。\n"
            "3. 框图/数据通路重点抽 modules、interfaces、connections。\n"
            "4. 时钟/复位/电源图重点抽 clocks_resets 与依赖/控制关系。\n"
            "5. 地址图重点抽 address_regions；时序/波形图重点抽 signals 和 wavejson。\n"
            "6. 输出必须是一个 JSON object；不要 markdown 代码块；不要额外解释文字。\n"
            "7. 数量上限：modules<=10，signals<=12，interfaces<=8，"
            "clocks_resets<=8，address_regions<=8，connections<=12。\n"
            "8. mermaid/wavejson/plantuml 字段必须存在；除非能保证是合法 JSON 字符串/"
            "对象，否则填 null。不要在 JSON 字符串里写未转义的真实换行。\n\n"
            "严格按如下 JSON schema 输出：\n"
            "{\n"
            '  "domain": "chip",\n'
            '  "figure_type": "timing|waveform|fsm|block|data_path|'
            'address_map|clock_reset|flow|schematic|other",\n'
            '  "summary": "中文简述，120字以内，聚焦芯片实际意义",\n'
            '  "modules": [\n'
            '    {"name": "...", "role": "...", "description": "..."}\n'
            "  ],\n"
            '  "signals": [\n'
            '    {"name": "...", "direction": "IN|OUT|IO|BIDIR|null", '
            '"width": "...", "description": "..."}\n'
            "  ],\n"
            '  "interfaces": [\n'
            '    {"name": "...", "protocol": "AXI|APB|PCIe|...", '
            '"role": "master|slave|endpoint|root|...", '
            '"width": "...", "description": "..."}\n'
            "  ],\n"
            '  "clocks_resets": [\n'
            '    {"name": "...", "type": "clock|reset|power", '
            '"domain": "...", "polarity": "...", "frequency": "...", '
            '"description": "..."}\n'
            "  ],\n"
            '  "address_regions": [\n'
            '    {"name": "...", "address": "...", "size": "...", '
            '"target": "...", "description": "..."}\n'
            "  ],\n"
            '  "connections": [\n'
            '    {"source": "...", "target": "...", "label": "...", '
            '"kind": "connects_to|depends_on|controls|references", '
            '"description": "..."}\n'
            "  ],\n"
            '  "mermaid": null,\n'
            '  "wavejson": null,\n'
            '  "plantuml": null,\n'
            '  "confidence": 0.0\n'
            "}"
        )

    @staticmethod
    def _general_prompt(fig_type: str, caption: str, page_context: str) -> str:
        return (
            "你是通用技术文档图语义抽取器。请客观提取图里的事实结构，"
            "不要强行套芯片/硬件本体。\n"
            f"图类型初判：{fig_type}（可纠正为 chart/diagram/flow/photo 等）\n"
            f"Caption：{caption[:300] or '无'}\n"
            f"同页上下文：\n{page_context[:1600] or '无'}\n\n"
            "严格输出 JSON object，不要 markdown 代码块，不要额外解释文字：\n"
            "{\n"
            '  "domain": "general",\n'
            '  "figure_type": "chart|diagram|flow|table_image|photo|'
            'screenshot|timeline|map|other",\n'
            '  "summary": "中文简述，120字以内",\n'
            '  "entities": [\n'
            '    {"name": "...", "type": "...", "description": "..."}\n'
            "  ],\n"
            '  "relationships": [\n'
            '    {"source": "...", "target": "...", "label": "...", '
            '"kind": "connects_to|depends_on|controls|references", '
            '"description": "..."}\n'
            "  ],\n"
            '  "mermaid": "流程/关系图可给 Mermaid；否则 null",\n'
            '  "confidence": 0.0\n'
            "}"
        )

    # ------- semantic materialization -------

    @staticmethod
    def _apply_semantic_to_figure(
        fig_node: Node,
        semantic: ChipFigureSemantic | GeneralFigureSemantic,
    ) -> None:
        data = semantic.model_dump(exclude_none=True)
        summary = data.get("summary") or ""
        fig_node.attrs.update({
            "domain": semantic.domain,
            "figure_type": data.get("figure_type") or fig_node.attrs.get("figure_type"),
            "semantic_summary": summary or None,
            "vlm_desc": summary[:240] or None,
            "semantic_entities": data,
            "mermaid": data.get("mermaid"),
            "wavejson": data.get("wavejson"),
            "plantuml": data.get("plantuml"),
            "confidence": data.get("confidence"),
        })
        if summary:
            fig_node.summary = summary[:120]

    def _materialize_chip_semantics(
        self,
        *,
        semantic: ChipFigureSemantic,
        fig_node: Node,
        page_no: int,
        ctx: ExtractContext,
        doc_id: str,
        source_block_ids: list[str],
        source_chunk_ids: list[str],
    ) -> dict[str, list]:
        nodes: list[Node] = []
        edges: list[Edge] = []
        name_to_id: dict[str, str] = {}

        def emit_node(kind: NodeKind, name: str, attrs: dict, summary: str = "") -> str | None:
            if not name.strip():
                return None
            node_id = make_node_id(ctx.family, kind, name, doc_id=doc_id)
            node = Node(
                id=node_id,
                kind=kind,
                name=name,
                qualified_name=name,
                doc_id=doc_id,
                location=Location(page=page_no),
                evidence=Evidence(
                    chunk_ids=source_chunk_ids,
                    pages=[page_no],
                    extractor=f"{self.name}@{self.version}",
                    raw_snippet=name,
                ),
                summary=summary[:240] or None,
                attrs={
                    **attrs,
                    "source": f"{self.name}@{self.version}",
                    "source_block_ids": source_block_ids,
                    "source_chunk_ids": source_chunk_ids,
                    "source_figure_id": fig_node.id,
                },
                hash=content_hash(f"{doc_id}:{kind.value}:{name}"),
            )
            nodes.append(node)
            name_to_id[name] = node_id
            edges.append(self._edge(
                src=node_id,
                dst=fig_node.id,
                kind=EdgeKind.ILLUSTRATED_BY,
                page_no=page_no,
                source_block_ids=source_block_ids,
                source_chunk_ids=source_chunk_ids,
                confidence=semantic.confidence,
                raw_snippet=fig_node.attrs.get("caption"),
            ))
            return node_id

        for item in semantic.modules:
            emit_node(
                NodeKind.MODULE,
                item.name,
                {"entity_type": "module", **item.model_dump()},
                item.description or item.role or "",
            )
        for item in semantic.signals:
            emit_node(
                NodeKind.SIGNAL,
                item.name,
                {"entity_type": "signal", **item.model_dump()},
                item.description,
            )
        for item in semantic.interfaces:
            emit_node(
                NodeKind.INTERFACE,
                item.name,
                {"entity_type": "interface", **item.model_dump()},
                item.description or item.protocol or "",
            )
        for item in semantic.clocks_resets:
            emit_node(
                NodeKind.MODULE,
                item.name,
                {"entity_type": "clock_reset", **item.model_dump()},
                item.description,
            )
        for item in semantic.address_regions:
            emit_node(
                NodeKind.MODULE,
                item.name,
                {"entity_type": "memory_map", **item.model_dump()},
                item.description or item.address or "",
            )

        for conn in semantic.connections:
            src_id = name_to_id.get(conn.source)
            dst_id = name_to_id.get(conn.target)
            if not src_id or not dst_id or src_id == dst_id:
                continue
            edges.append(self._edge(
                src=src_id,
                dst=dst_id,
                kind=self._edge_kind(conn.kind),
                page_no=page_no,
                source_block_ids=source_block_ids,
                source_chunk_ids=source_chunk_ids,
                confidence=semantic.confidence,
                raw_snippet=conn.description or conn.label or fig_node.attrs.get("caption"),
                attrs={"label": conn.label, "description": conn.description},
            ))

        fig_node.attrs["semantic_node_ids"] = [n.id for n in nodes]
        return {"nodes": nodes, "edges": edges}

    @staticmethod
    def _edge_kind(kind: str) -> EdgeKind:
        return {
            "connects_to": EdgeKind.CONNECTS_TO,
            "depends_on": EdgeKind.DEPENDS_ON,
            "controls": EdgeKind.CONTROLS,
            "references": EdgeKind.REFERENCES,
        }.get(kind, EdgeKind.CONNECTS_TO)

    def _edge(
        self,
        *,
        src: str,
        dst: str,
        kind: EdgeKind,
        page_no: int,
        source_block_ids: list[str],
        source_chunk_ids: list[str],
        confidence: float,
        raw_snippet: str | None,
        attrs: dict | None = None,
    ) -> Edge:
        return Edge(
            src=src,
            dst=dst,
            kind=kind,
            confidence=max(0.0, min(confidence, 1.0)),
            evidence=Evidence(
                pages=[page_no],
                extractor=f"{self.name}@{self.version}",
                raw_snippet=raw_snippet,
            ),
            attrs={
                "source": f"{self.name}@{self.version}",
                "source_block_ids": source_block_ids,
                "source_chunk_ids": source_chunk_ids,
                **(attrs or {}),
            },
        )
