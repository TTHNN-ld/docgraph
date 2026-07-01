"""Marker PDF parser adapter.

Marker (https://github.com/VikParuchuri/marker) 是基于 transformer 的高质量
PDF parser，对表格、公式、章节结构识别比 PyMuPDF 强很多。

依赖较重（torch + transformers + opencv），故按需 import。

安装：
  pip install marker-pdf

使用：
  config.yaml:
    parsers:
      pdf:
        primary: marker
        fallback: [pymupdf]
"""
from __future__ import annotations

from pathlib import Path

from docgraph.core.logger import get_logger
from docgraph.graph.schema import (
    ParsedDoc, ParsedFigure, ParsedPage, ParsedTable,
    TextBlock, TocEntry,
)
from docgraph.parsers.base import ParseContext

log = get_logger(__name__)


class MarkerParser:
    """基于 marker-pdf 的 PDF parser。"""
    name = "marker"
    supports = {".pdf"}
    version = "0.1"

    _converter = None  # 模型加载较慢，做类级缓存

    def can_parse(self, path: Path) -> bool:
        return path.suffix.lower() in self.supports

    @classmethod
    def _get_converter(cls):
        if cls._converter is not None:
            return cls._converter
        try:
            from marker.converters.pdf import PdfConverter  # type: ignore
            from marker.models import create_model_dict  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "marker-pdf not installed. Install with: pip install marker-pdf\n"
                "Note: this brings ~2GB deps (torch + transformers)."
            ) from e
        log.info("[marker] loading models (one-time, may take ~30s)...")
        cls._converter = PdfConverter(artifact_dict=create_model_dict())
        return cls._converter

    def parse(self, path: Path, ctx: ParseContext) -> ParsedDoc:
        converter = self._get_converter()
        try:
            from marker.output import text_from_rendered  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("marker-pdf API not available") from e

        rendered = converter(str(path))
        full_md, _meta, images = text_from_rendered(rendered)

        # Marker 给的是 markdown 形式 → 我们重新拆成 pages + toc。
        # Marker 1.x 输出会保留 "Page N" 锚点；但更稳的是按章节切。
        pages = self._md_to_pages(full_md, images, ctx, path)
        toc = self._md_to_toc(full_md)

        return ParsedDoc(
            doc_id=ctx.doc_id,
            source_path=str(path),
            pages=pages,
            metadata=ctx.metadata,
            toc=toc,
            parser=self.name,
            parser_version=self.version,
        )

    # ----- markdown → pages -----

    @staticmethod
    def _md_to_pages(md: str, images: dict, ctx, path: Path) -> list[ParsedPage]:
        """Marker 输出整篇 markdown，我们按 page 标志切；找不到就单页。"""
        import re
        # marker 偶尔会用 "<!-- page: N -->" 锚点
        chunks = re.split(r"<!--\s*page:\s*(\d+)\s*-->", md)
        if len(chunks) >= 3:
            # 形如 [pre, '1', text1, '2', text2, ...]
            pages: list[ParsedPage] = []
            for i in range(1, len(chunks), 2):
                pno = int(chunks[i])
                text = chunks[i + 1] if i + 1 < len(chunks) else ""
                pages.append(_md_chunk_to_page(text, pno, images, ctx))
            return pages

        # 找不到 page 锚 → 整篇当作单页
        return [_md_chunk_to_page(md, 1, images, ctx)]

    @staticmethod
    def _md_to_toc(md: str) -> list[TocEntry]:
        import re
        out: list[TocEntry] = []
        counter = [0] * 6
        for line in md.splitlines():
            m = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
            if not m:
                continue
            level = len(m.group(1))
            counter[level - 1] += 1
            for j in range(level, 6):
                counter[j] = 0
            path_str = ".".join(str(c) for c in counter[:level] if c > 0)
            out.append(TocEntry(
                level=level, title=m.group(2).strip(),
                section_path=path_str or None,
            ))
        return out


def _md_chunk_to_page(md: str, page_no: int, images: dict, ctx) -> ParsedPage:
    """把一段 markdown 切成 ParsedPage（含 text_blocks / tables / figures）。"""
    import re

    text_blocks: list[TextBlock] = []
    tables: list[ParsedTable] = []
    figures: list[ParsedFigure] = []
    order = 0
    lines = md.splitlines()
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # 标题
        m = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if m:
            text_blocks.append(TextBlock(
                text=m.group(2).strip(), reading_order=order,
                is_heading=True, heading_level=len(m.group(1)),
            ))
            order += 1; i += 1; continue

        # 图片 ![alt](path)
        m = re.match(r"^!\[(.*?)\]\(([^)]+)\)\s*$", stripped)
        if m:
            alt, src = m.group(1), m.group(2)
            # marker 返回的 images dict：path → bytes；保存到 cache_dir
            img_path = _save_marker_image(src, images, ctx, page_no)
            figures.append(ParsedFigure(
                image_path=img_path, caption=alt or None,
            ))
            i += 1; continue

        # 表格（GFM 风格）
        if "|" in stripped and i + 1 < n and re.match(r"^\s*\|?[\s\-:|]+\|?\s*$", lines[i + 1]):
            headers = [c.strip() for c in stripped.strip("|").split("|")]
            rows = []
            j = i + 2
            while j < n and "|" in lines[j]:
                row = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                if any(row): rows.append(row)
                j += 1
            tables.append(ParsedTable(headers=headers, rows=rows))
            i = j; continue

        # 普通段落 / 列表
        if stripped:
            text_blocks.append(TextBlock(text=stripped, reading_order=order))
            order += 1
        i += 1

    return ParsedPage(
        page_no=page_no,
        text_blocks=text_blocks,
        tables=tables,
        figures=figures,
    )


def _save_marker_image(src: str, images: dict, ctx, page_no: int) -> str | None:
    """把 marker 返回的图片字节保存到 cache_dir，返回相对路径。"""
    from pathlib import Path
    if ctx.cache_dir is None:
        return src
    cache_dir = Path(ctx.cache_dir)
    img_dir = cache_dir / "figures"
    img_dir.mkdir(parents=True, exist_ok=True)
    # marker 1.x 给的 images 是 {filename: PIL.Image} 或 {filename: bytes}
    payload = images.get(src) if isinstance(images, dict) else None
    if payload is None:
        return src
    fname = Path(src).name
    out_path = img_dir / fname
    try:
        if hasattr(payload, "save"):  # PIL
            payload.save(str(out_path))
        elif isinstance(payload, (bytes, bytearray)):
            out_path.write_bytes(payload)
        else:
            return src
    except Exception:
        return src
    return str(out_path)
