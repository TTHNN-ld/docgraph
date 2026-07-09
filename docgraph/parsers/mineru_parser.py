"""MinerU PDF parser adapter.

MinerU (https://github.com/opendatalab/MinerU) 是上海 AI Lab 开源的高精度
PDF parser，对中文混排、公式、复杂表格识别效果好。

依赖更重（detectron2 / paddle / 模型 ~4GB），按需 import。

安装：
  pip install magic-pdf[full]
  # 或参考 https://github.com/opendatalab/MinerU 的最新安装文档

使用：
  config.yaml:
    parsers:
      pdf:
        primary: mineru
        fallback: [docling, pymupdf]
"""
from __future__ import annotations

import json
import os
import re
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from docgraph.core.config import user_docgraph_dir
from docgraph.core.logger import get_logger
from docgraph.graph.schema import (
    BBox, Block, BlockKind, ParsedDoc, ParsedFigure, ParsedPage, ParsedTable,
    TableData, TextBlock, TocEntry,
)
from docgraph.parsers.base import ParseContext

log = get_logger(__name__)


class MinerUParser:
    """基于 magic-pdf (MinerU) 的 PDF parser。

    通过当前 magic-pdf API 产出 middle JSON，再归一为 ParsedDoc。
    """
    name = "mineru"
    supports = {".pdf"}
    version = "0.1"

    def can_parse(self, path: Path) -> bool:
        return path.suffix.lower() in self.supports and find_spec("magic_pdf") is not None

    def parse(self, path: Path, ctx: ParseContext) -> ParsedDoc:
        mid, image_dir = self._parse_with_current_api(path, ctx)
        return _middle_json_to_parsed_doc(path, ctx, mid, image_dir=image_dir)

    def _parse_with_current_api(self, path: Path, ctx: ParseContext) -> tuple[dict[str, Any], Path]:
        cache_dir = Path(ctx.cache_dir) if ctx.cache_dir else path.parent / ".mineru_cache"
        table_enable = _table_enabled_for_quality(ctx.options.get("quality"))
        output_dir = cache_dir / ("mineru_table" if table_enable else "mineru_fast")
        output_dir.mkdir(parents=True, exist_ok=True)
        models_dir = _mineru_models_dir()
        models_dir.mkdir(parents=True, exist_ok=True)
        config_path = cache_dir.parent.parent / "magic-pdf.json"
        config = {
            "models-dir": str(models_dir),
            "device-mode": _resolve_device(ctx),
            "layout-config": {"model": "doclayout_yolo"},
            "formula-config": {"enable": False},
            "table-config": {
                "model": "rapid_table",
                "enable": table_enable,
                "max_time": 400,
            },
        }
        if config_path.is_file():
            try:
                existing = json.loads(config_path.read_text(encoding="utf-8"))
                existing.update(config)
                config = existing
            except Exception:
                pass
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.environ["MINERU_TOOLS_CONFIG_JSON"] = str(config_path.resolve())

        try:
            import magic_pdf.model as model_config  # type: ignore
            import magic_pdf.pdf_parse_union_core_v2 as parse_core  # type: ignore
            import magic_pdf.post_proc.para_split_v3 as para_split  # type: ignore
            from magic_pdf.tools.common import do_parse  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "magic-pdf not installed. Install: pip install 'magic-pdf[full]'\n"
                "See https://github.com/opendatalab/MinerU for full setup."
            ) from e

        log.info(f"[mineru] processing {path.name} with magic-pdf current API ...")
        _patch_rapid_table_ocr_result()
        ocr_device = _resolve_ocr_device(ctx)
        _patch_ocr_device(ocr_device)
        if ocr_device and ocr_device != _resolve_device(ctx):
            log.info(
                f"[mineru] split device: layout={_resolve_device(ctx)} ocr={ocr_device}"
            )

        # The pip package defaults to external model-list mode; use the bundled model pipeline.
        model_config.__use_inside_model__ = True
        model_config.__model_mode__ = "full"
        # Avoid blocking on the optional hantian/layoutreader download. MinerU's
        # downstream code falls back to coordinate/xy-cut ordering when this
        # returns None, which is good enough for local parser evaluation.
        parse_core.sort_lines_by_model = lambda *args, **kwargs: None
        original_merge = getattr(para_split, "__merge_2_text_blocks")

        def merge_nonempty_text_blocks(block1, block2):
            if not block1.get("lines") or not block2.get("lines"):
                return None
            return original_merge(block1, block2)

        setattr(para_split, "__merge_2_text_blocks", merge_nonempty_text_blocks)
        pdf_name = path.stem
        middle_path = output_dir / pdf_name / "txt" / f"{pdf_name}_middle.json"
        if middle_path.is_file():
            log.info(f"[mineru] reusing cached middle json: {middle_path}")
            with middle_path.open("r", encoding="utf-8") as f:
                return json.load(f), middle_path.parent

        do_parse(
            str(output_dir),
            pdf_name,
            path.read_bytes(),
            [],
            "txt",
            debug_able=False,
            f_draw_span_bbox=False,
            f_draw_layout_bbox=False,
            f_dump_md=True,
            f_dump_middle_json=True,
            f_dump_model_json=False,
            f_dump_orig_pdf=False,
            f_dump_content_list=True,
            formula_enable=False,
            table_enable=table_enable,
        )

        if not middle_path.is_file():
            matches = list(output_dir.rglob(f"{pdf_name}_middle.json"))
            if not matches:
                raise RuntimeError(f"MinerU did not produce middle json under {output_dir}")
            middle_path = matches[0]

        with middle_path.open("r", encoding="utf-8") as f:
            mid = json.load(f)
        return mid, middle_path.parent


def _table_enabled_for_quality(quality: Any) -> bool:
    return str(quality or "balanced").strip().lower() != "fast"


def _mineru_models_dir() -> Path:
    """Return the user-level MinerU model directory.

    Parser outputs and middle JSON stay under the project `.docgraph/cache`, but
    model weights are large and should be reused across projects.
    """
    override = os.environ.get("DOCGRAPH_MINERU_MODELS_DIR")
    if override:
        return Path(override).expanduser()
    return user_docgraph_dir() / "mineru-models"


def _resolve_device(ctx: ParseContext) -> str:
    """torch device for MinerU layout (doclayout_yolo) + the global default.

    DOCGRAPH_MINERU_DEVICE overrides; else ctx.options['device'] from
    parsers.pdf.device; else cpu. rapid_table (onnx) ignores this and always
    runs on CPU. OCR can be split off via ocr_device (see _resolve_ocr_device).
    """
    forced = os.environ.get("DOCGRAPH_MINERU_DEVICE", "").strip().lower()
    if forced in {"cpu", "cuda", "mps"}:
        return forced
    cfg_val = (ctx.options or {}).get("device") if ctx.options else None
    if isinstance(cfg_val, str) and cfg_val.strip().lower() in {"cpu", "cuda", "mps"}:
        return cfg_val.strip().lower()
    return "cpu"


def _resolve_ocr_device(ctx: ParseContext) -> str | None:
    """MinerU OCR (paddleocr2pytorch) device override. Priority: env > config > None.

    None -> OCR follows the layout device (device-mode). On Apple Silicon,
    setting ocr_device=cpu while device=mps keeps layout on MPS (fast) and OCR
    on CPU (paddleocr2pytorch is faster on CPU than MPS, and avoids the MPS
    penalty). Side effect: cpu OCR auto-downgrades lang to ch_lite (magic-pdf
    heuristic), which is the fast path.
    """
    forced = os.environ.get("DOCGRAPH_MINERU_OCR_DEVICE", "").strip().lower()
    if forced in {"cpu", "cuda", "mps"}:
        return forced
    cfg_val = (ctx.options or {}).get("ocr_device") if ctx.options else None
    if isinstance(cfg_val, str) and cfg_val.strip().lower() in {"cpu", "cuda", "mps"}:
        return cfg_val.strip().lower()
    return None


def _patch_ocr_device(ocr_device: str | None) -> None:
    """Pin paddleocr2pytorch to a specific device, independent of device-mode.

    PytorchPaddleOCR calls get_device() at __init__; we rebind the module-level
    name in pytorch_paddle so OCR sees our value while layout keeps using the
    global device-mode. Idempotent.
    """
    if not ocr_device:
        return
    try:
        from magic_pdf.model.sub_modules.ocr.paddleocr2pytorch import (  # type: ignore
            pytorch_paddle as pp,
        )
    except Exception:
        return
    if getattr(pp.get_device, "_docgraph_pinned", False):
        return

    def _pinned() -> str:
        return ocr_device

    _pinned._docgraph_pinned = True  # type: ignore[attr-defined]
    pp.get_device = _pinned


def _middle_json_to_parsed_doc(
    path: Path,
    ctx: ParseContext,
    mid: dict[str, Any],
    *,
    image_dir: Path,
) -> ParsedDoc:
    pages_raw = mid.get("pdf_info") or []
    pages: list[ParsedPage] = []
    toc: list[TocEntry] = _parse_pdf_outline(path)
    use_mineru_title_toc = not toc
    # MinerU 导出的 markdown 里表格 HTML 完整（middle.json 里 html 字段为空）。
    # 按出现顺序逐 table block 对应回填。
    md_path = image_dir / f"{path.stem}.md"
    md_tables = _load_markdown_tables(md_path)
    md_idx = 0
    counter = [0] * 6
    for p in pages_raw:
        pno = int(p.get("page_idx", 0)) + 1
        blocks: list[Block] = []
        text_blocks: list[TextBlock] = []
        tables: list[ParsedTable] = []
        figures: list[ParsedFigure] = []
        order = 0

        for blk in p.get("preproc_blocks", []) or p.get("para_blocks", []):
            btype = _block_type(blk)
            bbox = blk.get("bbox") or [0, 0, 0, 0]
            bb = _bbox(bbox, pno)

            if btype == "title":
                title = _join_spans(blk)
                explicit_path, title_without_num = _split_section_number(title)
                level = int(blk.get("level", 1) or 1)
                if explicit_path:
                    level = explicit_path.count(".") + 1
                level = min(max(level, 1), len(counter))
                counter[level - 1] += 1
                for j in range(level, len(counter)):
                    counter[j] = 0
                path_str = explicit_path or ".".join(str(c) for c in counter[:level] if c > 0)
                if use_mineru_title_toc and explicit_path:
                    toc.append(TocEntry(
                        level=level,
                        title=title_without_num or title,
                        page=pno,
                        section_path=path_str or None,
                    ))
                text_blocks.append(TextBlock(
                    text=title, bbox=bb, reading_order=order,
                    is_heading=True, heading_level=level,
                ))
                blocks.append(Block(
                    id=_block_id(ctx.doc_id, pno, order),
                    doc_id=ctx.doc_id,
                    page=pno,
                    kind=BlockKind.HEADING,
                    reading_order=order,
                    bbox=bb,
                    text=title,
                    section_path=path_str or None,
                    heading_level=level,
                    attrs={"parser": MinerUParser.name},
                ))
                order += 1
            elif btype in {"text", "list", "index"}:
                text = _join_spans(blk)
                if text:
                    text_blocks.append(TextBlock(text=text, bbox=bb, reading_order=order))
                    blocks.append(Block(
                        id=_block_id(ctx.doc_id, pno, order),
                        doc_id=ctx.doc_id,
                        page=pno,
                        kind=BlockKind.LIST if btype == "list" else BlockKind.PARAGRAPH,
                        reading_order=order,
                        bbox=bb,
                        text=text,
                        attrs={"parser": MinerUParser.name},
                    ))
                    order += 1
            elif btype in {"table", "table_body"}:
                md_fallback = None
                if md_idx < len(md_tables):
                    md_fallback = md_tables[md_idx]
                    md_idx += 1
                headers, rows, html = _extract_table(blk, md_fallback=md_fallback)
                caption = _caption_from_nested_blocks(blk, {"table_caption"})
                image_path = _image_path_from_nested_blocks(blk, image_dir)
                raw_table_text = _join_spans(blk)
                if _is_decorative_table_image(
                    image_path=image_path,
                    caption=caption,
                    headers=headers,
                    rows=rows,
                    html=html,
                    raw_text=raw_table_text,
                ):
                    figures.append(ParsedFigure(
                        image_path=image_path, bbox=bb, caption=caption,
                    ))
                    blocks.append(Block(
                        id=_block_id(ctx.doc_id, pno, order),
                        doc_id=ctx.doc_id,
                        page=pno,
                        kind=BlockKind.FIGURE,
                        reading_order=order,
                        bbox=bb,
                        image_path=image_path,
                        text=caption,
                        attrs={
                            "parser": MinerUParser.name,
                            "mineru_type": btype,
                            "semantic_role": "decoration",
                        },
                    ))
                    order += 1
                    continue

                tables.append(ParsedTable(
                    html=html, headers=headers, rows=rows, bbox=bb,
                    caption=caption,
                ))
                blocks.append(Block(
                    id=_block_id(ctx.doc_id, pno, order),
                    doc_id=ctx.doc_id,
                    page=pno,
                    kind=BlockKind.TABLE,
                    reading_order=order,
                    bbox=bb,
                    table=TableData(
                        headers=headers,
                        rows=rows,
                        n_rows=len(rows),
                        n_cols=max([len(headers), *(len(r) for r in rows)] or [0]),
                        caption=caption,
                        html=html,
                    ),
                    image_path=image_path,
                    attrs={
                        "parser": MinerUParser.name,
                        "table_source": "html" if html else ("cells" if headers or rows else "image"),
                    },
                ))
                order += 1
            elif btype in {"image", "figure", "image_body"}:
                caption = _caption_from_nested_blocks(blk, {"image_caption"})
                image_path = _image_path_from_nested_blocks(blk, image_dir)
                figures.append(ParsedFigure(
                    image_path=image_path, bbox=bb, caption=caption,
                ))
                blocks.append(Block(
                    id=_block_id(ctx.doc_id, pno, order),
                    doc_id=ctx.doc_id,
                    page=pno,
                    kind=BlockKind.FIGURE,
                    reading_order=order,
                    bbox=bb,
                    image_path=image_path,
                    text=caption,
                    attrs={"parser": MinerUParser.name},
                ))
                order += 1

        pages.append(ParsedPage(
            page_no=pno, blocks=blocks, text_blocks=text_blocks,
            tables=tables, figures=figures,
        ))

    return ParsedDoc(
        doc_id=ctx.doc_id,
        source_path=str(path),
        pages=pages,
        metadata=ctx.metadata,
        toc=toc,
        parser=MinerUParser.name,
        parser_version=MinerUParser.version,
    )


def _parse_pdf_outline(path: Path) -> list[TocEntry]:
    """Read the PDF's embedded outline when available.

    MinerU is still the L0 layout parser. The outline is native PDF metadata,
    so using it avoids inventing section numbers from visual title order.
    """
    try:
        import fitz  # type: ignore
    except Exception:
        try:
            import pymupdf as fitz  # type: ignore
        except Exception:
            return []

    try:
        doc = fitz.open(path)
        raw = doc.get_toc()
    except Exception:
        return []

    out: list[TocEntry] = []
    seen: set[tuple[str | None, str, int | None]] = set()
    for entry in raw:
        if len(entry) < 3:
            continue
        level, title, page = entry[0], str(entry[1]).strip(), entry[2]
        if not title:
            continue
        section_path, clean_title = _split_section_number(title)
        if section_path and not clean_title:
            continue
        if section_path is None:
            section_path = _outline_path_from_level(len(out), int(level), out)
        key = (section_path, clean_title, int(page) if page else None)
        if key in seen:
            continue
        seen.add(key)
        out.append(TocEntry(
            level=int(level),
            title=clean_title or title,
            page=int(page) if page else None,
            section_path=section_path,
        ))
    return out


def _split_section_number(title: str) -> tuple[str | None, str]:
    s = " ".join((title or "").split()).strip()
    match = re.match(r"^(\d+(?:\.\d+){0,5})(?:[.)])?\s*(.*)$", s)
    if not match:
        return None, s
    section_path = match.group(1)
    clean_title = match.group(2).strip()
    return section_path, clean_title


def _outline_path_from_level(index: int, level: int, entries: list[TocEntry]) -> str | None:
    """Fallback path for outline entries without visible numbers."""
    if level <= 0:
        return None
    siblings = [
        e for e in entries
        if e.level == level and e.section_path and e.section_path.count(".") + 1 == level
    ]
    if not siblings and level == 1:
        return str(index + 1)
    return None


def _block_id(doc_id: str, page_no: int, order: int) -> str:
    return f"{doc_id}#p{page_no}#b{order}"


def _bbox(bbox: list[Any], page_no: int) -> BBox | None:
    if len(bbox) < 4:
        return None
    return BBox(
        x0=float(bbox[0]), y0=float(bbox[1]),
        x1=float(bbox[2]), y1=float(bbox[3]),
        page=page_no,
    )


def _block_type(blk: dict[str, Any]) -> str:
    return str(blk.get("type") or "").strip().lower()


def _caption_from_nested_blocks(blk: dict[str, Any], types: set[str]) -> str | None:
    captions: list[str] = []
    for child in blk.get("blocks", []) or []:
        if _block_type(child) in types:
            text = _join_spans(child)
            if text:
                captions.append(text)
    return "\n".join(captions).strip() or None


def _image_path_from_nested_blocks(blk: dict[str, Any], image_dir: Path) -> str | None:
    for child in blk.get("blocks", []) or [blk]:
        for line in child.get("lines", []) or []:
            for span in line.get("spans", []) or []:
                raw = span.get("image_path") or span.get("img_path")
                if raw:
                    p = Path(str(raw))
                    return str(_resolve_mineru_asset(image_dir, p))
    raw = blk.get("image_path") or blk.get("img_path")
    if raw:
        p = Path(str(raw))
        return str(_resolve_mineru_asset(image_dir, p))
    return None


def _resolve_mineru_asset(image_dir: Path, path: Path) -> Path:
    if path.is_absolute():
        return path
    direct = image_dir / path
    if direct.exists():
        return direct
    nested = image_dir / "images" / path
    if nested.exists():
        return nested
    return direct


def _iter_spans(blk: dict[str, Any]):
    for line in blk.get("lines", []) or []:
        for span in line.get("spans", []) or []:
            yield span
    for child in blk.get("blocks", []) or []:
        yield from _iter_spans(child)


def _span_text(span: dict[str, Any]) -> str:
    return str(span.get("content") or span.get("text") or "").strip()


def _span_table_html(span: dict[str, Any]) -> str:
    return str(
        span.get("html")
        or span.get("table_html")
        or span.get("latex")
        or ""
    )


def _join_spans(blk: dict) -> str:
    """递归把 block 内所有 span.text 拼起来。"""
    out: list[str] = []
    for span in _iter_spans(blk):
        text = _span_text(span)
        if text:
            out.append(text)
    return "".join(out).strip()


def _parse_table_html(html: str) -> tuple[list[str], list[list[str]]]:
    """解析表格 HTML → (headers, rows)，正确处理 rowspan/colspan。

    MinerU 导出的表格 HTML 带 rowspan/colspan 合并单元格；旧的正则解法会把
    合并单元格的行整体左移，导致字段错位。这里按"网格填充"重建：
    - 遇 colspan=N：当前格占 N 列
    - 遇 rowspan=N：把当前值填入下方 N-1 行的同列（占位，避免左移）
    """
    if not html:
        return [], []
    rows_raw = re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I)
    # 先解析每个 tr 的单元格（含属性）
    parsed_rows: list[list[tuple[str, int, int]]] = []  # [(text, colspan, rowspan)]
    for r in rows_raw:
        cells = re.findall(
            r"<t[dh](?P<attr>[^>]*)>(?P<body>.*?)</t[dh]>",
            r,
            flags=re.S | re.I,
        )
        row: list[tuple[str, int, int]] = []
        for attr, body in cells:
            text = re.sub(r"<[^>]+>", "", body).strip()
            cs = re.search(r"colspan\s*=\s*\"?(\d+)", attr, flags=re.I)
            rs = re.search(r"rowspan\s*=\s*\"?(\d+)", attr, flags=re.I)
            colspan = int(cs.group(1)) if cs else 1
            rowspan = int(rs.group(1)) if rs else 1
            row.append((text, colspan, rowspan))
        if row:
            parsed_rows.append(row)

    if not parsed_rows:
        return [], []

    # 按网格展开：colspan 横向占位，rowspan 向下占位
    n_cols = max(sum(c[1] for c in row) for row in parsed_rows)
    grid: list[list[str | None]] = [[None] * n_cols for _ in parsed_rows]
    pending: dict[int, tuple[str, int]] = {}  # col_idx -> (text, remaining_rows)
    for ri, row in enumerate(parsed_rows):
        ci = 0
        # 先消化本行上方 rowspan 的占位
        for col in range(n_cols):
            if col in pending:
                txt, rem = pending[col]
                grid[ri][col] = txt
                if rem - 1 <= 0:
                    del pending[col]
                else:
                    pending[col] = (txt, rem - 1)
        # 再填本行的单元格
        for text, colspan, rowspan in row:
            while ci < n_cols and grid[ri][ci] is not None:
                ci += 1
            if ci >= n_cols:
                break
            for k in range(colspan):
                if ci + k < n_cols:
                    grid[ri][ci + k] = text
            if rowspan > 1:
                for k in range(colspan):
                    if ci + k < n_cols:
                        pending[ci + k] = (text, rowspan - 1)
            ci += colspan

    result_rows: list[list[str]] = [
        [("" if v is None else v) for v in row] for row in grid
    ]
    headers = result_rows[0]
    body = result_rows[1:]
    return headers, body


def _load_markdown_tables(md_path: Path) -> list[str]:
    """读取 MinerU 导出 markdown，按出现顺序返回每个 <table>...</table> 的 HTML。

    middle.json 里部分 table block 的 html 字段为空（行数据丢失），但导出的
    markdown 里这些表格的 HTML 是完整的。按序匹配可一对一回填。
    """
    if not md_path.is_file():
        return []
    try:
        text = md_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    # markdown 里每个表格是一整行 <html><body><table>...</table></body></html>
    tables = re.findall(
        r"<table[^>]*>.*?</table>",
        text,
        flags=re.S | re.I,
    )
    return tables


def _extract_table(
    blk: dict, md_fallback: str | None = None
) -> tuple[list[str], list[list[str]], str | None]:
    """MinerU 表格 → headers + rows。

    优先用 block 自带 html；为空时用 markdown 回填（md_fallback），救回
    middle.json 丢失行数据的表格。两者都正确解析 rowspan/colspan。
    """
    html = blk.get("html") or ""
    if not html:
        for span in _iter_spans(blk):
            html = _span_table_html(span)
            if html:
                break
    if not html and md_fallback:
        html = md_fallback
    if not html:
        return [], [], None
    headers, rows = _parse_table_html(html)
    if headers or rows:
        return headers, rows, html
    return [], [], None


def _is_decorative_table_image(
    *,
    image_path: str | None,
    caption: str | None,
    headers: list[str],
    rows: list[list[str]],
    html: str | None,
    raw_text: str | None,
) -> bool:
    """Detect MinerU false-positive tables that are really cover/background art.

    The rule is intentionally conservative: only image-only, textless table
    blocks with almost no dark ink are reclassified. This keeps real scanned
    tables as TABLE blocks even when table OCR fails.
    """
    if headers or rows or html or caption or (raw_text or "").strip() or not image_path:
        return False
    try:
        from PIL import Image
    except Exception:
        return False

    try:
        with Image.open(image_path) as image:
            rgb = image.convert("RGB")
            rgb.thumbnail((256, 256))
            data = getattr(rgb, "get_flattened_data", rgb.getdata)
            pixels = list(data())
    except Exception:
        return False

    if not pixels:
        return False

    dark = 0
    for r, g, b in pixels:
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        if luminance < 100 and max(r, g, b) < 140:
            dark += 1

    return (dark / len(pixels)) < 0.003


def _patch_rapid_table_ocr_result() -> None:
    """Patch magic-pdf 1.3.x RapidTable wrapper for rapid-table 2.x.

    magic-pdf builds OCR rows as ``[[box, text, score], ...]`` and passes them
    directly to rapid-table. rapid-table 2.x expects one OCR package per image:
    ``[[boxes, texts, scores]]``. Without this compatibility shim, table
    recognition fails with ``'numpy.float32' object is not iterable``.
    """
    try:
        from magic_pdf.model.sub_modules.table.rapidtable import rapid_table as rt  # type: ignore
    except Exception:
        return
    model_cls = getattr(rt, "RapidTableModel", None)
    if model_cls is None or getattr(model_cls, "_docgraph_ocr_patch", False):
        return

    def predict(self, image):
        import cv2
        import numpy as np

        bgr_image = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
        ocr_rows = self.ocr_engine.ocr(bgr_image)[0]
        if not ocr_rows:
            return None, None, None, None

        boxes, texts, scores = [], [], []
        for item in ocr_rows:
            if len(item) != 2 or not isinstance(item[1], tuple):
                continue
            boxes.append(item[0])
            texts.append(item[1][0])
            scores.append(float(item[1][1]))

        if not boxes:
            return None, None, None, None

        ocr_result = [np.asarray(boxes), tuple(texts), tuple(scores)]
        table_results = self.table_model(np.asarray(image), [ocr_result])
        htmls = getattr(table_results, "pred_htmls", None)
        html_code = htmls[0] if htmls else getattr(table_results, "pred_html", None)
        cell_bboxes = getattr(table_results, "cell_bboxes", None)
        table_cell_bboxes = cell_bboxes[0] if cell_bboxes else None
        points = getattr(table_results, "logic_points", None)
        logic_points = points[0] if points else None
        elapse = table_results.elapse
        return html_code, table_cell_bboxes, logic_points, elapse

    model_cls.predict = predict
    model_cls._docgraph_ocr_patch = True
