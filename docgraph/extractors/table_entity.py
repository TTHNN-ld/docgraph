"""通用 TableEntityExtractor（M7-P3）—— L2 唯一入口。

替代旧 register.py / pin.py / timing.py 的三个专用 extractor。
所有实体抽取统一走 schema registry + 通用 LLM 抽取引擎。
VLM 整页兜底也集成在此（M6 能力迁移至此）。

分层契约（layered-architecture.md §2）：
- L2 是可选增强，不得成为唯一入口
- 所有产出节点带 evidence（源页/extractor/confidence）
- L2 抽取失败不影响 L0/L1 完整性
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from docgraph.core.ids import make_node_id
from docgraph.core.logger import get_logger
from docgraph.extractors.candidates import EntityCandidate, build_entity_candidates
from docgraph.extractors._vlm_backstop import page_needs_vlm_for, vlm_extract
from docgraph.extractors.base import ExtractContext
from docgraph.extractors.schema_registry import (
    ConstraintDef,
    EntitySchema,
    InterfaceDef,
    InterruptDef,
    MemoryMapDef,
    PhysicalConstraintDef,
    PinDef,
    RegisterDef,
    SignalDef,
    TimingParam,
    get_schema,
    schemas_for_doctype,
)
from docgraph.graph.schema import (
    BitFieldDef,
    BlockKind,
    DocType,
    Edge,
    EdgeKind,
    Evidence,
    ExtractResult,
    ExtractStats,
    Location,
    Node,
    NodeKind,
    ParsedDoc,
    TableData,
)

log = get_logger(__name__)


class TableEntityExtractor:
    """通用表格实体抽取器 —— L2 统一入口。

    用法：
        ex = TableEntityExtractor(schema_names=["register", "pin", "signal", ...])
        result = ex.extract(doc, ctx)

    流程：
    1. 扫描所有 L0 table block → 表头匹配 schema → LLM 抽取
    2. 文本候选：对 L0 未覆盖的页，用 LLM 从文本窗口抽取
    3. VLM 整页兜底：page.quality 命中 + 上述两轮都未抽出 → VLM 看图抽取
    """
    name = "table_entity"
    kinds = set()  # 动态（由 schema 决定）
    requires = {"section"}
    version = "0.2"

    # LLM 上限
    MAX_LLM_PER_SCHEMA = 20
    MAX_LLM_TOTAL = 300

    # VLM 上限（受环境变量 DOCGRAPH_VLM_PAGE_LIMIT 影响）
    DEFAULT_VLM_LIMIT = 8

    def __init__(
        self,
        schema_names: list[str] | None = None,
        doc_type: DocType | str | None = None,
    ) -> None:
        env_schemas = os.environ.get("DOCGRAPH_TABLE_ENTITY_SCHEMAS")
        if schema_names is not None:
            selected = list(schema_names)
            self._explicit_schemas = True
        elif env_schemas:
            selected = [s.strip() for s in env_schemas.split(",") if s.strip()]
            self._explicit_schemas = True
        elif doc_type is not None:
            # 按文档类型路由默认 schema 子集
            selected = schemas_for_doctype(doc_type)
            self._explicit_schemas = True
        else:
            # 未指定：延迟到 extract() 时按 parsed.metadata.type 路由
            selected = schemas_for_doctype(DocType.UNKNOWN)
            self._explicit_schemas = False
        self.schema_names = selected

    def _resolved_schemas(self, doc: ParsedDoc) -> list[tuple[str, EntitySchema]]:
        """解析当前应启用的 schema 列表。

        若构造时未显式指定 schema_names/doc_type，则按文档自身的
        metadata.type 路由（layered-architecture.md ADR-012 文档类型路由）。
        """
        if not self._explicit_schemas:
            dt = getattr(getattr(doc, "metadata", None), "type", None)
            if dt is not None:
                self.schema_names = schemas_for_doctype(dt)
        out: list[tuple[str, EntitySchema]] = []
        for sn in self.schema_names:
            s = get_schema(sn)
            if s is None:
                log.warning(f"[table_entity] unknown schema: {sn}")
                continue
            out.append((sn, s))
        return out

    def extract(self, doc: ParsedDoc, ctx: ExtractContext) -> ExtractResult:
        t0 = time.time()
        nodes: list[Node] = []
        edges: list[Edge] = []
        llm_calls = 0
        vlm_calls = 0
        seen: dict[str, set[str]] = {}  # schema → {name}

        # 按文档类型路由 schema 子集（未显式指定时用 parsed.metadata.type）
        schemas = self._resolved_schemas(doc)
        if not schemas:
            return ExtractResult(
                stats=ExtractStats(duration_s=round(time.time() - t0, 3)),
            )

        candidates = build_entity_candidates(doc)
        table_candidates = [
            c for c in candidates
            if c.kind == "table" and c.table is not None
        ]
        table_image_candidates = [
            c for c in candidates
            if c.kind == "table_image" and c.image_path
        ]
        text_candidates = [c for c in candidates if c.kind == "text"]
        page_image_candidates = [c for c in candidates if c.kind == "page_image" and c.image_path]
        pages_by_no = {p.page_no: p for p in doc.pages}
        hits_per_page: dict[int, int] = {}
        vlm_client = ctx.options.get("vlm_client") if ctx.options else None
        vlm_max = int(os.environ.get("DOCGRAPH_VLM_PAGE_LIMIT", self.DEFAULT_VLM_LIMIT))

        for sn, schema in schemas:
            schema_calls = 0
            seen.setdefault(sn, set())
            for candidate in table_candidates:
                if schema_calls >= self.MAX_LLM_PER_SCHEMA or llm_calls >= self.MAX_LLM_TOTAL:
                    break
                if not self._table_matches(candidate.table, schema):
                    continue
                if not self._table_has_cells(candidate.table):
                    continue
                result = None
                if sn == "register":
                    result = self._extract_registers_from_table(candidate.table)
                elif sn == "pin":
                    result = self._extract_pins_from_table(candidate.table)
                elif sn == "timing":
                    result = self._extract_timing_from_table(candidate.table)
                elif sn == "memory_map":
                    result = self._extract_memory_maps_from_table(candidate.table)
                elif sn == "interrupt":
                    result = self._extract_interrupts_from_table(candidate.table)
                elif sn == "signal":
                    result = self._extract_signals_from_table(candidate.table)
                elif sn == "interface":
                    result = self._extract_interfaces_from_table(candidate.table)
                elif sn == "constraint":
                    result = self._extract_constraints_from_table(candidate.table)
                elif sn == "physical_constraint":
                    result = self._extract_physical_constraints_from_table(candidate.table)
                if result is None and ctx.has_llm:
                    result = self._llm_extract(candidate.table, schema, sn, ctx)
                    llm_calls += 1
                schema_calls += 1
                if result is None:
                    continue
                n = self._materialize(
                    result, sn, schema, candidate.page, ctx, doc.doc_id,
                    seen[sn], hits_per_page,
                    source_block_ids=candidate.block_ids,
                    source_chunk_ids=candidate.source_chunk_ids,
                    candidate_id=candidate.id,
                )
                nodes.extend(n["nodes"])
                edges.extend(n["edges"])
                llm_calls = max(llm_calls, n["calls"])

            if vlm_client is not None and vlm_max > vlm_calls:
                for candidate in table_image_candidates:
                    if schema_calls >= self.MAX_LLM_PER_SCHEMA or vlm_calls >= vlm_max:
                        break
                    if candidate.table is not None and not self._table_matches(candidate.table, schema):
                        continue
                    result = self._vlm_extract_table_image(candidate, sn, schema, vlm_client)
                    vlm_calls += 1
                    schema_calls += 1
                    if result is None:
                        continue
                    n = self._materialize(
                        result, sn, schema, candidate.page, ctx, doc.doc_id,
                        seen[sn], hits_per_page,
                        source_block_ids=candidate.block_ids,
                        source_chunk_ids=candidate.source_chunk_ids,
                        candidate_id=candidate.id,
                    )
                    nodes.extend(n["nodes"])
                    edges.extend(n["edges"])

        # 1.5) 从文本段落中检测候选（对 find_tables 漏掉的表格降级）。
        # 不写规则正则，只做候选判断 + 送 LLM。
        for sn, schema in schemas:
            if not ctx.has_llm:
                continue
            schema_calls = 0
            seen.setdefault(sn, set())
            for candidate in text_candidates:
                if schema_calls >= self.MAX_LLM_PER_SCHEMA or llm_calls >= self.MAX_LLM_TOTAL:
                    break
                if hits_per_page.get(candidate.page, 0) > 0:
                    continue
                text = candidate.text
                if not text:
                    continue
                if not self._text_looks_like_entity(text, schema):
                    continue
                result = self._llm_extract_text(text, schema, sn, ctx)
                llm_calls += 1
                schema_calls += 1
                if result is None:
                    continue
                n = self._materialize(
                    result, sn, schema, candidate.page, ctx,
                    doc.doc_id, seen[sn], hits_per_page,
                    source_block_ids=candidate.block_ids,
                    source_chunk_ids=candidate.source_chunk_ids,
                    candidate_id=candidate.id,
                )
                nodes.extend(n["nodes"])
                edges.extend(n["edges"])

        # 2) VLM 整页兜底（对 L0 未抽到实体的页，用 VLM 看图）
        if vlm_client is not None and vlm_max > 0:
            vlm_reasons_per_entity: dict[str, set[str]] = {
                "register": {"register_with_table", "scan_like_no_text"},
                "pin":      {"pin_with_table", "scan_like_no_text"},
                "timing":   {"timing_with_table", "scan_like_no_text"},
            }
            for sn, schema in schemas:
                if vlm_calls >= vlm_max:
                    break
                trigger_reasons = vlm_reasons_per_entity.get(sn, set())
                if not trigger_reasons:
                    continue
                seen.setdefault(sn, set())
                for candidate in page_image_candidates:
                    if vlm_calls >= vlm_max:
                        break
                    page = pages_by_no.get(candidate.page)
                    if page is None or not page_needs_vlm_for(page, trigger_reasons):
                        continue
                    if hits_per_page.get(candidate.page, 0) > 0:
                        continue
                    regs = self._vlm_extract_page(page, sn, schema, vlm_client)
                    vlm_calls += 1
                    if not regs:
                        continue
                    for item in regs:
                        name = getattr(item, "name", "") or getattr(item, "symbol", "")
                        if name and name not in seen[sn]:
                            seen[sn].add(name)
                            n = self._node_from_model(item, sn, schema, page.page_no,
                                                       ctx, doc.doc_id,
                                                       source_block_ids=candidate.block_ids,
                                                       source_chunk_ids=candidate.source_chunk_ids,
                                                       candidate_id=candidate.id)
                            nodes.append(n["node"]); edges.extend(n["edges"])
                            if sn == "register" and hasattr(item, "bitfields"):
                                nodes.extend(n["bitfield_nodes"])
                                edges.extend(n["bitfield_edges"])

        return ExtractResult(
            nodes=nodes, edges=edges,
            stats=ExtractStats(
                nodes_emitted=len(nodes),
                edges_emitted=len(edges),
                duration_s=round(time.time() - t0, 3),
                llm_calls=llm_calls + vlm_calls,
            ),
        )

    # ------- 文本候选检测（find_tables 漏掉时的降级） -------

    @staticmethod
    def _page_text(page) -> str:
        """收集该页全部 paragraph/heading 文本。"""
        texts = []
        for b in (page.blocks or []):
            if b.kind in (BlockKind.PARAGRAPH, BlockKind.HEADING) and b.text:
                texts.append(b.text)
        return "\n".join(texts)

    @staticmethod
    def _text_looks_like_entity(text: str, schema: EntitySchema) -> bool:
        """判断文本是否看起来像包含某类实体。

        不抽结构、不写正则——只是用关键词做候选判断。
        errata 等段落型实体不强制要求 "table" caption。
        """
        lower = text.lower()
        if not lower or len(lower) < 40:
            return False
        # 负向排除：命中 SoC/地址映射/封装等词的段落不抽该实体
        if schema.negative_hints and any(x in lower for x in schema.negative_hints):
            return False
        hits = sum(1 for h in schema.table_header_hints if h.lower() in lower)
        # errata 等段落型 schema：命中 2 个 hint 即可（不依赖 table caption）
        if schema.kind == NodeKind.ERRATA:
            return hits >= 2
        # 表格型 schema：必须有 table caption 且 hint 密集
        if "table " not in lower and "表" not in lower:
            return False
        return hits >= 3

    def _llm_extract_text(self, text: str, schema: EntitySchema,
                          schema_name: str, ctx: ExtractContext) -> list | None:
        """把文本段落直接送 LLM 按 schema 抽取（不构造 TableData）。"""
        # 截取文本（最长 6000 字符避免成本过大）
        snippet = text[:6000]
        prompt = (
            f"你是芯片 spec 抽取器。下面是一段文本，可能包含{schema.description}信息。\n"
            f"请全量抽出**所有**符合{schema.description}的条目为 JSON。\n"
            f"如果这段文本中没有相关内容，返回空列表。\n\n"
            f"文本：\n```\n{snippet}\n```"
        )
        try:
            result = ctx.llm_client.json(
                prompt, schema=schema.list_wrapper,
                tier="balanced", max_tokens=4096,
                extractor=f"table_entity_text:{schema_name}",
            )
        except Exception as e:
            log.warning(f"[table_entity] text {schema_name} LLM fail: {str(e)[:120]}")
            return None
        if not result:
            return None
        items = getattr(result, schema.items_field, None)
        if not items:
            return None
        return [item for item in items if isinstance(item, schema.target_model)]

    # ------- LLM 抽取 -------

    def _llm_extract(self, table: TableData, schema: EntitySchema,
                     schema_name: str, ctx: ExtractContext) -> list | None:
        prompt = schema.prompt_template.format(
            table_text=self._table_text(table)
        )
        try:
            result = ctx.llm_client.json(
                prompt, schema=schema.list_wrapper,
                tier="balanced", max_tokens=4096,
                extractor=f"table_entity:{schema_name}",
            )
        except Exception as e:
            log.warning(f"[table_entity] {schema_name} LLM fail: {str(e)[:120]}")
            return None
        if not result:
            return None
        items = getattr(result, schema.items_field, None)
        if not items:
            return None
        return [item for item in items if isinstance(item, schema.target_model)]

    # ------- VLM 兜底 -------

    def _vlm_extract_page(self, page, schema_name: str, schema: EntitySchema,
                          vlm_client) -> list:
        prompt = (
            f"你看到的是芯片 spec 文档的一页（已渲染为图像）。\n"
            f"请把这一页包含的**所有{schema.description}**完整抽成 JSON。\n"
            f"类型：{schema_name}\n"
            f"如果这一页没有相关内容，返回空列表。\n"
        )
        result = vlm_extract(
            vlm_client=vlm_client,
            image_path=page.rendered_image_path,
            prompt=prompt,
            schema=schema.list_wrapper,
            extractor=f"table_entity:{schema_name}",
            max_tokens=4096,
        )
        if result is None:
            return []
        items = getattr(result, schema.items_field, None)
        if not items:
            return []
        return [item for item in items if isinstance(item, schema.target_model)]

    def _vlm_extract_table_image(self, candidate: EntityCandidate, schema_name: str, schema: EntitySchema,
                                 vlm_client) -> list:
        if not candidate.image_path or not Path(candidate.image_path).is_file():
            return []
        caption = candidate.table.caption if candidate.table else None
        prompt = (
            f"你看到的是芯片 spec 文档中的一张表格裁剪图。\n"
            f"请先按视觉内容识别完整表格，再抽出**所有{schema.description}**为 JSON。\n"
            f"类型：{schema_name}\n"
            f"页码：{candidate.page}\n"
            f"表题：{caption or ''}\n"
            f"候选：{candidate.id}\n"
            f"如果这张表中没有相关内容，返回空列表。"
        )
        result = vlm_extract(
            vlm_client=vlm_client,
            image_path=candidate.image_path,
            prompt=prompt,
            schema=schema.list_wrapper,
            extractor=f"table_entity_table_image:{schema_name}",
            max_tokens=4096,
        )
        if result is None:
            return []
        items = getattr(result, schema.items_field, None)
        if not items:
            return []
        return [item for item in items if isinstance(item, schema.target_model)]

    # ------- 物化 -------

    def _materialize(self, items: list, schema_name: str, schema: EntitySchema,
                     page: int, ctx: ExtractContext, doc_id: str,
                     seen: set[str], hits_per_page: dict,
                     source_block_ids: list[str] | None = None,
                     source_chunk_ids: list[str] | None = None,
                     candidate_id: str | None = None) -> dict:
        nodes: list[Node] = []
        edges: list[Edge] = []
        calls = 0
        for item in items:
            name = getattr(item, "name", "") or getattr(item, "symbol", "")
            if not name or name in seen:
                continue
            seen.add(name)
            result = self._node_from_model(
                item, schema_name, schema, page, ctx, doc_id,
                source_block_ids=source_block_ids or [],
                source_chunk_ids=source_chunk_ids or [],
                candidate_id=candidate_id,
            )
            nodes.append(result["node"])
            nodes.extend(result["bitfield_nodes"])
            edges.extend(result["edges"])
            edges.extend(result["bitfield_edges"])
            hits_per_page[page] = hits_per_page.get(page, 0) + 1
        return {"nodes": nodes, "edges": edges, "calls": calls}

    def _node_from_model(self, item, schema_name: str, schema: EntitySchema,
                         page: int, ctx: ExtractContext, doc_id: str,
                         source_block_ids: list[str] | None = None,
                         source_chunk_ids: list[str] | None = None,
                         candidate_id: str | None = None) -> dict:
        name = getattr(item, "name", "") or getattr(item, "symbol", "")
        attrs = self._dump_attrs(item)
        if schema.kind in (NodeKind.SIGNAL, NodeKind.INTERFACE):
            attrs["width"] = self._normalize_width(attrs.get("width"))
        attrs["source"] = f"table_entity:{schema_name}"
        attrs["source_block_ids"] = source_block_ids or []
        attrs["source_chunk_ids"] = source_chunk_ids or []
        if schema_name in {"constraint", "physical_constraint"}:
            attrs["entity_type"] = schema_name
        if candidate_id:
            attrs["candidate_id"] = candidate_id
        attrs["schema_name"] = schema_name
        node = Node(
            id=make_node_id(ctx.family, schema.kind, name, doc_id=doc_id),
            kind=schema.kind, name=name, qualified_name=name,
            doc_id=doc_id, location=Location(page=page),
            evidence=Evidence(
                chunk_ids=source_chunk_ids or [],
                pages=[page],
                extractor=f"table_entity:{schema_name}",
                raw_snippet=name,
            ),
            summary=getattr(item, "description", "") or "",
            attrs=attrs,
        )
        edges: list[Edge] = []
        bf_nodes: list[Node] = []
        bf_edges: list[Edge] = []
        if schema_name == "register" and hasattr(item, "bitfields"):
            selected_bitfields, dropped_bitfields = self._select_non_overlapping_bitfields(item.bitfields or [])
            if dropped_bitfields:
                node.attrs["dropped_bitfields"] = dropped_bitfields
            for bf in selected_bitfields:
                bf_name = getattr(bf, "name", "")
                if not bf_name:
                    continue
                bf_n = Node(
                    id=make_node_id(ctx.family, NodeKind.BITFIELD,
                                    f"{name}.{bf_name}", doc_id=doc_id),
                    kind=NodeKind.BITFIELD, name=bf_name,
                    qualified_name=f"{name}.{bf_name}",
                    doc_id=doc_id, location=Location(page=page),
                    evidence=Evidence(
                        chunk_ids=source_chunk_ids or [],
                        pages=[page],
                        extractor=f"table_entity:{schema_name}",
                        raw_snippet=f"{name}.{bf_name}",
                    ),
                    attrs={
                        "register_id": node.id,
                        "bit_high": getattr(bf, "bit_high", 0),
                        "bit_low": getattr(bf, "bit_low", 0),
                        "access": getattr(bf, "access", None),
                        "reset": getattr(bf, "reset", None),
                        "description": getattr(bf, "description", ""),
                        "source": f"table_entity:{schema_name}",
                        "source_block_ids": source_block_ids or [],
                        "source_chunk_ids": source_chunk_ids or [],
                        "schema_name": schema_name,
                        **({"candidate_id": candidate_id} if candidate_id else {}),
                    },
                )
                bf_nodes.append(bf_n)
                bf_edges.append(Edge(
                    src=node.id, dst=bf_n.id, kind=EdgeKind.HAS_BITFIELD,
                    confidence=schema.min_confidence,
                    evidence=Evidence(
                        pages=[page],
                        extractor=f"table_entity:{schema_name}",
                    ),
                ))
            node.attrs["bitfield_ids"] = [n.id for n in bf_nodes]
        return {"node": node, "edges": edges, "bitfield_nodes": bf_nodes, "bitfield_edges": bf_edges}

    # ------- helpers -------

    @staticmethod
    def _table_matches(table: TableData | None, schema: EntitySchema) -> bool:
        """判断一张 L0 表是否像某 schema 的实体表。

        双语（中英）词表，大小写不敏感。register 有 negative 排除
        地址映射/中断表，避免借错 schema。
        """
        if table is None or not (table.headers or table.caption):
            return False
        hdr_lower = " ".join(h.lower() for h in (table.headers or []))
        caption_lower = (table.caption or "").lower()
        pool = hdr_lower + " " + caption_lower
        if schema.target_model is RegisterDef:
            negative = (
                "address map", "memory map", "base address", "地址映射",
                "基地址", "中断", "interrupt", "irq", "signal list",
                "pin list", "管脚表",
            )
            if any(x in pool for x in negative):
                return False
            strong = (
                "reg name", "register", "field", "bit", "bits", "msb", "lsb",
                "swaccess", "hwaccess", "access", "reset", "default", "offset",
                "寄存器", "字段", "位域", "复位", "访问", "偏移",
            )
            return sum(1 for x in strong if x in pool) >= 2
        # 非 register schema：先过 negative 排除，再按 hint 计数
        if schema.negative_hints:
            if any(x in pool for x in schema.negative_hints):
                return False
        if schema.kind == NodeKind.SIGNAL:
            has_interface_group = "interface group" in pool or "接口组" in pool or "外围接口" in pool
            has_direction = "direction" in pool or "方向" in pool
            if has_interface_group and has_direction:
                return True
        if schema.kind == NodeKind.INTERRUPT:
            return TableEntityExtractor._has_interrupt_definition_columns(table)
        if schema.kind == NodeKind.INTERFACE:
            return TableEntityExtractor._has_interface_definition_columns(table)
        hits = sum(1 for h in schema.table_header_hints if h.lower() in pool)
        if not (table.headers or table.rows or table.html):
            return hits >= 1
        return hits >= 2

    @staticmethod
    def _has_interface_definition_columns(table: TableData | None) -> bool:
        """Return true for bus/interface definitions, not grouping or map tables."""
        if table is None or not table.headers:
            return False
        headers = [TableEntityExtractor._norm_header(h) for h in table.headers]
        caption = TableEntityExtractor._norm_header(table.caption or "")
        pool = " ".join([*headers, caption])
        if not any(token in pool for token in ("interface", "bus", "protocol", "接口", "总线", "协议")):
            return False

        address_map_tokens = (
            "address", "base", "offset", "size", "range", "memory map",
            "address map", "noc master", "noc slave", "地址", "基地址",
            "偏移", "大小", "范围", "地址映射",
        )
        if any(token in pool for token in address_map_tokens):
            return False

        has_group_header = any(h in {"interface group", "接口组", "外围接口"} for h in headers)
        has_direction_only = any(h in {"direction", "dir", "方向"} for h in headers)
        has_description = any(h in {"description", "desc", "function", "说明", "描述", "功能"} for h in headers)
        has_protocol_col = any(h in {"protocol", "bus", "协议", "总线"} for h in headers)
        has_width_col = any("width" in h or "位宽" in h or "宽度" in h for h in headers)
        has_role_col = any(h in {"role", "master", "slave", "角色", "主", "从"} for h in headers)
        has_name_col = any(h in {"name", "interface name", "interface", "接口名", "接口"} for h in headers)

        if has_group_header and not has_protocol_col and not has_width_col and not has_role_col:
            return False
        if has_direction_only and has_description and not has_protocol_col and not has_width_col and not has_name_col:
            return False
        return bool((has_name_col or has_protocol_col) and (has_protocol_col or has_width_col or has_role_col))

    @staticmethod
    def _has_interrupt_definition_columns(table: TableData | None) -> bool:
        """Return true only for IRQ/MSI definition lists, not feature summaries.

        Processor datasheets often contain high-level capability tables whose
        first header says "Nested vectored interrupt controller" or
        "Interrupt priority levels". Those tables mention interrupts but do not
        define interrupt entities. L2 should only materialize an interrupt when
        the table exposes a source/name/vector/number style column.
        """
        if table is None or not table.headers:
            return False
        headers = [TableEntityExtractor._norm_header(h) for h in table.headers]
        caption = TableEntityExtractor._norm_header(table.caption or "")
        pool = " ".join(headers + [caption])
        if not any(token in pool for token in ("interrupt", "irq", "msi", "vector", "中断", "向量")):
            return False

        strong_columns = (
            "irq src", "irq_src", "summary_irq", "interrupt source",
            "interrupt name", "irq name", "irq number", "vector number",
            "msi vector", "msi-x vector", "source signal", "irq signal",
            "中断源", "中断号", "中断信号", "向量号",
        )
        if any(any(token in h for token in strong_columns) for h in headers):
            return True

        has_sourceish = any(h in {"interrupt", "irq", "msi", "vector", "signal", "name", "中断", "信号"} for h in headers)
        has_structural = any(
            any(token in h for token in ("number", "priority", "type", "description", "desc", "位宽", "类型", "描述"))
            for h in headers
        )
        caption_declares_list = any(
            token in caption
            for token in ("interrupt source", "interrupt list", "irq list", "msi", "中断源", "中断列表")
        )
        return bool((caption_declares_list or has_structural) and has_sourceish)

    @staticmethod
    def _table_has_cells(table: TableData | None) -> bool:
        return bool(table and (table.headers or table.rows or table.html))

    @staticmethod
    def _table_text(table: TableData) -> str:
        out: list[str] = []
        if table.caption:
            out.append(f"Caption: {table.caption}")
        if table.headers:
            out.append("| " + " | ".join(table.headers) + " |")
            out.append("|" + "|".join(["---"] * len(table.headers)) + "|")
        for row in table.rows:
            out.append("| " + " | ".join(str(c) for c in row) + " |")
        return "\n".join(out)

    @staticmethod
    def _dump_attrs(item) -> dict:
        if isinstance(item, dict):
            return dict(item)
        try:
            return dict(item.model_dump() if hasattr(item, "model_dump") else item.__dict__)
        except Exception:
            return {}

    # ------- deterministic table normalizers -------

    @classmethod
    def _extract_registers_from_table(cls, table: TableData | None) -> list[RegisterDef] | None:
        """Deterministically parse common register field tables.

        This is deliberately schema/column-driven, not document-specific. It
        handles the two dominant chip-spec forms:
        - one table with ``Reg name`` + ``Field`` + ``Msb``/``Lsb`` columns
        - per-register tables whose caption/header says ``Fields for Register``
          and whose bit range is in a ``Bits`` column/header.

        Ambiguous tables return ``None`` so the schema-guided LLM path can still
        act as fallback.
        """
        if table is None or not table.rows:
            return None
        headers = [cls._norm_header(h) for h in (table.headers or [])]
        raw_headers = list(table.headers or [])
        if not headers:
            return None

        reg_col = cls._find_col(headers, ("reg name", "register name", "register", "reg", "寄存器名", "寄存器"))
        field_col = cls._find_col(headers, ("field", "bit field", "bitfield", "name", "字段", "位域"))
        msb_col = cls._find_col(headers, ("msb", "bit high", "high", "bit_high"))
        lsb_col = cls._find_col(headers, ("lsb", "bit low", "low", "bit_low"))
        bits_col = cls._find_col(headers, ("bits", "bit", "位", "位段"))
        access_col = cls._find_col(headers, ("swaccess", "sw access", "software access", "access", "memory access", "访问"))
        reset_col = cls._find_col(headers, ("default", "reset", "reset value", "复位", "默认"))
        desc_col = cls._find_col(headers, ("description", "desc", "function", "说明", "描述", "功能"))
        offset_col = cls._find_col(headers, ("offset", "address offset", "addr offset", "address", "base address", "偏移", "地址"))
        width_col = cls._find_col(headers, ("width", "reg width", "位宽"))

        caption_reg = cls._register_name_from_caption(table.caption or "")
        bits_from_header = cls._bit_range_from_text(" ".join(raw_headers))
        is_field_table = field_col is not None and (
            (msb_col is not None and lsb_col is not None)
            or bits_col is not None
            or bits_from_header is not None
        )
        if not is_field_table:
            return None
        if reg_col is None and not caption_reg:
            return None

        grouped: dict[str, dict] = {}
        current_reg_name = caption_reg
        known_bit_ranges: dict[int, tuple[int, int]] = {}
        for row in table.rows:
            cells = [str(c or "").strip() for c in row]
            field_name = cls._clean_entity_name(cls._cell(cells, field_col))
            embedded_reg = cls._register_name_from_cells(cells, skip_indexes={reg_col, field_col})
            raw_reg_name = cls._cell(cells, reg_col)
            if embedded_reg:
                reg_name = embedded_reg
            elif raw_reg_name and not cls._looks_like_continuation_reg_cell(raw_reg_name):
                reg_name = raw_reg_name
            elif current_reg_name:
                reg_name = current_reg_name
            else:
                reg_name = caption_reg
            reg_name = cls._clean_entity_name(reg_name)
            if not reg_name:
                continue
            if not field_name:
                continue
            current_reg_name = reg_name

            bit_range = None
            if msb_col is not None and lsb_col is not None:
                bit_range = cls._parse_bit_pair(cls._cell(cells, msb_col), cls._cell(cells, lsb_col))
            if bit_range is None and bits_col is not None:
                bit_range = cls._bit_range_from_text(cls._cell(cells, bits_col))
            if bit_range is None:
                bit_range = bits_from_header
            if bit_range is None:
                bit_range = cls._fallback_bit_range_from_field(field_name, known_bit_ranges)
            if bit_range is None:
                continue
            bit_high, bit_low = bit_range
            suffix = cls._bit_suffix(field_name)
            if suffix is not None:
                known_bit_ranges[suffix] = bit_range

            entry = grouped.setdefault(reg_name, {
                "name": reg_name,
                "address": None,
                "offset": None,
                "width": 32,
                "access": None,
                "reset_value": None,
                "description": "",
                "bitfields": [],
            })
            offset = cls._cell(cells, offset_col)
            if offset and entry["offset"] is None:
                if re.search(r"0x[0-9a-fA-F]+|\b\d+\s*[KMGTP]?B?\b", offset):
                    entry["offset"] = offset
            width = cls._parse_intish(cls._cell(cells, width_col))
            if width and width > 0:
                entry["width"] = width

            access = cls._normalize_access(cls._cell(cells, access_col))
            reset = cls._cell(cells, reset_col) or None
            desc = cls._cell(cells, desc_col)
            entry["bitfields"].append(BitFieldDef(
                name=field_name,
                bit_high=bit_high,
                bit_low=bit_low,
                access=access,
                reset=reset,
                description=desc,
            ))

        registers: list[RegisterDef] = []
        for item in grouped.values():
            selected, _dropped = cls._select_non_overlapping_bitfields(item["bitfields"])
            if not selected:
                continue
            max_bit = max(int(bf.bit_high) for bf in selected)
            width = int(item["width"] or 32)
            while max_bit >= width:
                width *= 2
            registers.append(RegisterDef(
                name=item["name"],
                address=item["address"],
                offset=item["offset"],
                width=width,
                access=item["access"],
                reset_value=item["reset_value"],
                description=item["description"],
                bitfields=selected,
            ))
        return registers or None

    @classmethod
    def _extract_memory_maps_from_table(cls, table: TableData | None) -> list[MemoryMapDef] | None:
        """Deterministically parse address/base/offset/size tables.

        Common in chip specs as:
        - Interface / Base Address
        - NoC Slave / Offset / Size / Description
        - Access Region / Access Address
        """
        if table is None or not table.rows:
            return None
        headers = [cls._norm_header(h) for h in (table.headers or [])]
        if not headers:
            return None
        name_col = cls._find_col(headers, (
            "name", "region", "target", "slave", "noc slave", "interface",
            "访问区域", "区域", "目标", "从设备", "接口",
        ))
        address_col = cls._find_col(headers, (
            "base address", "address", "offset", "addr", "访问地址", "基地址", "地址", "偏移",
        ))
        size_col = cls._find_col(headers, ("size", "range", "大小", "范围"))
        desc_col = cls._find_col(headers, ("description", "desc", "说明", "描述", "功能"))
        if address_col is None:
            return None
        if name_col == address_col and address_col + 1 < len(headers):
            address_col = address_col + 1

        entries: list[MemoryMapDef] = []
        for row in table.rows:
            cells = [str(c or "").strip() for c in row]
            address = cls._cell(cells, address_col)
            if not cls._looks_like_address_locator(address):
                continue
            name = cls._clean_display_name(cls._cell(cells, name_col)) or cls._clean_display_name(address)
            if not name or cls._is_header_echo(name, headers):
                continue
            entries.append(MemoryMapDef(
                name=name,
                address=address,
                size=cls._cell(cells, size_col) or None,
                description=cls._cell(cells, desc_col),
            ))
        return entries or None

    @classmethod
    def _extract_interrupts_from_table(cls, table: TableData | None) -> list[InterruptDef] | None:
        """Deterministically parse IRQ/MSI/vector tables."""
        if table is None or not table.rows:
            return None
        headers = [cls._norm_header(h) for h in (table.headers or [])]
        if not cls._has_interrupt_definition_columns(table):
            return None
        name_col = cls._find_col(headers, (
            "irq src signal", "irq src", "irq_src信号", "irq_src 信号",
            "summary_irq 信号", "interrupt", "irq", "vector", "signal", "name", "中断", "信号",
        ))
        type_col = cls._find_col(headers, ("type", "category", "类型"))
        number_col = cls._find_col(headers, ("number", "irq number", "vector number", "中断号", "编号"))
        desc_col = cls._find_col(headers, ("description", "desc", "说明", "描述", "功能"))
        if name_col is None:
            return None
        out: list[InterruptDef] = []
        for row in table.rows:
            cells = [str(c or "").strip() for c in row]
            name = cls._clean_display_name(cls._cell(cells, name_col))
            if not name or cls._is_header_echo(name, headers):
                continue
            out.append(InterruptDef(
                name=name,
                number=cls._cell(cells, number_col) or None,
                type=cls._cell(cells, type_col) or None,
                description=cls._cell(cells, desc_col),
            ))
        return out or None

    @classmethod
    def _extract_signals_from_table(cls, table: TableData | None) -> list[SignalDef] | None:
        """Deterministically parse signal/port list tables."""
        if table is None or not table.rows:
            return None
        headers = [cls._norm_header(h) for h in (table.headers or [])]
        pool = " ".join(headers + [cls._norm_header(table.caption or "")])
        if any(token in pool for token in ("interrupt", "irq", "中断", "irq_src")):
            return None
        name_col = cls._find_col(headers, ("signal", "port", "pin name", "name", "信号", "端口"))
        width_col = cls._find_col(headers, ("width", "bit width", "bits", "位宽", "宽度"))
        direction_col = cls._find_col(headers, ("direction", "dir", "i/o", "io", "方向"))
        desc_col = cls._find_col(headers, ("description", "desc", "function", "说明", "描述", "功能"))
        if name_col is None:
            name_col = cls._infer_signal_name_col(table, headers, direction_col, width_col, desc_col)
        if name_col is None:
            return None
        out: list[SignalDef] = []
        for row in table.rows:
            cells = [str(c or "").strip() for c in row]
            name = cls._clean_display_name(cls._cell(cells, name_col))
            if (
                not name
                or cls._is_header_echo(name, headers)
                or not cls._looks_like_signal_name(name)
            ):
                continue
            out.append(SignalDef(
                name=name,
                direction=cls._normalize_direction(cls._cell(cells, direction_col)),
                width=cls._normalize_width(cls._cell(cells, width_col)),
                description=cls._cell(cells, desc_col),
            ))
        return out or None

    @classmethod
    def _extract_pins_from_table(cls, table: TableData | None) -> list[PinDef] | None:
        """Deterministically parse physical pin / ball / package tables.

        标准 pin 表列：Pin Name / Pin No / Direction / Type / Function / Voltage。
        只在表头明确含 pin 关键词时解析，避免把信号表误判为 pin 表。
        """
        if table is None or not table.rows:
            return None
        headers = [cls._norm_header(h) for h in (table.headers or [])]
        pool = " ".join(headers + [cls._norm_header(table.caption or "")])
        # 必须有 pin/ball/管脚/引脚/package 之一，否则不是物理 pin 表
        if not any(token in pool for token in ("pin", "ball", "管脚", "引脚", "package", "封装")):
            return None
        if "pin list" in pool or "管脚表" in pool:
            return None
        name_col = cls._find_col(headers, ("pin name", "pin", "ball name", "name", "管脚名", "引脚名"))
        no_col = cls._find_col(headers, ("pin no", "pin number", "ball no", "no", "number", "编号", "管脚号"))
        direction_col = cls._find_col(headers, ("direction", "dir", "type", "i/o", "io", "方向", "类型"))
        voltage_col = cls._find_col(headers, ("voltage", "power", "电压", "电源"))
        desc_col = cls._find_col(headers, ("description", "desc", "function", "功能", "描述", "说明"))
        if name_col is None and no_col is None:
            return None
        if name_col is None:
            name_col = no_col
        out: list[PinDef] = []
        for row in table.rows:
            cells = [str(c or "").strip() for c in row]
            name = cls._clean_display_name(cls._cell(cells, name_col))
            if not name or cls._is_header_echo(name, headers):
                continue
            if name_col == no_col and not any(c.isalpha() for c in name):
                continue
            out.append(PinDef(
                name=name,
                direction=cls._normalize_direction(cls._cell(cells, direction_col)),
                pin_no=cls._cell(cells, no_col) if no_col != name_col else None,
                voltage=cls._cell(cells, voltage_col) or None,
                description=cls._cell(cells, desc_col),
            ))
        return out or None

    @classmethod
    def _extract_timing_from_table(cls, table: TableData | None) -> list[TimingParam] | None:
        """Deterministically parse timing / electrical parameter tables.

        标准时序表列：Symbol / Min / Typ / Max / Unit / Condition。
        要求同时有 symbol 列和 min/max/typ 之一才算时序表。
        """
        if table is None or not table.rows:
            return None
        headers = [cls._norm_header(h) for h in (table.headers or [])]
        symbol_col = cls._find_col(headers, ("symbol", "parameter", "name", "param", "参数", "符号"))
        min_col = cls._find_col(headers, ("min", "minimum", "最小"))
        typ_col = cls._find_col(headers, ("typ", "typical", "典型"))
        max_col = cls._find_col(headers, ("max", "maximum", "最大"))
        unit_col = cls._find_col(headers, ("unit", "units", "单位"))
        cond_col = cls._find_col(headers, ("condition", "test condition", "note", "条件", "测试条件"))
        if symbol_col is None:
            return None
        if min_col is None and typ_col is None and max_col is None:
            return None
        out: list[TimingParam] = []
        for row in table.rows:
            cells = [str(c or "").strip() for c in row]
            symbol = cls._clean_display_name(cls._cell(cells, symbol_col))
            if not symbol or cls._is_header_echo(symbol, headers):
                continue
            if symbol.replace(".", "").replace("/", "").isdigit():
                continue
            out.append(TimingParam(
                symbol=symbol,
                min=cls._cell(cells, min_col) or None,
                typ=cls._cell(cells, typ_col) or None,
                max=cls._cell(cells, max_col) or None,
                unit=cls._cell(cells, unit_col) or None,
                condition=cls._cell(cells, cond_col),
            ))
        return out or None

    @classmethod
    def _infer_signal_name_col(
        cls,
        table: TableData,
        headers: list[str],
        direction_col: int | None,
        width_col: int | None,
        desc_col: int | None,
    ) -> int | None:
        """Infer signal-name column for interface-list tables.

        Many IP specs use columns such as "Interface Group / Direction /
        Description" where the first column contains both group labels and real
        signal names. This is common in integration guides and subsystem specs.
        """
        pool = " ".join(headers + [cls._norm_header(table.caption or "")])
        if direction_col is None and width_col is None:
            return None
        if not any(token in pool for token in ("interface", "port", "signal", "接口", "端口", "信号", "方向")):
            return None
        excluded = {idx for idx in (direction_col, width_col, desc_col) if idx is not None}
        col_count = table.n_cols or max((len(r) for r in table.rows), default=0)
        best_col: int | None = None
        best_score = 0
        for idx in range(col_count):
            if idx in excluded:
                continue
            score = 0
            for row in table.rows:
                cells = [str(c or "").strip() for c in row]
                value = cls._cell(cells, idx)
                if cls._looks_like_signal_name(value) and not cls._is_header_echo(value, headers):
                    score += 1
            if score > best_score:
                best_col = idx
                best_score = score
        return best_col if best_score >= 2 else None

    @classmethod
    def _extract_interfaces_from_table(cls, table: TableData | None) -> list[InterfaceDef] | None:
        """Deterministically parse high-level bus/protocol interface tables."""
        if table is None or not table.rows:
            return None
        headers = [cls._norm_header(h) for h in (table.headers or [])]
        if not cls._has_interface_definition_columns(table):
            return None
        name_col = cls._find_col(headers, ("name", "interface name", "interface", "接口名", "接口"))
        protocol_col = cls._find_col(headers, ("protocol", "bus", "协议", "总线"))
        direction_col = cls._find_col(headers, ("direction", "role", "master", "slave", "方向", "角色"))
        width_col = cls._find_col(headers, ("width", "data width", "位宽", "宽度"))
        desc_col = cls._find_col(headers, ("description", "desc", "function", "说明", "描述", "功能"))
        if name_col is None:
            name_col = protocol_col
        if name_col is None:
            return None
        out: list[InterfaceDef] = []
        for row in table.rows:
            cells = [str(c or "").strip() for c in row]
            name = cls._clean_display_name(cls._cell(cells, name_col))
            if not name or cls._is_header_echo(name, headers):
                continue
            out.append(InterfaceDef(
                name=name,
                protocol=cls._cell(cells, protocol_col) if protocol_col != name_col else None,
                direction=cls._normalize_direction(cls._cell(cells, direction_col)),
                width=cls._normalize_width(cls._cell(cells, width_col)),
                description=cls._cell(cells, desc_col),
            ))
        return out or None

    @classmethod
    def _extract_constraints_from_table(cls, table: TableData | None) -> list[ConstraintDef] | None:
        """Deterministically parse backend timing/design constraint tables."""
        if table is None or not table.rows:
            return None
        headers = [cls._norm_header(h) for h in (table.headers or [])]
        pool = " ".join(headers + [cls._norm_header(table.caption or "")])
        if not any(token in pool for token in (
            "constraint", "sdc", "sta", "setup", "hold", "uncertainty",
            "transition", "fanout", "false path", "multicycle", "约束", "时序",
        )):
            return None
        name_col = cls._find_col(headers, ("name", "id", "constraint", "constraint name", "约束", "名称", "编号"))
        target_col = cls._find_col(headers, ("target", "object", "path", "clock", "net", "pin", "目标", "对象", "路径", "时钟", "网络"))
        type_col = cls._find_col(headers, ("type", "constraint type", "kind", "category", "类型", "类别"))
        value_col = cls._find_col(headers, ("value", "limit", "max", "min", "取值", "值", "限制", "最大", "最小"))
        unit_col = cls._find_col(headers, ("unit", "单位"))
        condition_col = cls._find_col(headers, ("condition", "corner", "mode", "scenario", "条件", "工况", "模式"))
        desc_col = cls._find_col(headers, ("description", "desc", "notes", "说明", "描述", "备注"))
        if name_col is None and type_col is None:
            return None
        out: list[ConstraintDef] = []
        for row in table.rows:
            cells = [str(c or "").strip() for c in row]
            raw_name = cls._clean_display_name(cls._cell(cells, name_col))
            constraint_type = cls._clean_display_name(cls._cell(cells, type_col))
            target = cls._clean_display_name(cls._cell(cells, target_col))
            if not (raw_name or constraint_type or target):
                continue
            if cls._is_header_echo(raw_name, headers):
                continue
            name = raw_name or " ".join(x for x in [constraint_type, target] if x).strip()
            out.append(ConstraintDef(
                name=name,
                target=target or None,
                constraint_type=constraint_type or None,
                value=cls._cell(cells, value_col) or None,
                unit=cls._cell(cells, unit_col) or None,
                condition=cls._cell(cells, condition_col),
                description=cls._cell(cells, desc_col),
            ))
        return out or None

    @classmethod
    def _extract_physical_constraints_from_table(cls, table: TableData | None) -> list[PhysicalConstraintDef] | None:
        """Deterministically parse backend floorplan/placement/routing constraint tables."""
        if table is None or not table.rows:
            return None
        headers = [cls._norm_header(h) for h in (table.headers or [])]
        pool = " ".join(headers + [cls._norm_header(table.caption or "")])
        if not any(token in pool for token in (
            "floorplan", "placement", "route", "routing", "layer", "region",
            "macro", "keepout", "blockage", "spacing", "density",
            "布局", "摆放", "布线", "区域", "宏", "禁布", "阻塞", "间距", "密度",
        )):
            return None
        name_col = cls._find_col(headers, ("name", "id", "constraint", "rule", "名称", "编号", "规则"))
        object_col = cls._find_col(headers, ("object", "target", "macro", "net", "cell", "对象", "目标", "宏", "网络", "单元"))
        type_col = cls._find_col(headers, ("type", "constraint type", "category", "kind", "类型", "类别"))
        value_col = cls._find_col(headers, ("value", "limit", "width", "spacing", "density", "utilization", "取值", "值", "线宽", "间距", "密度", "利用率"))
        layer_col = cls._find_col(headers, ("layer", "metal", "层", "金属层"))
        region_col = cls._find_col(headers, ("region", "area", "voltage area", "domain", "区域", "电压区域", "域"))
        desc_col = cls._find_col(headers, ("description", "desc", "notes", "说明", "描述", "备注"))
        if name_col is None and type_col is None:
            return None
        out: list[PhysicalConstraintDef] = []
        for row in table.rows:
            cells = [str(c or "").strip() for c in row]
            raw_name = cls._clean_display_name(cls._cell(cells, name_col))
            constraint_type = cls._clean_display_name(cls._cell(cells, type_col))
            obj = cls._clean_display_name(cls._cell(cells, object_col))
            if not (raw_name or constraint_type or obj):
                continue
            if cls._is_header_echo(raw_name, headers):
                continue
            name = raw_name or " ".join(x for x in [constraint_type, obj] if x).strip()
            out.append(PhysicalConstraintDef(
                name=name,
                object=obj or None,
                constraint_type=constraint_type or None,
                value=cls._cell(cells, value_col) or None,
                layer=cls._cell(cells, layer_col) or None,
                region=cls._cell(cells, region_col) or None,
                description=cls._cell(cells, desc_col),
            ))
        return out or None

    @staticmethod
    def _norm_header(value: str) -> str:
        text = re.sub(r"\s+", " ", str(value or "").strip().lower())
        text = text.replace("_", " ")
        return text

    @staticmethod
    def _find_col(headers: list[str], names: tuple[str, ...]) -> int | None:
        wanted = {n.lower() for n in names}
        for idx, header in enumerate(headers):
            if header in wanted:
                return idx
        for idx, header in enumerate(headers):
            if any(n in header for n in wanted):
                return idx
        return None

    @staticmethod
    def _cell(cells: list[str], idx: int | None) -> str:
        if idx is None or idx < 0 or idx >= len(cells):
            return ""
        return cells[idx].strip()

    @staticmethod
    def _clean_entity_name(value: str) -> str:
        text = re.sub(r"\s+", "_", str(value or "").strip())
        text = text.strip("_")
        return text

    @staticmethod
    def _clean_display_name(value: str) -> str:
        text = re.sub(r"\s+", " ", str(value or "").strip())
        return text.strip(" ,;，；")

    @staticmethod
    def _is_header_echo(value: str, headers: list[str]) -> bool:
        norm = TableEntityExtractor._norm_header(value)
        return bool(norm and norm in set(headers))

    @staticmethod
    def _looks_like_address_locator(value: str) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        if re.search(r"0x[0-9a-fA-F]+", text):
            return True
        if re.search(r"\b[A-Z0-9_]*BAR\d*\b", text, re.I):
            return True
        if re.search(r"\b(BDF|offset|base|host|local|memory|register|寄存器|空间|偏移)\b", text, re.I):
            return True
        if any(ch in text for ch in "+:/"):
            return True
        return False

    @staticmethod
    def _looks_like_signal_name(value: str) -> bool:
        text = str(value or "").strip()
        if not text or len(text) > 96:
            return False
        lowered = text.lower()
        if lowered in {
            "reserved", "reserve", "description", "clock/reset", "interface group",
            "interrupt", "interrupts", "irq", "irqs",
        }:
            return False
        if any(ch in text for ch in " /，,;；"):
            return False
        if re.search(r"[a-zA-Z][a-zA-Z0-9]*_[a-zA-Z0-9_]+", text):
            return True
        if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9]*(?:\[[0-9:]+\])?", text):
            return any(ch.islower() for ch in text) or text.isupper()
        return False

    @staticmethod
    def _normalize_direction(value: str) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        upper = text.upper().replace(" ", "")
        if re.fullmatch(r"\d+", upper):
            return None
        mapping = {
            "I": "IN",
            "IN": "IN",
            "INPUT": "IN",
            "O": "OUT",
            "OUT": "OUT",
            "OUTPUT": "OUT",
            "IO": "IO",
            "I/O": "IO",
            "BIDIR": "BIDIR",
            "BIDIRECTIONAL": "BIDIR",
            "MASTER": "master",
            "SLAVE": "slave",
        }
        return mapping.get(upper, text)

    @staticmethod
    def _normalize_width(value) -> str | None:
        text = str(value or "").strip()
        if not text or text.lower() in {"-", "--", "n/a", "na", "none", "null"}:
            return None
        text = re.sub(r"\s+", " ", text)
        repeated = re.fullmatch(r"(\d+)(?:\s+\1)+", text)
        if repeated:
            return repeated.group(1)
        bit_suffix = re.fullmatch(r"(\d+)\s*b", text, flags=re.I)
        if bit_suffix:
            return bit_suffix.group(1)
        return text

    @staticmethod
    def _register_name_from_caption(caption: str) -> str:
        text = caption or ""
        patterns = [
            r"fields\s+for\s+register\s*[:：]\s*([A-Za-z0-9_.\-\[\]/]+)",
            r"register\s*[:：]\s*([A-Za-z0-9_.\-\[\]/]+)",
            r"寄存器\s*[:：]\s*([A-Za-z0-9_.\-\[\]/]+)",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.I)
            if m:
                return m.group(1).strip().rstrip(".,;，。；")
        return ""

    @staticmethod
    def _register_name_from_cells(cells: list[str], *, skip_indexes: set[int | None]) -> str:
        """Recover register names accidentally shifted into neighbor columns."""
        for idx, cell in enumerate(cells[:4]):
            if idx in skip_indexes:
                continue
            text = str(cell or "").strip()
            if not text:
                continue
            m = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*?(?:reg|ctrl|cfg|stat|state|sel)[A-Za-z0-9_]*)\b", text, re.I)
            if m:
                return m.group(1)
        return ""

    @staticmethod
    def _looks_like_continuation_reg_cell(value: str) -> bool:
        text = str(value or "").strip()
        return not text or bool(re.fullmatch(r"\d+|[-–—]+", text))

    @staticmethod
    def _bit_suffix(field_name: str) -> int | None:
        m = re.search(r"(?:^|_)bit[_-]?(\d+)$", field_name, re.I)
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _fallback_bit_range_from_field(
        field_name: str,
        known_bit_ranges: dict[int, tuple[int, int]],
    ) -> tuple[int, int] | None:
        suffix = TableEntityExtractor._bit_suffix(field_name)
        if suffix is None:
            return None
        for delta in (4, 8, 16):
            previous = suffix - delta
            if previous in known_bit_ranges:
                return known_bit_ranges[previous]
        return None

    @staticmethod
    def _parse_bit_pair(high: str, low: str) -> tuple[int, int] | None:
        hi = TableEntityExtractor._parse_intish(high)
        lo = TableEntityExtractor._parse_intish(low)
        if hi is None or lo is None:
            return None
        if hi < lo:
            hi, lo = lo, hi
        return hi, lo

    @staticmethod
    def _bit_range_from_text(text: str) -> tuple[int, int] | None:
        raw = str(text or "")
        m = re.search(r"(\d+)\s*[:：]\s*(\d+)", raw)
        if m:
            hi, lo = int(m.group(1)), int(m.group(2))
            if hi < lo:
                hi, lo = lo, hi
            return hi, lo
        m = re.search(r"\bbit[s]?\s*(\d+)\b", raw, re.I)
        if m:
            bit = int(m.group(1))
            return bit, bit
        m = re.search(r"\b(\d+)\b", raw)
        if m and re.search(r"\bbit", raw, re.I):
            bit = int(m.group(1))
            return bit, bit
        return None

    @staticmethod
    def _parse_intish(value: str | int | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        text = str(value).strip()
        m = re.search(r"0x[0-9a-fA-F]+|\d+", text)
        if not m:
            return None
        try:
            return int(m.group(0), 0)
        except Exception:
            return None

    @staticmethod
    def _normalize_access(value: str) -> str | None:
        text = str(value or "").strip().upper().replace(" ", "")
        if not text:
            return None
        text = text.replace("R/W", "RW").replace("R/O", "RO").replace("W/O", "WO")
        for token in ("RW", "RO", "WO", "W1C", "W1S", "RC", "RS", "WC", "R", "W"):
            if token in text:
                return token
        return text[:24]

    @staticmethod
    def _select_non_overlapping_bitfields(bitfields: list) -> tuple[list, list[dict]]:
        """Keep a deterministic non-overlapping bitfield set.

        LLM/table OCR can duplicate rows or merge adjacent bit ranges. This
        keeps the set that maximizes covered bits and, for ties, keeps more
        granular fields. Dropped items are recorded on the register node.
        """
        candidates: list[tuple[int, int, int, object]] = []
        dropped: list[dict] = []
        for idx, bf in enumerate(bitfields):
            name = getattr(bf, "name", "") or f"bitfield_{idx}"
            high = getattr(bf, "bit_high", None)
            low = getattr(bf, "bit_low", None)
            try:
                high_i = int(high)
                low_i = int(low)
            except Exception:
                dropped.append({"name": name, "bit_high": high, "bit_low": low, "reason": "invalid_range"})
                continue
            if high_i < low_i:
                dropped.append({"name": name, "bit_high": high_i, "bit_low": low_i, "reason": "invalid_range"})
                continue
            candidates.append((low_i, high_i, idx, bf))
        if len(candidates) <= 1:
            return [c[3] for c in candidates], dropped

        ordered = sorted(candidates, key=lambda item: (item[1], item[0], item[2]))
        prev: list[int] = []
        for i, (low, _high, _idx, _bf) in enumerate(ordered):
            j = i - 1
            while j >= 0 and ordered[j][1] >= low:
                j -= 1
            prev.append(j)

        # score = (covered bits, number of fields). Python tuple comparison is lexicographic.
        best: list[tuple[int, int, tuple[int, ...]]] = [(0, 0, tuple())]
        for i, (low, high, _idx, _bf) in enumerate(ordered, start=1):
            width = high - low + 1
            take_base = best[prev[i - 1] + 1]
            take = (take_base[0] + width, take_base[1] + 1, take_base[2] + (i - 1,))
            skip = best[i - 1]
            best.append(max(skip, take))

        selected_indexes = set(best[-1][2])
        selected = [ordered[i][3] for i in sorted(selected_indexes, key=lambda idx: ordered[idx][2])]
        for i, (low, high, _idx, bf) in enumerate(ordered):
            if i in selected_indexes:
                continue
            dropped.append({
                "name": getattr(bf, "name", ""),
                "bit_high": high,
                "bit_low": low,
                "reason": "overlap",
            })
        return selected, dropped
