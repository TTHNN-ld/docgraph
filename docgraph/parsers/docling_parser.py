"""Docling PDF parser adapter."""
from __future__ import annotations

import re
from importlib.util import find_spec
from pathlib import Path
from typing import Any, ClassVar

from docgraph.graph.schema import (
    BBox,
    Block,
    BlockKind,
    ParsedDoc,
    ParsedFigure,
    ParsedPage,
    ParsedTable,
    TableData,
    TextBlock,
    TocEntry,
)
from docgraph.parsers.base import ParseContext


class DoclingParser:
    """PDF parser backed by IBM Docling.

    Docling is optional and imported only when this parser is actually used.
    """

    name = "docling"
    supports: ClassVar[set[str]] = {".pdf"}
    version = "0.1"

    def can_parse(self, path: Path) -> bool:
        return path.suffix.lower() in self.supports and find_spec("docling") is not None

    def parse(self, path: Path, ctx: ParseContext) -> ParsedDoc:
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "docling is required for DoclingParser. Install with: pip install docling"
            ) from e

        opts = PdfPipelineOptions()
        opts.do_table_structure = True
        opts.table_structure_options.mode = TableFormerMode.ACCURATE
        opts.generate_page_images = False
        opts.generate_picture_images = True
        opts.do_formula_enrichment = False
        opts.do_picture_description = False

        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )
        result = converter.convert(path)
        document = result.document
        pages = _docling_document_to_pages(document, ctx, path)
        toc = _toc_from_blocks(pages)
        return ParsedDoc(
            doc_id=ctx.doc_id,
            source_path=str(path),
            pages=pages,
            metadata=ctx.metadata,
            toc=toc,
            parser=self.name,
            parser_version=self.version,
        )


def _docling_document_to_pages(document: Any, ctx: ParseContext, path: Path) -> list[ParsedPage]:
    page_items: dict[int, list[Any]] = {}
    for item in _iter_body_items(document):
        page_no = _page_no(item) or 1
        page_items.setdefault(page_no, []).append(item)

    page_count = _num_pages(document)
    if not page_count:
        page_count = max(page_items) if page_items else 1

    pages: list[ParsedPage] = []
    for page_no in range(1, page_count + 1):
        blocks: list[Block] = []
        text_blocks: list[TextBlock] = []
        tables: list[ParsedTable] = []
        figures: list[ParsedFigure] = []
        order = 0

        for item in page_items.get(page_no, []):
            block = _item_to_block(document, item, ctx, path, page_no, order)
            if block is None:
                continue
            blocks.append(block)
            match block.kind:
                case BlockKind.HEADING | BlockKind.PARAGRAPH | BlockKind.LIST | BlockKind.CAPTION:
                    text_blocks.append(TextBlock(
                        text=block.text or "",
                        bbox=block.bbox,
                        reading_order=order,
                        is_heading=block.kind == BlockKind.HEADING,
                        heading_level=block.heading_level,
                    ))
                case BlockKind.TABLE:
                    if block.table:
                        tables.append(ParsedTable(
                            html=block.table.html,
                            headers=block.table.headers,
                            rows=block.table.rows,
                            bbox=block.bbox,
                            caption=block.table.caption,
                        ))
                case BlockKind.FIGURE:
                    figures.append(ParsedFigure(
                        image_path=block.image_path,
                        bbox=block.bbox,
                        caption=block.text,
                    ))
                case _:
                    pass
            order += 1

        pages.append(ParsedPage(
            page_no=page_no,
            blocks=blocks,
            text_blocks=text_blocks,
            tables=tables,
            figures=figures,
        ))
    return pages


def _num_pages(document: Any) -> int:
    value = getattr(document, "num_pages", 0)
    if callable(value):
        try:
            value = value()
        except Exception:
            value = 0
    try:
        return int(value or len(getattr(document, "pages", {}) or {}))
    except Exception:
        return len(getattr(document, "pages", {}) or {})


def _iter_body_items(document: Any) -> list[Any]:
    out: list[Any] = []
    for ref in getattr(getattr(document, "body", None), "children", []) or []:
        item = _resolve_ref(document, ref)
        if item is None:
            continue
        out.extend(_flatten_item(document, item))
    return out


def _flatten_item(document: Any, item: Any) -> list[Any]:
    label = _label(item)
    if label in {"group", "list", "ordered_list", "unordered_list"}:
        out: list[Any] = []
        for child_ref in getattr(item, "children", []) or []:
            child = _resolve_ref(document, child_ref)
            if child is not None:
                out.extend(_flatten_item(document, child))
        return out
    return [item]


def _resolve_ref(document: Any, ref: Any) -> Any | None:
    cref = getattr(ref, "cref", None) or str(ref)
    match = re.match(r"#/([^/]+)/(\d+)$", cref)
    if not match:
        return None
    collection, index_s = match.groups()
    items = getattr(document, collection, None)
    if items is None:
        return None
    try:
        return items[int(index_s)]
    except Exception:
        return None


def _item_to_block(
    document: Any,
    item: Any,
    ctx: ParseContext,
    path: Path,
    page_no: int,
    order: int,
) -> Block | None:
    label = _label(item)
    bbox = _bbox_from_item(document, item)
    block_id = f"{ctx.doc_id}#p{page_no}#b{order}"

    match label:
        case "section_header" | "title":
            text = _text(item)
            if not text:
                return None
            level = int(getattr(item, "level", None) or _heading_level_from_text(text) or 1)
            section_path, _ = _split_section_number(text)
            return Block(
                id=block_id,
                doc_id=ctx.doc_id,
                page=page_no,
                kind=BlockKind.HEADING,
                reading_order=order,
                bbox=bbox,
                text=text,
                section_path=section_path,
                heading_level=level,
                attrs={"parser": DoclingParser.name, "docling_label": label},
            )
        case "table":
            headers, rows, merged = _table_grid(item)
            caption = _caption_text(item)
            html = _table_html(document, item)
            return Block(
                id=block_id,
                doc_id=ctx.doc_id,
                page=page_no,
                kind=BlockKind.TABLE,
                reading_order=order,
                bbox=bbox,
                table=TableData(
                    headers=headers,
                    rows=rows,
                    n_rows=len(rows),
                    n_cols=max([len(headers), *(len(r) for r in rows)] or [0]),
                    merged_cells=merged,
                    caption=caption,
                    html=html,
                ),
                attrs={"parser": DoclingParser.name, "table_source": "cells"},
            )
        case "picture" | "figure":
            caption = _caption_text(item)
            image_path = _save_picture(document, item, ctx, path, page_no, order)
            return Block(
                id=block_id,
                doc_id=ctx.doc_id,
                page=page_no,
                kind=BlockKind.FIGURE,
                reading_order=order,
                bbox=bbox,
                text=caption,
                image_path=image_path,
                attrs={"parser": DoclingParser.name, "docling_label": label},
            )
        case "list_item":
            text = _text(item)
            if not text:
                return None
            return Block(
                id=block_id,
                doc_id=ctx.doc_id,
                page=page_no,
                kind=BlockKind.LIST,
                reading_order=order,
                bbox=bbox,
                text=text,
                attrs={"parser": DoclingParser.name, "docling_label": label},
            )
        case _:
            text = _text(item)
            if not text:
                return None
            return Block(
                id=block_id,
                doc_id=ctx.doc_id,
                page=page_no,
                kind=BlockKind.PARAGRAPH,
                reading_order=order,
                bbox=bbox,
                text=text,
                attrs={"parser": DoclingParser.name, "docling_label": label},
            )


def _label(item: Any) -> str:
    label = getattr(item, "label", "")
    return str(getattr(label, "value", label)).strip().lower()


def _text(item: Any) -> str:
    return str(getattr(item, "text", "") or "").strip()


def _page_no(item: Any) -> int | None:
    prov = getattr(item, "prov", None) or []
    if not prov:
        return None
    try:
        return int(prov[0].page_no)
    except Exception:
        return None


def _bbox_from_item(document: Any, item: Any) -> BBox | None:
    prov = getattr(item, "prov", None) or []
    if not prov:
        return None
    p = prov[0]
    raw = getattr(p, "bbox", None)
    page_no = int(getattr(p, "page_no", 0) or 0)
    if raw is None:
        return None
    page = (getattr(document, "pages", {}) or {}).get(page_no)
    page_height = float(getattr(getattr(page, "size", None), "height", 0.0) or 0.0)
    left = float(getattr(raw, "l", 0.0))
    right = float(getattr(raw, "r", 0.0))
    top = float(getattr(raw, "t", 0.0))
    bottom = float(getattr(raw, "b", 0.0))
    origin = str(getattr(getattr(raw, "coord_origin", None), "value", "")).lower()
    if origin == "bottomleft" and page_height > 0:
        y0 = page_height - top
        y1 = page_height - bottom
    else:
        y0 = top
        y1 = bottom
    return BBox(
        x0=min(left, right),
        y0=min(y0, y1),
        x1=max(left, right),
        y1=max(y0, y1),
        page=page_no or None,
    )


def _table_grid(item: Any) -> tuple[list[str], list[list[str]], list[dict[str, Any]]]:
    data = getattr(item, "data", None)
    grid = getattr(data, "grid", None) or []
    rows: list[list[str]] = []
    for row in grid:
        rows.append([str(getattr(cell, "text", "") or "").strip() for cell in row])
    if not rows:
        return [], [], []
    header_row_count = 1
    first = grid[0] if grid else []
    if first and not any(bool(getattr(cell, "column_header", False)) for cell in first):
        header_row_count = 1
    headers = rows[0] if rows else []
    body = rows[header_row_count:]
    merged: list[dict[str, Any]] = []
    for cell in getattr(data, "table_cells", []) or []:
        row_span = int(getattr(cell, "row_span", 1) or 1)
        col_span = int(getattr(cell, "col_span", 1) or 1)
        if row_span > 1 or col_span > 1:
            merged.append({
                "row": int(getattr(cell, "start_row_offset_idx", 0) or 0),
                "col": int(getattr(cell, "start_col_offset_idx", 0) or 0),
                "row_span": row_span,
                "col_span": col_span,
                "text": str(getattr(cell, "text", "") or ""),
            })
    return headers, body, merged


def _table_html(document: Any, item: Any) -> str | None:
    try:
        return item.export_to_html(doc=document)
    except TypeError:
        try:
            return item.export_to_html()
        except Exception:
            return None
    except Exception:
        return None


def _caption_text(item: Any) -> str | None:
    try:
        text = item.caption_text()
    except Exception:
        text = None
    if text:
        return str(text).strip()
    captions = getattr(item, "captions", []) or []
    out = []
    for caption in captions:
        val = getattr(caption, "text", None)
        if val:
            out.append(str(val).strip())
    return "\n".join(out).strip() or None


def _save_picture(
    document: Any,
    item: Any,
    ctx: ParseContext,
    path: Path,
    page_no: int,
    order: int,
) -> str | None:
    if ctx.cache_dir is None:
        return None
    try:
        image = item.get_image(document)
    except TypeError:
        try:
            image = item.get_image()
        except Exception:
            image = None
    except Exception:
        image = None
    if image is None:
        return None
    out_dir = Path(ctx.cache_dir) / "docling_images"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{path.stem}_p{page_no:04d}_b{order:04d}.png"
    if not out.is_file():
        image.save(out)
    return str(out)


def _toc_from_blocks(pages: list[ParsedPage]) -> list[TocEntry]:
    toc: list[TocEntry] = []
    counters = [0] * 6
    for page in pages:
        for block in page.blocks:
            if block.kind != BlockKind.HEADING:
                continue
            level = min(max(block.heading_level or 1, 1), len(counters))
            section_path, title = _split_section_number(block.text or "")
            if section_path is None:
                counters[level - 1] += 1
                for i in range(level, len(counters)):
                    counters[i] = 0
                section_path = ".".join(str(v) for v in counters[:level] if v)
                title = block.text or ""
            toc.append(TocEntry(
                level=level,
                title=title or block.text or "",
                page=page.page_no,
                section_path=section_path,
            ))
    return toc


def _heading_level_from_text(text: str) -> int | None:
    section_path, _ = _split_section_number(text)
    if not section_path:
        return None
    return section_path.count(".") + 1


def _split_section_number(title: str) -> tuple[str | None, str]:
    s = " ".join((title or "").split()).strip()
    match = re.match(r"^(\d+(?:\.\d+){0,5})(?:[.)])?\s*(.*)$", s)
    if not match:
        return None, s
    return match.group(1), match.group(2).strip() or s
