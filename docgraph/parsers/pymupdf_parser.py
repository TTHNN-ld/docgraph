"""PyMuPDF Parser —— M6 智能路由版本。

M6 升级：
- 每页 PageQuality 评估
- 低质页（扫描 / 表格密集 / 图密集）自动渲染为 PNG 供 VLM 兜底
- 不再写死 figures=[]：识别到 figure caption 时把整页或裁切区作为 ParsedFigure
- 仍然不抽 HTML 表格（那是 Marker/MinerU 的强项），但把"表格关键词命中"标记好

ParsedPage.quality.needs_vlm == True 的页，下游 Extractor 可以用 VLM 整页兜底。
"""

from __future__ import annotations

import re
from pathlib import Path

from docgraph.core.logger import get_logger
from docgraph.graph.schema import (
    BBox,
    Block,
    BlockKind,
    PageQuality,
    ParsedDoc,
    ParsedFigure,
    ParsedPage,
    ParsedTable,
    TableData,
    TextBlock,
    TocEntry,
)
from docgraph.parsers.base import ParseContext

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# 关键词触发器
# ---------------------------------------------------------------------------

_RE_TABLE_CAPTION = re.compile(r"\bTable\s+\d", re.IGNORECASE)
_RE_FIGURE_CAPTION = re.compile(r"\bFigure\s+\d|\bFig\.?\s+\d", re.IGNORECASE)
_RE_REG_KEYWORDS = re.compile(
    r"\b(register|bit\s*field|bit\s*assignments?|bit\s*description|"
    r"register\s*description|register\s*summary)\b|"
    r"寄存器|位域|位字段",
    re.IGNORECASE,
)
_RE_PIN_KEYWORDS = re.compile(
    r"\b(pin\s*name|pin\s*no|signal\s*name|direction|function\b.*\bdescription|"
    r"\bI/?O\b\s+(type|direction))\b|管脚|引脚|信号名",
    re.IGNORECASE,
)
_RE_TIMING_KEYWORDS = re.compile(
    r"\b(min\b.*\btyp\b.*\bmax|min\b.*\bmax\b.*\bunit|"
    r"electrical\s*characteristics|timing\s*characteristics|"
    r"timing\s*parameters?)\b|时序参数|电气特性",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class PyMuPDFParser:
    name = "pymupdf"
    supports = {".pdf"}
    version = "0.2"

    # 触发 VLM 的阈值
    MIN_TEXT_CHARS_PER_PAGE = 80  # < 此值 → 视为扫描/空白
    IMAGE_AREA_HEAVY_RATIO = 0.35  # 图片占比超过此值 → 图重页
    DPI_FOR_VLM_RENDER = 144  # 整页渲染 DPI

    def can_parse(self, path: Path) -> bool:
        return path.suffix.lower() in self.supports

    def parse(self, path: Path, ctx: ParseContext) -> ParsedDoc:
        try:
            import pymupdf  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "pymupdf is required for PyMuPDFParser. "
                "Run uv sync to install the core dependencies"
            ) from e

        doc = pymupdf.open(str(path))
        try:
            pages = self._parse_pages(doc, ctx)
            toc = self._parse_toc(doc)
        finally:
            doc.close()

        n_vlm = sum(1 for p in pages if p.quality and p.quality.needs_vlm)
        if n_vlm:
            log.info(f"[pymupdf] {path.name}: {n_vlm}/{len(pages)} pages flagged for VLM")

        return ParsedDoc(
            doc_id=ctx.doc_id,
            source_path=str(path),
            pages=pages,
            metadata=ctx.metadata,
            toc=toc,
            parser=self.name,
            parser_version=self.version,
        )

    # ------- pages -------

    def _parse_pages(self, doc, ctx: ParseContext) -> list[ParsedPage]:
        out: list[ParsedPage] = []
        for i, page in enumerate(doc):
            page_no = i + 1
            text_blocks = self._extract_text_blocks(page, page_no=page_no)
            # L0：真实抽表格（保留单元格结构）
            parsed_tables, table_bboxes = self._extract_tables(page, page_no)
            quality = self._assess_quality(page, text_blocks, n_tables=len(parsed_tables))
            rendered: str | None = None

            if quality.needs_vlm and ctx.cache_dir:
                try:
                    rendered = self._render_page(page, page_no, ctx.cache_dir)
                except Exception as e:
                    log.warning(f"[pymupdf] render page {page_no} failed: {e}")

            figures: list[ParsedFigure] = []
            if quality.figure_caption_hits > 0 and rendered:
                caption_text = self._find_first_figure_caption(text_blocks)
                figures.append(
                    ParsedFigure(
                        image_path=rendered,
                        caption=caption_text,
                    )
                )

            # L0：构建统一 Block 列表（文本 + 表格 + 图）
            blocks = self._build_blocks(
                ctx.doc_id, page_no, text_blocks, parsed_tables, table_bboxes, figures
            )

            out.append(
                ParsedPage(
                    page_no=page_no,
                    blocks=blocks,
                    text_blocks=text_blocks,
                    tables=parsed_tables,
                    figures=figures,
                    formulas=[],
                    quality=quality,
                    rendered_image_path=rendered,
                )
            )
        return out

    # ------- L0：表格抽取（保留单元格） -------

    def _extract_tables(self, page, page_no: int):
        """用 PyMuPDF find_tables 抽表格，保留单元格结构。"""
        parsed: list[ParsedTable] = []
        bboxes: list = []
        try:
            found = page.find_tables()
        except Exception:
            return parsed, bboxes
        for t in getattr(found, "tables", []):
            try:
                data = t.extract()
            except Exception:
                continue
            if not data:
                continue
            # 清洗 None → ""
            rows = [[(c or "").strip() for c in row] for row in data]
            # 去掉全空行
            rows = [r for r in rows if any(r)]
            if not rows:
                continue
            headers = rows[0]
            body = rows[1:] if len(rows) > 1 else []
            bb = getattr(t, "bbox", None)
            parsed.append(
                ParsedTable(
                    headers=headers,
                    rows=body,
                    bbox=BBox(x0=bb[0], y0=bb[1], x1=bb[2], y1=bb[3], page=page_no) if bb else None,
                )
            )
            bboxes.append(bb)
        return parsed, bboxes

    def _build_blocks(
        self, doc_id, page_no, text_blocks, parsed_tables, table_bboxes, figures
    ) -> list[Block]:
        """把文本块 + 表格 + 图汇成统一 Block 列表（按 reading_order）。"""
        blocks: list[Block] = []
        idx = 0

        def _bid() -> str:
            nonlocal idx
            bid = f"{doc_id}#p{page_no}#b{idx}"
            idx += 1
            return bid

        # 表格区域 bbox（用于把落在表格内的文本块剔除，避免重复）
        tbbs = [bb for bb in table_bboxes if bb]

        for tb in text_blocks:
            # 若该文本块基本落在某个表格区域内，跳过（表格已单独成块）
            if tb.bbox and any(_inside(tb.bbox, bb) for bb in tbbs):
                continue
            kind = BlockKind.HEADING if tb.is_heading else BlockKind.PARAGRAPH
            blocks.append(
                Block(
                    id=_bid(),
                    doc_id=doc_id,
                    page=page_no,
                    kind=kind,
                    reading_order=tb.reading_order,
                    bbox=tb.bbox,
                    text=tb.text,
                    heading_level=tb.heading_level,
                )
            )

        for t in parsed_tables:
            blocks.append(
                Block(
                    id=_bid(),
                    doc_id=doc_id,
                    page=page_no,
                    kind=BlockKind.TABLE,
                    reading_order=10_000 + len(blocks),
                    bbox=t.bbox,
                    table=TableData(
                        headers=t.headers,
                        rows=t.rows,
                        n_rows=len(t.rows),
                        n_cols=len(t.headers),
                        caption=t.caption,
                    ),
                    attrs={"parser": self.name, "table_source": "cells"},
                )
            )

        for f in figures:
            blocks.append(
                Block(
                    id=_bid(),
                    doc_id=doc_id,
                    page=page_no,
                    kind=BlockKind.FIGURE,
                    reading_order=20_000 + len(blocks),
                    bbox=f.bbox,
                    image_path=f.image_path,
                    text=f.caption,
                )
            )

        return blocks

    def _extract_text_blocks(self, page, page_no: int) -> list[TextBlock]:
        out: list[TextBlock] = []
        try:
            blocks = page.get_text("blocks")
        except Exception:
            return out
        for blk in blocks:
            if len(blk) < 5:
                continue
            x0, y0, x1, y1, text = blk[0], blk[1], blk[2], blk[3], blk[4]
            block_no = blk[5] if len(blk) > 5 else 0
            block_type = blk[6] if len(blk) > 6 else 0
            if block_type != 0:
                continue
            text = (text or "").strip()
            if not text:
                continue
            out.append(
                TextBlock(
                    text=text,
                    bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1, page=page_no),
                    reading_order=int(block_no),
                )
            )
        for tb in out:
            tb.is_heading = self._looks_like_heading(tb.text)
        return out

    @staticmethod
    def _looks_like_heading(text: str) -> bool:
        if len(text) > 200 or "\n" in text:
            return False
        if re.match(r"^\d+(\.\d+){0,4}\s+\S", text):
            return True
        return False

    # ------- 质量评估 -------

    def _assess_quality(self, page, text_blocks: list[TextBlock], n_tables: int = 0) -> PageQuality:
        # 文本字符数
        text_chars = sum(len(tb.text) for tb in text_blocks)
        # 页面面积（pt^2）
        rect = page.rect
        page_area = max(1.0, float(rect.width) * float(rect.height))
        text_density = text_chars / page_area * 100  # 放大便于阅读

        # 图片面积比
        image_area = 0.0
        try:
            for info in page.get_image_info():
                bb = info.get("bbox")
                if bb and len(bb) == 4:
                    image_area += abs((bb[2] - bb[0]) * (bb[3] - bb[1]))
        except Exception:
            pass
        image_area_ratio = min(1.0, image_area / page_area)

        has_text_layer = text_chars > 20

        joined = "\n".join(tb.text for tb in text_blocks)
        table_hits = len(_RE_TABLE_CAPTION.findall(joined))
        figure_hits = len(_RE_FIGURE_CAPTION.findall(joined))
        register_hits = len(_RE_REG_KEYWORDS.findall(joined))
        pin_hits = len(_RE_PIN_KEYWORDS.findall(joined))
        timing_hits = len(_RE_TIMING_KEYWORDS.findall(joined))

        reasons: list[str] = []
        needs_vlm = False

        # 规则 1：扫描版（无文本层）→ 整页 VLM
        if not has_text_layer or text_chars < self.MIN_TEXT_CHARS_PER_PAGE:
            needs_vlm = True
            reasons.append("scan_like_no_text")

        # 规则 2：图重页（包含 figure caption + 大图片）→ 让 VLM 看图
        if image_area_ratio >= self.IMAGE_AREA_HEAVY_RATIO and figure_hits > 0:
            needs_vlm = True
            reasons.append("figure_heavy")

        # 规则 3/4/5：实体关键词 + 表格语境，但 find_tables 没抽到结构化表格
        # （n_tables == 0）才需要 VLM 兜底；若 L0 已抽到表格则不必。
        table_context = table_hits >= 1 or n_tables >= 1
        l0_table_missing = n_tables == 0
        if register_hits >= 1 and table_context and l0_table_missing:
            needs_vlm = True
            reasons.append("register_with_table")
        if pin_hits >= 1 and table_context and l0_table_missing:
            needs_vlm = True
            reasons.append("pin_with_table")
        if timing_hits >= 1 and table_context and l0_table_missing:
            needs_vlm = True
            reasons.append("timing_with_table")

        return PageQuality(
            text_chars=text_chars,
            text_blocks=len(text_blocks),
            text_density=round(text_density, 3),
            image_area_ratio=round(image_area_ratio, 3),
            has_text_layer=has_text_layer,
            table_keyword_hits=table_hits,
            register_keyword_hits=register_hits,
            figure_caption_hits=figure_hits,
            pin_keyword_hits=pin_hits,
            timing_keyword_hits=timing_hits,
            needs_vlm=needs_vlm,
            vlm_reasons=reasons,
        )

    # ------- 整页渲染 -------

    def _render_page(self, page, page_no: int, cache_dir: Path) -> str:
        """把整页渲染为 PNG，返回路径。同 hash 已存在则复用。"""
        img_dir = Path(cache_dir) / "page_renders"
        img_dir.mkdir(parents=True, exist_ok=True)
        out = img_dir / f"page_{page_no:04d}.png"
        if out.is_file():
            return str(out)
        import pymupdf  # type: ignore

        matrix = pymupdf.Matrix(self.DPI_FOR_VLM_RENDER / 72, self.DPI_FOR_VLM_RENDER / 72)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        pix.save(str(out))
        return str(out)

    # ------- TOC -------

    def _parse_toc(self, doc) -> list[TocEntry]:
        try:
            raw = doc.get_toc()
        except Exception:
            return []
        out: list[TocEntry] = []
        for entry in raw:
            if len(entry) < 3:
                continue
            level, title, page = entry[0], entry[1], entry[2]
            out.append(
                TocEntry(
                    level=int(level),
                    title=str(title).strip(),
                    page=int(page),
                )
            )
        return out

    # ------- helpers -------

    @staticmethod
    def _find_first_figure_caption(blocks: list[TextBlock]) -> str | None:
        for tb in blocks:
            m = _RE_FIGURE_CAPTION.search(tb.text)
            if m:
                # 取 caption 整行
                line = tb.text[m.start() : m.start() + 200].split("\n", 1)[0].strip()
                return line
        return None


def _inside(inner: BBox, outer, tol: float = 4.0) -> bool:
    """判断 inner bbox 是否基本落在 outer (x0,y0,x1,y1) 内。用于剔除表格内文本块。"""
    if not outer or len(outer) != 4:
        return False
    ox0, oy0, ox1, oy1 = outer
    # inner 中心点在 outer 内即视为内部
    cx = (inner.x0 + inner.x1) / 2
    cy = (inner.y0 + inner.y1) / 2
    return (ox0 - tol) <= cx <= (ox1 + tol) and (oy0 - tol) <= cy <= (oy1 + tol)
