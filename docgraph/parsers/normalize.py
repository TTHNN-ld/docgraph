"""Normalize parser-derived views into the authoritative L0 block model."""

from __future__ import annotations

from docgraph.graph.schema import Block, BlockKind, ParsedPage, TableData


def populate_l0_blocks(page: ParsedPage, *, doc_id: str, parser: str) -> ParsedPage:
    """Populate stable L0 blocks when an adapter only produced derived views."""
    if page.blocks:
        return page

    blocks: list[Block] = []
    section_path: str | None = None
    section_counters = [0] * 9

    for text_block in sorted(page.text_blocks, key=lambda item: item.reading_order):
        if text_block.is_heading:
            level = max(1, min(text_block.heading_level or 1, len(section_counters)))
            section_counters[level - 1] += 1
            for index in range(level, len(section_counters)):
                section_counters[index] = 0
            section_path = ".".join(str(value) for value in section_counters[:level] if value)
            kind = BlockKind.HEADING
        elif text_block.text.lstrip().startswith("```"):
            kind = BlockKind.CODE
        else:
            kind = BlockKind.PARAGRAPH
        blocks.append(
            Block(
                id=_block_id(doc_id, page.page_no, len(blocks)),
                doc_id=doc_id,
                page=page.page_no,
                kind=kind,
                reading_order=len(blocks),
                bbox=text_block.bbox,
                text=text_block.text,
                section_path=section_path,
                heading_level=text_block.heading_level,
                attrs={"parser": parser},
            )
        )

    for table in page.tables:
        n_cols = max([len(table.headers), *(len(row) for row in table.rows)] or [0])
        blocks.append(
            Block(
                id=_block_id(doc_id, page.page_no, len(blocks)),
                doc_id=doc_id,
                page=page.page_no,
                kind=BlockKind.TABLE,
                reading_order=len(blocks),
                bbox=table.bbox,
                table=TableData(
                    headers=table.headers,
                    rows=table.rows,
                    n_rows=len(table.rows),
                    n_cols=n_cols,
                    caption=table.caption,
                    html=table.html,
                ),
                text=table.caption,
                section_path=section_path,
                attrs={"parser": parser, "table_source": "cells"},
            )
        )

    for figure in page.figures:
        blocks.append(
            Block(
                id=_block_id(doc_id, page.page_no, len(blocks)),
                doc_id=doc_id,
                page=page.page_no,
                kind=BlockKind.FIGURE,
                reading_order=len(blocks),
                bbox=figure.bbox,
                text=figure.caption,
                image_path=figure.image_path,
                section_path=section_path,
                attrs={"parser": parser},
            )
        )

    for formula in page.formulas:
        blocks.append(
            Block(
                id=_block_id(doc_id, page.page_no, len(blocks)),
                doc_id=doc_id,
                page=page.page_no,
                kind=BlockKind.FORMULA,
                reading_order=len(blocks),
                bbox=formula.bbox,
                latex=formula.latex,
                section_path=section_path,
                attrs={"parser": parser},
            )
        )

    page.blocks = blocks
    return page


def _block_id(doc_id: str, page_no: int, order: int) -> str:
    return f"{doc_id}#p{page_no}#b{order}"
