"""L1 切块器 —— 把 L0 Block 切成可检索、可回溯的 Chunk。

规则（layered-architecture.md §3.2）：
- 每个 table block → 独立 chunk（整表不切碎）
- 每个 figure block → 独立 chunk
- 连续 paragraph/heading 按 section 归并，超 MAX_TOKENS 则滑窗切
- 每个 chunk 带 block_ids 反查 L0

不依赖文档类型，完全通用。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from docgraph.core.ids import content_hash, make_node_id, normalize_name
from docgraph.graph.schema import Block, BlockKind, Chunk, NodeKind, ParsedDoc, TocEntry


# token 粗估：4 char ≈ 1 token
_CHARS_PER_TOKEN = 4
MAX_CHUNK_CHARS = 2000  # ~500 tokens
MIN_CHUNK_CHARS = 80


@dataclass
class ChunkReport:
    total: int = 0
    by_kind: dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.by_kind is None:
            self.by_kind = {}


def chunk_doc(doc: ParsedDoc) -> list[Chunk]:
    """对整份 ParsedDoc 切块。

    L1 is section-aware: continuous prose can span pages within the same
    section, while tables and figures remain independently addressable chunks.
    """
    section_index = _section_index(doc)
    chunks: list[Chunk] = []
    blocks = [
        b for page in doc.pages for b in page.blocks
    ]
    chunks.extend(_chunk_blocks(doc.doc_id, blocks, section_index))
    return _merge_logical_tables(chunks)


def _chunk_blocks(
    doc_id: str,
    blocks: list[Block],
    section_index: dict[str, str],
) -> list[Chunk]:
    out: list[Chunk] = []
    section_path: str | None = None
    section_node_id: str | None = None
    buf_blocks: list[Block] = []
    buf_text: list[str] = []

    def flush(kind: str = "section"):
        nonlocal buf_blocks, buf_text
        if not buf_blocks:
            return
        text = "\n".join(buf_text).strip()
        if not text:
            buf_blocks = []; buf_text = []
            return
        page_start = min(b.page for b in buf_blocks)
        page_end = max(b.page for b in buf_blocks)
        block_ids = [b.id for b in buf_blocks]
        chunk_idx = len(out)
        out.append(Chunk(
            id=_chunk_id(doc_id, kind, section_path, page_start, chunk_idx),
            doc_id=doc_id, page=page_start,
            page_start=page_start, page_end=page_end,
            section_id=section_path,
            section_node_id=section_node_id,
            text=text,
            hash=content_hash(text),
            source_hash=content_hash("|".join(block_ids)),
            block_ids=block_ids,
            kind=kind,
            chunk_type=kind,
            attrs={
                **({"section_path": section_path} if section_path else {}),
                **({"section_node_id": section_node_id} if section_node_id else {}),
                "page_start": page_start,
                "page_end": page_end,
                "chunk_type": kind,
            },
        ))
        buf_blocks = []; buf_text = []

    for b in sorted(blocks, key=lambda x: (x.page, x.reading_order)):
        block_section_path = _block_section_path(b, section_index, section_path)
        if b.kind == BlockKind.HEADING:
            flush()
            section_path = block_section_path
            section_node_id = section_index.get(section_path or "")
        elif block_section_path and block_section_path != section_path:
            flush()
            section_path = block_section_path
            section_node_id = section_index.get(section_path or "")

        # table / figure 独立成 chunk（先 flush 累积的 section 文本）
        if b.kind == BlockKind.TABLE:
            flush()
            out.append(_block_to_chunk(
                doc_id, b, len(out), "table", section_path, section_node_id,
            ))
            continue
        if b.kind == BlockKind.FIGURE:
            flush()
            out.append(_block_to_chunk(
                doc_id, b, len(out), "figure", section_path, section_node_id,
            ))
            continue

        buf_blocks.append(b)
        buf_text.append(b.text or "")
        if sum(len(t) for t in buf_text) >= MAX_CHUNK_CHARS:
            flush()

    flush()
    return out


def _block_to_chunk(
    doc_id: str,
    b: Block,
    idx: int,
    kind: str,
    section_path: str | None,
    section_node_id: str | None,
) -> Chunk:
    text = b.text or ""
    table_profile = None
    if b.kind == BlockKind.TABLE and b.table:
        # 表格 → 文本表示：markdown 表
        t = b.table
        table_profile = _table_profile(b)
        lines = []
        if t.caption:
            lines.append(t.caption)
        if table_profile:
            lines.append(f"[table_kind={table_profile['kind']}]")
            if table_profile.get("continued"):
                lines.append("[table_continued=true]")
        table_source = b.attrs.get("table_source")
        if table_source:
            lines.append(f"[table_source={table_source}]")
        if t.headers:
            lines.append("| " + " | ".join(t.headers) + " |")
            lines.append("|" + "|".join(["---"] * len(t.headers)) + "|")
        for row in t.rows:
            lines.append("| " + " | ".join(str(c) for c in row) + " |")
        if b.image_path:
            lines.append(f"[table_image={b.image_path}]")
        text = "\n".join(lines)
    return Chunk(
        id=_chunk_id(doc_id, kind, section_path or b.section_path, b.page, idx),
        doc_id=doc_id, page=b.page,
        page_start=b.page,
        page_end=b.page,
        section_id=section_path or b.section_path,
        section_node_id=section_node_id,
        text=text or "(empty)",
        hash=content_hash(text),
        source_hash=content_hash(b.id),
        block_ids=[b.id],
        kind=kind,
        chunk_type=kind,
        attrs={
            **({"section_path": section_path or b.section_path} if (section_path or b.section_path) else {}),
            **({"section_node_id": section_node_id} if section_node_id else {}),
            "page_start": b.page,
            "page_end": b.page,
            "chunk_type": kind,
            **({"image_path": b.image_path} if b.image_path else {}),
            **({"table_source": b.attrs.get("table_source")} if b.attrs.get("table_source") else {}),
            **({"table_profile": table_profile} if table_profile else {}),
        },
    )


def _merge_logical_tables(chunks: list[Chunk]) -> list[Chunk]:
    """Merge adjacent chunks that belong to the same logical table.

    This is intentionally conservative. It only merges table chunks with the
    same continued group on nearby pages, leaving ambiguous tables separate.
    """
    out: list[Chunk] = []
    i = 0
    while i < len(chunks):
        cur = chunks[i]
        if cur.kind != "table":
            out.append(cur)
            i += 1
            continue

        group = [cur]
        j = i + 1
        while j < len(chunks) and chunks[j].kind == "table" and _same_logical_table(group[-1], chunks[j]):
            group.append(chunks[j])
            j += 1

        if len(group) == 1:
            out.append(cur)
        else:
            out.append(_merge_table_group(group))
        i = j
    return out


def _same_logical_table(left: Chunk, right: Chunk) -> bool:
    lp = left.attrs.get("table_profile") or {}
    rp = right.attrs.get("table_profile") or {}
    if not lp or not rp:
        return False
    if lp.get("group_key") != rp.get("group_key"):
        return False
    if lp.get("kind") != rp.get("kind"):
        return False
    if right.page_start and left.page_end and right.page_start - left.page_end > 1:
        return False
    return bool(lp.get("continued") or rp.get("continued") or lp.get("caption") or rp.get("caption"))


def _merge_table_group(group: list[Chunk]) -> Chunk:
    first = group[0]
    page_start = min(c.page_start or c.page or 0 for c in group) or first.page
    page_end = max(c.page_end or c.page or 0 for c in group) or first.page
    block_ids = [bid for c in group for bid in c.block_ids]
    text = "\n\n[logical_table_part]\n\n".join(c.text for c in group)
    profile = dict(first.attrs.get("table_profile") or {})
    flags = set(profile.get("quality_flags") or [])
    flags.add("logical_table")
    flags.add("merged_from_blocks")
    if any((c.attrs.get("table_profile") or {}).get("continued") for c in group):
        flags.add("continued")
    profile["quality_flags"] = sorted(flags)
    profile["logical_table_parts"] = len(group)
    profile["page_start"] = page_start
    profile["page_end"] = page_end

    return Chunk(
        id=_chunk_id(first.doc_id, "table", first.section_id, page_start or first.page or 0, 0)
        + "_logical_" + content_hash("|".join(block_ids)).split(":", 1)[1][:10],
        doc_id=first.doc_id,
        page=page_start,
        page_start=page_start,
        page_end=page_end,
        section_id=first.section_id,
        section_node_id=first.section_node_id,
        text=text,
        hash=content_hash(text),
        source_hash=content_hash("|".join(block_ids)),
        block_ids=block_ids,
        kind="table",
        chunk_type="logical_table",
        attrs={
            **first.attrs,
            "page_start": page_start,
            "page_end": page_end,
            "chunk_type": "logical_table",
            "table_profile": profile,
            "logical_table_source_chunk_ids": [c.id for c in group],
        },
    )


def _table_profile(block: Block) -> dict:
    table = block.table
    headers = table.headers if table else []
    caption = table.caption if table else None
    rows = table.rows if table else []
    header_text = " ".join(str(h) for h in headers).lower()
    caption_text = (caption or "").lower()
    pool = f"{caption_text} {header_text}"

    kind = "generic_table"
    if "fields for register" in caption_text or _hits(pool, [
        "reg name", "register", "field", "msb", "lsb", "swaccess", "hwaccess", "reset",
    ]) >= 3:
        kind = "register_table"
    elif _hits(pool, ["base address", "address map", "memory map", "offset", "size", "地址映射", "基地址"]) >= 2:
        kind = "memory_map"
    elif _hits(pool, ["signal", "direction", "width", "interface group", "位宽", "信号"]) >= 2:
        kind = "signal_table"
    elif _hits(pool, ["pin", "direction", "function", "voltage", "管脚", "引脚"]) >= 2:
        kind = "pin_table"
    elif _hits(pool, ["min", "typ", "max", "unit", "condition", "时序"]) >= 2:
        kind = "timing_table"

    continued = bool(re.search(r"\bcontinued\b|续表|接上", caption_text, re.I))
    group_key = _table_group_key(caption, headers, kind, block.section_path)
    quality_flags: list[str] = []
    if continued:
        quality_flags.append("continued")
    if not caption:
        quality_flags.append("no_caption")
    if block.image_path:
        quality_flags.append("image_backed")
    if table and table.merged_cells:
        quality_flags.append("has_merged_cells")
    if not rows:
        quality_flags.append("empty_rows")
    if headers and any(len(row) != len(headers) for row in rows):
        quality_flags.append("ragged_rows")

    return {
        "kind": kind,
        "caption": caption,
        "continued": continued,
        "group_key": group_key,
        "headers": headers,
        "n_rows": len(rows),
        "n_cols": len(headers) or (max((len(r) for r in rows), default=0)),
        "quality_flags": quality_flags,
    }


def _hits(text: str, needles: list[str]) -> int:
    return sum(1 for n in needles if n.lower() in text)


def _table_group_key(
    caption: str | None,
    headers: list[str],
    kind: str,
    section_path: str | None,
) -> str:
    cap = " ".join((caption or "").split())
    cap = re.sub(r"\(?\s*continued\s*\)?", "", cap, flags=re.I).strip()
    cap = re.sub(r"（\s*continued\s*）", "", cap, flags=re.I).strip()
    if cap:
        table_no = re.search(r"\btable\s+([0-9A-Za-z_.\-–]+)", cap, re.I)
        if table_no:
            return f"caption:{normalize_name(table_no.group(1))}"
        return f"caption:{normalize_name(cap)[:80]}"
    header_sig = normalize_name("|".join(str(h).lower() for h in headers))[:120]
    return f"{kind}:{section_path or 'no_section'}:{header_sig}"


def _section_index(doc: ParsedDoc) -> dict[str, str]:
    family = doc.doc_id.split("::", 1)[0] if "::" in doc.doc_id else doc.doc_id
    out: dict[str, str] = {}
    for entry in doc.toc:
        path = entry.section_path or _infer_toc_path(entry)
        if not path or path in out:
            continue
        out[path] = make_node_id(family, NodeKind.SECTION, path, doc_id=doc.doc_id)
    # Parser TOC can be absent or incomplete; heading blocks still provide paths.
    for page in doc.pages:
        for b in page.blocks:
            if b.kind != BlockKind.HEADING:
                continue
            path = _heading_path(b.text)
            if path and path not in out:
                out[path] = make_node_id(family, NodeKind.SECTION, path, doc_id=doc.doc_id)
    return out


def _infer_toc_path(entry: TocEntry) -> str | None:
    return _heading_path(entry.title) or normalize_name(entry.title)[:64]


def _heading_path(text: str | None) -> str | None:
    import re
    s = text or ""
    m = re.match(r"^\s*(\d+(?:\.\d+){0,5})(?:\.(?=\s)|(?=[^\d.]|$))", s)
    if m:
        return m.group(1)
    m = re.match(r"^\s*Chapter\s+(\d+)(?=[^\d]|$)", s, re.I)
    if m:
        return m.group(1)
    m = re.match(r"^\s*Appendix\s+([A-Z])(?=[A-Z][a-z]|[^A-Za-z]|$)", s)
    if m:
        return m.group(1).upper()
    m = re.match(r"^\s*([A-Z]\.\d+(?:\.\d+){0,4})(?=[^\d.]|$)", s)
    if m:
        return m.group(1)
    return None


def _block_section_path(
    block: Block,
    section_index: dict[str, str],
    current_section_path: str | None,
) -> str | None:
    if block.kind == BlockKind.HEADING:
        explicit = _heading_path(block.text)
        if explicit and explicit in section_index:
            return explicit
        return current_section_path
    if block.section_path and block.section_path in section_index:
        return block.section_path
    return current_section_path


def _chunk_id(
    doc_id: str,
    kind: str,
    section_path: str | None,
    page: int,
    idx: int,
) -> str:
    if section_path:
        safe = normalize_name(section_path)
        return f"{doc_id}#c_{kind}_s{safe}_{idx}"
    return f"{doc_id}#c_{kind}_p{page}_{idx}"
