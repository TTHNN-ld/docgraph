"""MinerU 3.x PDF parser adapter.

The adapter runs MinerU's orchestration client and normalizes its ``middle.json``
into DocGraph's L0 contract. MinerU may execute locally, or keep document
orchestration local while sending VLM inference to an OpenAI-compatible model
server through the official ``vlm-http-client``/``hybrid-http-client`` backend.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from docgraph.core.logger import get_logger
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

log = get_logger(__name__)


class MinerUParser:
    """Use MinerU 3.x and normalize its structured output to ``ParsedDoc``."""

    name = "mineru"
    supports = {".pdf"}
    version = "3-cli-v1"

    def can_parse(self, path: Path) -> bool:
        return (
            path.suffix.lower() in self.supports
            and find_spec("mineru") is not None
            and shutil.which("mineru") is not None
        )

    def parse(self, path: Path, ctx: ParseContext) -> ParsedDoc:
        mid, image_dir, backend = self._run_cli(path, ctx)
        parsed = _middle_json_to_parsed_doc(path, ctx, mid, image_dir=image_dir)
        for page in parsed.pages:
            for block in page.blocks:
                block.attrs.setdefault("mineru_backend", backend)
        return parsed

    def _run_cli(
        self,
        path: Path,
        ctx: ParseContext,
    ) -> tuple[dict[str, Any], Path, str]:
        settings = dict((ctx.options or {}).get("mineru") or {})
        backend = str(settings.get("backend") or "pipeline")
        model_server_url = _configured_value(
            settings,
            "model_server_url",
            "model_server_url_env",
            "MINERU_MODEL_SERVER_URL",
        )
        if backend.endswith("-http-client") and not model_server_url:
            raise RuntimeError(
                f"MinerU backend '{backend}' requires parsers.pdf.mineru.model_server_url "
                "or MINERU_MODEL_SERVER_URL"
            )

        table_enabled = bool(settings.get("table", True)) and _table_enabled_for_quality(
            (ctx.options or {}).get("quality")
        )
        cache_dir = Path(ctx.cache_dir) if ctx.cache_dir else path.parent / ".mineru_cache"
        output_dir = cache_dir / "mineru" / _cache_variant(
            settings,
            model_server_url,
            table_enabled=table_enabled,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        middle_path = _find_middle_json(output_dir, path.stem)
        if middle_path is not None:
            log.info(f"[mineru] reusing cached middle json: {middle_path}")
            return _read_json_object(middle_path), middle_path.parent, backend

        command = [
            _mineru_executable(),
            "--path",
            str(path),
            "--output",
            str(output_dir),
            "--backend",
            backend,
            "--formula",
            _cli_bool(settings.get("formula", True)),
            "--table",
            _cli_bool(table_enabled),
            "--image-analysis",
            _cli_bool(settings.get("image_analysis", True)),
            "--client-side-output-generation",
            "true",
        ]
        if model_server_url:
            command.extend(["--url", model_server_url])

        env = os.environ.copy()
        timeout_seconds = int(settings.get("timeout_seconds") or 3600)
        env["MINERU_TASK_RESULT_TIMEOUT_SECONDS"] = str(timeout_seconds)
        model = _configured_value(settings, "model", "model_env", "MINERU_VL_MODEL_NAME")
        api_key = _configured_value(settings, "api_key", "api_key_env", "MINERU_VL_API_KEY")
        if model:
            env["MINERU_VL_MODEL_NAME"] = model
        if api_key:
            env["MINERU_VL_API_KEY"] = api_key

        log.info(f"[mineru] processing {path.name} with backend={backend}")
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout_seconds + 30,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"MinerU timed out after {timeout_seconds} seconds using backend={backend}"
            ) from exc
        except OSError as exc:
            raise RuntimeError(f"Could not start MinerU CLI: {exc}") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown error").strip()
            raise RuntimeError(
                f"MinerU CLI failed with exit code {completed.returncode}: {detail[-2000:]}"
            )
        middle_path = _find_middle_json(output_dir, path.stem)
        if middle_path is None:
            raise RuntimeError(f"MinerU did not produce {path.stem}_middle.json under {output_dir}")
        return _read_json_object(middle_path), middle_path.parent, backend


def _mineru_executable() -> str:
    executable = shutil.which("mineru")
    if executable is None:
        raise RuntimeError("MinerU 3.x CLI is not installed; install the 'mineru' extra")
    return executable


def _configured_value(
    settings: dict[str, Any],
    value_key: str,
    env_key: str,
    default_env: str,
) -> str | None:
    direct = settings.get(value_key)
    if direct is not None and str(direct).strip():
        return str(direct).strip()
    env_name = str(settings.get(env_key) or default_env).strip()
    return os.environ.get(env_name) if env_name else None


def _cache_variant(
    settings: dict[str, Any],
    model_server_url: str | None,
    *,
    table_enabled: bool,
) -> str:
    payload = {
        "adapter_version": MinerUParser.version,
        "backend": settings.get("backend") or "pipeline",
        "model_server_url": model_server_url,
        "model": _configured_value(settings, "model", "model_env", "MINERU_VL_MODEL_NAME"),
        "formula": bool(settings.get("formula", True)),
        "table": table_enabled,
        "image_analysis": bool(settings.get("image_analysis", True)),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()[:12]
    return f"{payload['backend']}-{digest}"


def _find_middle_json(output_dir: Path, stem: str) -> Path | None:
    matches = sorted(output_dir.rglob(f"{stem}_middle.json"))
    return matches[0] if matches else None


def _read_json_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise RuntimeError(f"MinerU middle JSON must be an object: {path}")
    return value


def _cli_bool(value: Any) -> str:
    return "true" if bool(value) else "false"


def _table_enabled_for_quality(quality: Any) -> bool:
    return str(quality or "balanced").strip().lower() != "fast"


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
            elif btype in {"equation", "interline_equation"}:
                latex = _join_spans(blk)
                blocks.append(Block(
                    id=_block_id(ctx.doc_id, pno, order),
                    doc_id=ctx.doc_id,
                    page=pno,
                    kind=BlockKind.FORMULA,
                    reading_order=order,
                    bbox=bb,
                    text=latex or None,
                    latex=latex or None,
                    attrs={"parser": MinerUParser.name},
                ))
                order += 1
            elif btype in {"code", "algorithm"}:
                text = _join_spans(blk)
                if text:
                    blocks.append(Block(
                        id=_block_id(ctx.doc_id, pno, order),
                        doc_id=ctx.doc_id,
                        page=pno,
                        kind=BlockKind.CODE,
                        reading_order=order,
                        bbox=bb,
                        text=text,
                        attrs={
                            "parser": MinerUParser.name,
                            "mineru_type": btype,
                            "sub_type": blk.get("sub_type"),
                        },
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
        yield from line.get("spans", []) or []
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
    joined = "".join(out).strip()
    if joined:
        return joined
    direct = blk.get("content") or blk.get("text") or blk.get("code_body")
    return str(direct).strip() if isinstance(direct, (str, int, float)) else ""


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
