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
import time
from pathlib import Path

from docgraph.core.ids import make_node_id
from docgraph.core.logger import get_logger
from docgraph.extractors.candidates import EntityCandidate, build_entity_candidates
from docgraph.extractors._vlm_backstop import page_needs_vlm_for, vlm_extract
from docgraph.extractors.base import ExtractContext
from docgraph.extractors.schema_registry import (
    EntitySchema,
    RegisterDef,
    get_schema,
    schemas_for_doctype,
)
from docgraph.graph.schema import (
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

        if not ctx.has_llm:
            # 无 LLM 时 L2 不产出（L0/L1 已完整）
            return ExtractResult(
                stats=ExtractStats(duration_s=round(time.time() - t0, 3)),
            )

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
                if self._table_has_cells(candidate.table):
                    result = self._llm_extract(candidate.table, schema, sn, ctx)
                    llm_calls += 1
                else:
                    continue
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
        attrs["source"] = f"table_entity:{schema_name}"
        attrs["source_block_ids"] = source_block_ids or []
        attrs["source_chunk_ids"] = source_chunk_ids or []
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
        hits = sum(1 for h in schema.table_header_hints if h.lower() in pool)
        if not (table.headers or table.rows or table.html):
            return hits >= 1
        return hits >= 2

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
