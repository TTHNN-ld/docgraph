"""L2 extraction candidates built from L1 chunks and L0 blocks.

L2 extractors should consume these candidates instead of independently walking
raw pages. This keeps source provenance explicit and auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from docgraph.chunker import chunk_doc
from docgraph.graph.schema import Block, BlockKind, Chunk, ParsedDoc, TableData

CandidateKind = Literal["table", "text", "table_image", "page_image", "figure"]


@dataclass(frozen=True)
class EntityCandidate:
    id: str
    kind: CandidateKind
    doc_id: str
    page: int
    page_start: int | None
    page_end: int | None
    section_id: str | None
    chunk_id: str | None
    block_ids: list[str]
    text: str
    table: TableData | None = None
    image_path: str | None = None
    table_source: str | None = None
    chunk_ids: list[str] = field(default_factory=list)

    @property
    def source_chunk_ids(self) -> list[str]:
        if self.chunk_ids:
            return list(self.chunk_ids)
        return [self.chunk_id] if self.chunk_id else []


def build_entity_candidates(doc: ParsedDoc) -> list[EntityCandidate]:
    blocks_by_id = {block.id: block for page in doc.pages for block in page.blocks}
    blocks_by_page: dict[int, list[Block]] = {
        page.page_no: sorted(page.blocks, key=lambda b: b.reading_order) for page in doc.pages
    }
    candidates: list[EntityCandidate] = []

    chunks = chunk_doc(doc)
    for chunk in chunks:
        blocks = [blocks_by_id[bid] for bid in chunk.block_ids if bid in blocks_by_id]
        if not blocks:
            continue
        if (chunk.chunk_type or chunk.kind) in {"table", "logical_table"}:
            candidates.extend(_table_candidates(chunk, blocks, blocks_by_page))
        elif (chunk.chunk_type or chunk.kind) == "figure":
            candidates.extend(_figure_candidates(chunk, blocks))
        else:
            candidates.append(_text_candidate(chunk))

    chunk_ids_by_page = _chunk_ids_by_page(chunks)
    for page in doc.pages:
        if not page.rendered_image_path:
            continue
        candidates.append(
            EntityCandidate(
                id=f"{doc.doc_id}#candidate_page_image_p{page.page_no}",
                kind="page_image",
                doc_id=doc.doc_id,
                page=page.page_no,
                page_start=page.page_no,
                page_end=page.page_no,
                section_id=None,
                chunk_id=None,
                chunk_ids=chunk_ids_by_page.get(page.page_no, []),
                block_ids=[b.id for b in page.blocks],
                text="\n".join(b.text or "" for b in page.blocks if b.text),
                image_path=page.rendered_image_path,
            )
        )

    return candidates


def _table_candidates(
    chunk: Chunk,
    blocks: list[Block],
    blocks_by_page: dict[int, list[Block]],
) -> list[EntityCandidate]:
    table_blocks = [b for b in blocks if b.kind == BlockKind.TABLE and b.table is not None]
    if not table_blocks:
        return []
    table = _merge_table_data(table_blocks)
    if not table.caption:
        caption = _nearest_table_heading(table_blocks, blocks_by_page)
        if caption:
            table = table.model_copy(update={"caption": caption})
    image_path = next((b.image_path for b in table_blocks if b.image_path), None)
    table_source = next(
        (b.attrs.get("table_source") for b in table_blocks if b.attrs.get("table_source")), None
    )
    base = EntityCandidate(
        id=f"{chunk.id}#candidate_table",
        kind="table",
        doc_id=chunk.doc_id,
        page=chunk.page or chunk.page_start or table_blocks[0].page,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        section_id=chunk.section_id,
        chunk_id=chunk.id,
        chunk_ids=[chunk.id],
        block_ids=[b.id for b in table_blocks],
        text=chunk.text,
        table=table,
        image_path=image_path,
        table_source=table_source,
    )
    out = [base]
    if image_path and not (table.headers or table.rows or table.html):
        out.append(
            EntityCandidate(
                **{
                    **base.__dict__,
                    "id": f"{chunk.id}#candidate_table_image",
                    "kind": "table_image",
                }
            )
        )
    return out


def _nearest_table_heading(
    table_blocks: list[Block],
    blocks_by_page: dict[int, list[Block]],
) -> str | None:
    """Find the closest preceding heading/text label for captionless tables."""
    if not table_blocks:
        return None
    first = sorted(table_blocks, key=lambda b: (b.page, b.reading_order))[0]
    page_blocks = blocks_by_page.get(first.page, [])
    before = [
        b
        for b in page_blocks
        if b.reading_order < first.reading_order
        and b.kind in {BlockKind.HEADING, BlockKind.CAPTION, BlockKind.PARAGRAPH}
        and (b.text or "").strip()
    ]
    heading_like = [b for b in before if b.kind in {BlockKind.HEADING, BlockKind.CAPTION}]
    for block in reversed(heading_like[-8:]):
        text = " ".join((block.text or "").split())
        if _looks_like_table_context(text):
            return text[:240]
    for block in reversed(before[-4:]):
        if block.kind != BlockKind.PARAGRAPH:
            continue
        text = " ".join((block.text or "").split())
        if len(text) <= 120 and _looks_like_table_context(text):
            return text[:240]
    return None


def _looks_like_table_context(text: str) -> bool:
    low = text.lower()
    if not low or len(low) > 240:
        return False
    return any(
        token in low
        for token in (
            "register",
            "bit",
            "field",
            "address",
            "offset",
            "pin",
            "signal",
            "interface",
            "interrupt",
            "memory map",
            "寄存器",
            "位域",
            "地址",
            "信号",
            "接口",
            "中断",
        )
    )


def _figure_candidates(chunk: Chunk, blocks: list[Block]) -> list[EntityCandidate]:
    out: list[EntityCandidate] = []
    for block in blocks:
        if block.kind != BlockKind.FIGURE:
            continue
        out.append(
            EntityCandidate(
                id=f"{chunk.id}#candidate_figure",
                kind="figure",
                doc_id=chunk.doc_id,
                page=block.page,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                section_id=chunk.section_id,
                chunk_id=chunk.id,
                chunk_ids=[chunk.id],
                block_ids=[block.id],
                text=block.text or chunk.text,
                image_path=block.image_path,
            )
        )
    return out


def _text_candidate(chunk: Chunk) -> EntityCandidate:
    return EntityCandidate(
        id=f"{chunk.id}#candidate_text",
        kind="text",
        doc_id=chunk.doc_id,
        page=chunk.page or chunk.page_start or 0,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        section_id=chunk.section_id,
        chunk_id=chunk.id,
        chunk_ids=[chunk.id],
        block_ids=list(chunk.block_ids),
        text=chunk.text,
    )


def _merge_table_data(blocks: list[Block]) -> TableData:
    if len(blocks) == 1 and blocks[0].table is not None:
        return blocks[0].table
    headers: list[str] = []
    rows: list[list[str]] = []
    captions: list[str] = []
    html_parts: list[str] = []
    for block in blocks:
        table = block.table
        if table is None:
            continue
        if not headers and table.headers:
            headers = list(table.headers)
        rows.extend(table.rows or [])
        if table.caption:
            captions.append(table.caption)
        if table.html:
            html_parts.append(table.html)
    return TableData(
        headers=headers,
        rows=rows,
        n_rows=len(rows),
        n_cols=max([len(headers), *(len(r) for r in rows)] or [0]),
        caption="\n".join(dict.fromkeys(captions)) or None,
        html="\n".join(html_parts) or None,
    )


def _chunk_ids_by_page(chunks: list[Chunk]) -> dict[int, list[str]]:
    """Map each rendered page to L1 chunks that can explain it."""
    out: dict[int, list[str]] = {}
    for chunk in chunks:
        start = chunk.page_start or chunk.page
        end = chunk.page_end or chunk.page_start or chunk.page
        if start is None or end is None:
            continue
        for page in range(int(start), int(end) + 1):
            out.setdefault(page, []).append(chunk.id)
    return out
