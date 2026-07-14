"""PDF profiling and parser routing.

The router keeps the default PDF path small and maintainable:
PyMuPDF inspects every PDF cheaply, then routes to Docling, MinerU, or PyMuPDF.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PdfProfile:
    page_count: int = 0
    file_size: int = 0
    text_chars_per_page: float = 0.0
    image_count_per_page: float = 0.0
    image_area_ratio: float = 0.0
    has_extractable_text: bool = False
    is_probably_scanned: bool = False
    is_tagged_pdf: bool = False
    table_candidate_count: int = 0
    register_keyword_count: int = 0
    cjk_ratio: float = 0.0


@dataclass(frozen=True)
class ParseQualityVerdict:
    ok: bool
    reason: str | None = None


_TABLE_HINTS = (
    "table ",
    "表 ",
)

_REGISTER_HINTS = (
    "register",
    "bit field",
    "bitfield",
    "bit assignments",
    "bit description",
    "register description",
    "register summary",
    "address offset",
    "reset value",
    "swaccess",
    "hwaccess",
    "寄存器",
    "位域",
    "复位值",
    "地址偏移",
)


def inspect_pdf(path: Path, *, max_pages: int = 16) -> PdfProfile:
    """Cheaply inspect a PDF with PyMuPDF for parser routing."""
    try:
        import pymupdf  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("pymupdf is required for PDF routing") from e

    doc = pymupdf.open(str(path))
    try:
        page_count = len(doc)
        sample_count = min(max_pages, page_count)
        text_chars = 0
        cjk_chars = 0
        image_count = 0
        image_area = 0.0
        page_area = 0.0
        table_candidates = 0
        register_keywords = 0

        metadata = doc.metadata or {}
        is_tagged = _looks_tagged(doc, metadata)

        for page in list(doc)[:sample_count]:
            text = page.get_text("text") or ""
            text_chars += len(text.strip())
            cjk_chars += sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
            lower = text.lower()
            table_candidates += sum(lower.count(h) for h in _TABLE_HINTS)
            register_keywords += sum(lower.count(h) for h in _REGISTER_HINTS)

            rect = page.rect
            page_area += max(1.0, float(rect.width) * float(rect.height))
            try:
                images = page.get_image_info()
            except Exception:
                images = []
            image_count += len(images)
            for info in images:
                bbox = info.get("bbox")
                if bbox and len(bbox) == 4:
                    image_area += abs((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))

        sampled = max(sample_count, 1)
        chars_per_page = text_chars / sampled
        image_count_per_page = image_count / sampled
        image_area_ratio = min(1.0, image_area / max(page_area, 1.0))
        has_text = chars_per_page >= 80
        scan_like = not has_text and (image_count_per_page >= 0.5 or image_area_ratio >= 0.25)
        cjk_ratio = cjk_chars / max(text_chars, 1)
        return PdfProfile(
            page_count=page_count,
            file_size=path.stat().st_size,
            text_chars_per_page=round(chars_per_page, 2),
            image_count_per_page=round(image_count_per_page, 2),
            image_area_ratio=round(image_area_ratio, 3),
            has_extractable_text=has_text,
            is_probably_scanned=scan_like,
            is_tagged_pdf=is_tagged,
            table_candidate_count=table_candidates,
            register_keyword_count=register_keywords,
            cjk_ratio=round(cjk_ratio, 3),
        )
    finally:
        doc.close()


def choose_pdf_parser(profile: PdfProfile, *, quality: str) -> str:
    """Choose the primary parser for a PDF profile.

    The rules intentionally stay conservative:
    - fast mode is always PyMuPDF;
    - scanned or image-heavy PDFs go to MinerU;
    - born-digital/tagged/table-heavy PDFs go to Docling;
    - uncertain balanced PDFs prefer Docling, accurate PDFs prefer MinerU.
    """
    normalized = (quality or "balanced").strip().lower()
    image_heavy = (
        profile.image_area_ratio >= 0.35
        or profile.image_count_per_page >= 2.0
    )
    table_density = profile.table_candidate_count / max(profile.page_count, 1)
    register_density = profile.register_keyword_count / max(profile.page_count, 1)
    table_heavy = profile.table_candidate_count >= 8 or table_density >= 0.15
    register_dense = (
        profile.register_keyword_count >= 12
        or register_density >= 0.2
        or (profile.register_keyword_count >= 4 and profile.table_candidate_count >= 2)
    )

    match normalized:
        case "fast":
            return "pymupdf"
        case "accurate" if profile.is_probably_scanned or image_heavy:
            return "mineru"
        case "accurate" if register_dense:
            return "mineru"
        case "accurate" if profile.has_extractable_text and (profile.is_tagged_pdf or table_heavy):
            return "docling"
        case "accurate":
            return "mineru"
        case "balanced" if profile.is_probably_scanned or image_heavy:
            return "mineru"
        case "balanced" if profile.has_extractable_text:
            return "docling"
        case "balanced":
            return "mineru"
        case _:
            return "docling" if profile.has_extractable_text else "mineru"


def pdf_parser_chain(
    *,
    configured_primary: str,
    configured_fallback: list[str],
    quality: str,
    profile: PdfProfile | None,
) -> tuple[str, list[str]]:
    """Return a de-duplicated PDF parser chain."""
    primary = (configured_primary or "auto").strip().lower()
    if primary == "auto":
        if profile is None:
            chosen = "pymupdf" if quality == "fast" else "docling"
        else:
            chosen = choose_pdf_parser(profile, quality=quality)
        chain = [chosen, "docling", "mineru", "pymupdf", *configured_fallback]
    else:
        chain = [primary, *configured_fallback]
        if quality == "fast":
            chain = ["pymupdf", *chain]
    deduped = list(dict.fromkeys(name for name in chain if name))
    return deduped[0], deduped[1:]


def assess_pdf_parse(parsed: Any, profile: PdfProfile | None) -> ParseQualityVerdict:
    """Reject empty results and scan-like PyMuPDF results without useful text."""
    blocks = [block for page in parsed.pages for block in page.blocks]
    if not blocks:
        return ParseQualityVerdict(False, "parser returned no L0 blocks")

    if profile is None or not profile.is_probably_scanned or parsed.parser != "pymupdf":
        return ParseQualityVerdict(True)

    text_chars = sum(len((getattr(block, "text", None) or "").strip()) for block in blocks)
    minimum = max(80, profile.page_count * 20)
    if text_chars < minimum:
        return ParseQualityVerdict(
            False,
            f"scan-like PDF yielded only {text_chars} text characters with PyMuPDF "
            f"(minimum {minimum})",
        )
    return ParseQualityVerdict(True)


def _looks_tagged(doc, metadata: dict) -> bool:
    raw = " ".join(str(v or "") for v in metadata.values()).lower()
    if "word" in raw or "microsoft" in raw:
        return True
    try:
        return bool(getattr(doc, "is_tagged", False))
    except Exception:
        return False
