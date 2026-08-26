"""SectionExtractor —— 从 TOC 和 L0 heading block 构建章节树。

1. 优先使用 PDF 自带 TOC。
2. TOC 为空时回退：扫描 L0 heading block，按编号判断层级。
3. 输出 SECTION 节点 + 父子 CONTAINS 边。
"""

from __future__ import annotations

import re
import time

from docgraph.core.ids import content_hash, make_node_id, normalize_name
from docgraph.extractors.base import ExtractContext
from docgraph.graph.schema import (
    BlockKind,
    Edge,
    EdgeKind,
    Evidence,
    ExtractResult,
    ExtractStats,
    Location,
    Node,
    NodeKind,
    ParsedDoc,
    TocEntry,
)

_HEADING_NUM = re.compile(r"^(\d+(?:\.\d+){0,4})\s+(.+)$")


class SectionExtractor:
    name = "section"
    kinds = {NodeKind.SECTION}
    requires: set[str] = set()
    version = "0.1"

    def extract(self, doc: ParsedDoc, ctx: ExtractContext) -> ExtractResult:
        t0 = time.time()
        toc = doc.toc or self._fallback_toc(doc)
        nodes, edges = self._build_section_nodes(doc, toc, ctx)
        return ExtractResult(
            nodes=nodes,
            edges=edges,
            stats=ExtractStats(
                nodes_emitted=len(nodes),
                edges_emitted=len(edges),
                duration_s=round(time.time() - t0, 3),
            ),
        )

    # ------- TOC 回退 -------

    def _fallback_toc(self, doc: ParsedDoc) -> list[TocEntry]:
        out: list[TocEntry] = []
        for page in doc.pages:
            for block in page.blocks:
                if block.kind != BlockKind.HEADING or not block.text:
                    continue
                m = _HEADING_NUM.match(block.text)
                if not m:
                    continue
                num, title = m.group(1), m.group(2)
                level = num.count(".") + 1
                out.append(
                    TocEntry(
                        level=level,
                        title=title.strip(),
                        page=page.page_no,
                        section_path=num,
                    )
                )
        return out

    # ------- 主逻辑 -------

    def _build_section_nodes(
        self, doc: ParsedDoc, toc: list[TocEntry], ctx: ExtractContext
    ) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        edges: list[Edge] = []

        # 维护一个层级栈：栈顶是潜在父节点
        stack: list[tuple[int, str]] = []  # (level, node_id)
        section_counter = 0

        for entry in toc:
            section_counter += 1
            path = entry.section_path or self._infer_path(entry, toc)
            qn = path or f"sec_{section_counter}"
            node_id = make_node_id(ctx.family, NodeKind.SECTION, qn, doc_id=doc.doc_id)
            node = Node(
                id=node_id,
                kind=NodeKind.SECTION,
                name=entry.title,
                qualified_name=qn,
                doc_id=doc.doc_id,
                location=Location(page=entry.page, section_path=path),
                attrs={"level": entry.level, "path": path},
                summary=entry.title,
                hash=content_hash(f"{path}|{entry.title}"),
            )
            nodes.append(node)

            # 弹出层级 ≥ 当前的栈帧
            while stack and stack[-1][0] >= entry.level:
                stack.pop()
            if stack:
                parent_id = stack[-1][1]
                edges.append(
                    Edge(
                        src=parent_id,
                        dst=node_id,
                        kind=EdgeKind.CONTAINS,
                        confidence=1.0,
                        evidence=Evidence(
                            pages=[entry.page] if entry.page else [],
                            extractor=f"{self.name}@{self.version}",
                        ),
                    )
                )
            stack.append((entry.level, node_id))

        return nodes, edges

    @staticmethod
    def _infer_path(entry: TocEntry, all_entries: list[TocEntry]) -> str:
        """没有显式 section_path 时按 level 推一个。"""
        # 简化：title 归一化即可
        return normalize_name(entry.title)[:64]
