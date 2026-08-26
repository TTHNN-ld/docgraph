"""GlossaryExtractor —— 术语 / 缩写表。

策略：
- 找标题含 "Abbreviations / Acronyms / Glossary / 术语 / 缩写" 的章节
- 行级正则：缩写 + 全称
- 建 TERM 节点；后续 Linker 在缩写出现处建 ALIAS_OF 边
"""

from __future__ import annotations

import re
import time

from docgraph.core.ids import content_hash, make_node_id
from docgraph.extractors.base import ExtractContext
from docgraph.graph.schema import (
    BlockKind,
    Edge,
    ExtractResult,
    ExtractStats,
    Location,
    Node,
    NodeKind,
    ParsedDoc,
    ParsedPage,
)

_GLOSSARY_TITLE = re.compile(
    r"(?:abbreviations?|acronyms?|glossary|terminology|术语(?:表)?|缩写(?:表)?)",
    re.IGNORECASE,
)

# 行格式:
#  ABC      Acronym Bar Cluster
#  ABC  —  Acronym Bar Cluster
#  ABC : Acronym Bar Cluster
_GLOSS_LINE = re.compile(
    r"^(?P<abbr>[A-Z][A-Z0-9_/\-]{1,15})\s*(?:[—\-:：]|\s{2,})\s*(?P<full>.{3,200})$"
)


class GlossaryExtractor:
    name = "glossary"
    kinds = {NodeKind.TERM}
    requires = {"section"}
    version = "0.1"

    def extract(self, doc: ParsedDoc, ctx: ExtractContext) -> ExtractResult:
        t0 = time.time()
        nodes: list[Node] = []
        edges: list[Edge] = []
        seen: set[str] = set()

        # 找疑似术语章节：标题命中 _GLOSSARY_TITLE，或页内行集中匹配
        candidate_pages = self._find_glossary_pages(doc)
        for page in candidate_pages:
            # 段落/列表行（原有启发式）
            for abbr, full in self._extract_lines(page):
                if abbr in seen:
                    continue
                seen.add(abbr)
                nodes.append(self._make_term_node(abbr, full, page.page_no, doc, ctx))
            # 表格行：术语表常以表格形式出现（缩写 | 全称 | 描述）
            for abbr, full in self._extract_table_lines(page):
                if abbr in seen:
                    continue
                seen.add(abbr)
                nodes.append(self._make_term_node(abbr, full, page.page_no, doc, ctx))

        return ExtractResult(
            nodes=nodes,
            edges=edges,
            stats=ExtractStats(
                nodes_emitted=len(nodes),
                edges_emitted=len(edges),
                duration_s=round(time.time() - t0, 3),
            ),
        )

    def _find_glossary_pages(self, doc: ParsedDoc) -> list[ParsedPage]:
        # 从 TOC 取，附加全页扫描
        toc_pages: set[int] = set()
        for e in doc.toc:
            if e.title and _GLOSSARY_TITLE.search(e.title):
                if e.page:
                    toc_pages.add(e.page)
                    # 也扫后续几页（术语表常跨页）
                    for offset in range(1, 6):
                        toc_pages.add(e.page + offset)
        out: list[ParsedPage] = []
        for p in doc.pages:
            if p.page_no in toc_pages:
                out.append(p)
                continue
            # 兜底：页面内命中率高的也算（段落行 + 表格行）
            text_lines = list(self._extract_lines(p))
            table_lines = list(self._extract_table_lines(p))
            if len(text_lines) + len(table_lines) >= 5:
                out.append(p)
        return out

    def _make_term_node(
        self,
        abbr: str,
        full: str,
        page_no: int,
        doc: ParsedDoc,
        ctx: ExtractContext,
    ) -> Node:
        return Node(
            id=make_node_id(ctx.family, NodeKind.TERM, abbr, doc_id=doc.doc_id),
            kind=NodeKind.TERM,
            name=abbr,
            qualified_name=abbr,
            aliases=[full],
            doc_id=doc.doc_id,
            location=Location(page=page_no),
            attrs={"full": full, "source": "heuristic"},
            summary=f"{abbr}: {full}"[:120],
            hash=content_hash(f"{abbr}|{full}"),
        )

    def _extract_lines(self, page: ParsedPage):
        for text in _page_text_blocks(page):
            for ln in text.split("\n"):
                ln = ln.strip()
                m = _GLOSS_LINE.match(ln)
                if not m:
                    continue
                abbr = m.group("abbr")
                full = m.group("full").strip()
                # 排除明显非术语的（数字/小写开头）
                if len(abbr) < 2:
                    continue
                yield abbr, full

    def _extract_table_lines(self, page: ParsedPage):
        """从表格块中提取缩写-全称对。

        术语表常以表格形式出现，列标题包含「缩写/全称」或
        「Abbreviation/Full Name」等。
        """
        for block in sorted(page.blocks, key=lambda b: b.reading_order):
            if block.kind != BlockKind.TABLE:
                continue
            if block.table is None:
                continue
            headers = block.table.headers
            rows = block.table.rows
            if not headers or not rows:
                continue
            # 找缩略词列和全称列
            abbr_col = _find_abbreviation_column(headers)
            full_col = _find_full_name_column(headers)
            if abbr_col is None or full_col is None:
                continue
            for row in rows:
                if len(row) <= max(abbr_col, full_col):
                    continue
                abbr = (row[abbr_col] or "").strip()
                full = (row[full_col] or "").strip()
                if len(abbr) < 2:
                    continue
                if not full:
                    continue
                # 排除明显非术语的（数字开头或过长）
                if abbr[0].isdigit():
                    continue
                yield abbr, full


def _page_text_blocks(page: ParsedPage) -> list[str]:
    kinds = {BlockKind.HEADING, BlockKind.PARAGRAPH, BlockKind.LIST}
    return [
        block.text
        for block in sorted(page.blocks, key=lambda b: b.reading_order)
        if block.kind in kinds and block.text
    ]


# 表头关键词：缩略词列
_ABBR_HEADER = re.compile(
    r"(?:abbreviations?|acronyms?|缩写|简称|abbr\.?)",
    re.IGNORECASE,
)

# 表头关键词：全称列
_FULL_HEADER = re.compile(
    r"(?:full\s*(?:name|form)|全称|术语|description|描述|meaning|含义)",
    re.IGNORECASE,
)


def _find_abbreviation_column(headers: list[str]) -> int | None:
    """在表头中寻找缩略词/缩写列。"""
    for i, h in enumerate(headers):
        if _ABBR_HEADER.search(h):
            return i
    return None


def _find_full_name_column(headers: list[str]) -> int | None:
    """在表头中寻找全称列。"""
    for i, h in enumerate(headers):
        if _FULL_HEADER.search(h):
            return i
    return None
