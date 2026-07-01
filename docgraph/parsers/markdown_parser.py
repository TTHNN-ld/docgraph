"""Markdown parser。

策略：
- 用 markdown-it-py 解析（已是 docgraph 的依赖）
- 标题（#, ##, ...）→ TocEntry
- 段落 / 代码块 → TextBlock
- 表格 → ParsedTable
- 图片 link → ParsedFigure（caption = alt 文本，image_path = src）

把 markdown 整体当作单页；多文件不在 parser 这层处理（pipeline 层按文件粒度）。
"""
from __future__ import annotations

from pathlib import Path

from docgraph.graph.schema import (
    ParsedDoc,
    ParsedFigure,
    ParsedPage,
    ParsedTable,
    TextBlock,
    TocEntry,
)
from docgraph.parsers.base import ParseContext


class MarkdownParser:
    name = "markdown"
    supports = {".md", ".markdown"}
    version = "0.1"

    def can_parse(self, path: Path) -> bool:
        return path.suffix.lower() in self.supports

    def parse(self, path: Path, ctx: ParseContext) -> ParsedDoc:
        try:
            from markdown_it import MarkdownIt  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "markdown-it-py not installed; should come with docgraph base"
            ) from e

        text = path.read_text("utf-8")
        md = MarkdownIt("commonmark", {"breaks": False, "html": False}).enable("table")
        tokens = md.parse(text)

        toc: list[TocEntry] = []
        text_blocks: list[TextBlock] = []
        tables: list[ParsedTable] = []
        figures: list[ParsedFigure] = []
        heading_counter = [0] * 6
        order = 0

        i = 0
        n = len(tokens)
        while i < n:
            tok = tokens[i]
            if tok.type == "heading_open":
                level = int(tok.tag[1])  # h1..h6
                # 下一个 inline token 是标题文本
                title_tok = tokens[i + 1] if i + 1 < n else None
                title = title_tok.content if title_tok else ""
                heading_counter[level - 1] += 1
                for k in range(level, 6):
                    heading_counter[k] = 0
                path_str = ".".join(
                    str(c) for c in heading_counter[:level] if c > 0
                )
                toc.append(
                    TocEntry(level=level, title=title, section_path=path_str or None)
                )
                text_blocks.append(
                    TextBlock(
                        text=title,
                        reading_order=order,
                        is_heading=True,
                        heading_level=level,
                    )
                )
                order += 1
                i += 3  # heading_open, inline, heading_close
                continue

            if tok.type == "paragraph_open":
                inline = tokens[i + 1] if i + 1 < n else None
                content = inline.content if inline else ""
                if content:
                    text_blocks.append(
                        TextBlock(text=content, reading_order=order)
                    )
                    order += 1
                    # 抽 inline 内的图片
                    if inline and inline.children:
                        for c in inline.children:
                            if c.type == "image":
                                src = c.attrs.get("src", "") if hasattr(c, "attrs") else ""
                                alt = c.content or ""
                                if isinstance(src, str) and src:
                                    figures.append(
                                        ParsedFigure(
                                            image_path=src,
                                            caption=alt or None,
                                        )
                                    )
                i += 3
                continue

            if tok.type == "fence" or tok.type == "code_block":
                code = tok.content or ""
                if code.strip():
                    text_blocks.append(
                        TextBlock(
                            text=f"```{tok.info or ''}\n{code}\n```",
                            reading_order=order,
                        )
                    )
                    order += 1
                i += 1
                continue

            if tok.type == "table_open":
                # 收集到 table_close
                j = i + 1
                headers: list[str] = []
                rows: list[list[str]] = []
                cur_row: list[str] = []
                in_header = False
                while j < n and tokens[j].type != "table_close":
                    t = tokens[j]
                    if t.type == "thead_open":
                        in_header = True
                    elif t.type == "thead_close":
                        in_header = False
                    elif t.type == "tr_open":
                        cur_row = []
                    elif t.type == "tr_close":
                        if in_header and not headers:
                            headers = cur_row
                        else:
                            rows.append(cur_row)
                    elif t.type == "inline":
                        cur_row.append(t.content or "")
                    j += 1
                if headers or rows:
                    tables.append(ParsedTable(headers=headers, rows=rows))
                i = j + 1
                continue

            i += 1

        single_page = ParsedPage(
            page_no=1,
            text_blocks=text_blocks,
            tables=tables,
            figures=figures,
        )

        return ParsedDoc(
            doc_id=ctx.doc_id,
            source_path=str(path),
            pages=[single_page],
            metadata=ctx.metadata,
            toc=toc,
            parser=self.name,
            parser_version=self.version,
        )
