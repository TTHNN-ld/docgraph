"""TextEntityExtractor —— 正文实体抽取（requirement / errata）。

芯片 spec 里很多关键实体不在表格里，而在正文段落：
- requirement：`REQ_<FAMILY>_<NUM>: <描述>` 这种带 ID 的需求条目
- errata：`Errata <NUM>: ...` / `ERR<num> ...` 勘误条目

这两类实体结构清晰、有强标识符（编号），适合纯规则确定性抽取，不依赖 LLM。
抽出的节点带 source_block_ids 回溯到 L0 原文段落。

设计原则（docs/architecture/data-layers.md）：
- L2 是可选增强，失败不影响 L0/L1
- 节点必须带 evidence + source_block_ids
- 不写死特定项目，正则覆盖通用编号格式
"""

from __future__ import annotations

import re
import time

from docgraph.core.ids import content_hash, make_node_id
from docgraph.core.logger import get_logger
from docgraph.extractors.base import ExtractContext
from docgraph.graph.schema import (
    BlockKind,
    ExtractResult,
    ExtractStats,
    Location,
    Node,
    NodeKind,
    ParsedDoc,
)

log = get_logger(__name__)


# requirement 编号格式：REQ_PCIE_TRS_004 / REQ_USB_001 / REQ_001
# 前缀可含多段（PCIE_TRS / VC / PF），编号在最后
# 文本捕获到下一个 REQ 编号或行尾为止
_REQ_ID = r"REQ_[A-Z0-9]+(?:_[A-Z0-9]+)*_\d+|REQ_\d+"
_REQ_PATTERN = re.compile(
    rf"(?P<id>{_REQ_ID})\s*[:：]\s*"
    rf"(?P<text>.+?)(?={_REQ_ID}\s*[:：]|$)"
)

# errata 编号格式：ERR012345 / Errata: ERR001
# 文本捕获到下一个 ERR 编号或行尾
_ERR_PATTERN = re.compile(
    r"(?:errata[:：\s]*)?(?P<id>ERR\d{3,8})\s*[:：]?\s*"
    r"(?P<text>.+?)(?=errata\s+ERR\d{3,8}|ERR\d{3,8}|$)",
    re.IGNORECASE,
)

# 需求/勘误标题章节（命中则整段重点扫）
_REQ_SECTION_TITLE = re.compile(
    r"(?:list\s+of\s+requirements?|requirements?|需求(?:列表|清单)?|功能需求)",
    re.IGNORECASE,
)
_ERR_SECTION_TITLE = re.compile(r"(?:errata|勘误(?:表|清单)?)", re.IGNORECASE)


class TextEntityExtractor:
    """正文实体抽取器 —— requirement / errata。

    只扫描 L0 paragraph / heading block 的 text。纯规则，不调 LLM。
    多个 REQ 挤在同一个 block 里也能逐个抽出（finditer）。
    """

    name = "text_entity"
    kinds = {NodeKind.REQUIREMENT, NodeKind.ERRATA}
    requires = {"section"}
    version = "0.1"

    def extract(self, doc: ParsedDoc, ctx: ExtractContext) -> ExtractResult:
        t0 = time.time()
        nodes: list[Node] = []
        seen_req: set[str] = set()
        seen_err: set[str] = set()

        for page in doc.pages:
            blocks = sorted(page.blocks, key=lambda b: b.reading_order)
            for block in blocks:
                if block.kind not in (BlockKind.PARAGRAPH, BlockKind.HEADING, BlockKind.LIST):
                    continue
                if not block.text:
                    continue
                # requirement
                for m in _REQ_PATTERN.finditer(block.text):
                    rid = m.group("id")
                    if rid in seen_req:
                        continue
                    text = m.group("text").strip().rstrip(".。;；").strip()
                    if len(text) < 2:
                        continue
                    seen_req.add(rid)
                    nodes.append(self._make_req_node(rid, text, block, doc, ctx))
                # errata
                for m in _ERR_PATTERN.finditer(block.text):
                    eid = m.group("id").upper()
                    if eid in seen_err:
                        continue
                    text = m.group("text").strip().rstrip(".。;；").strip()
                    if len(text) < 2:
                        continue
                    seen_err.add(eid)
                    nodes.append(self._make_errata_node(eid, text, block, doc, ctx))

        return ExtractResult(
            nodes=nodes,
            stats=ExtractStats(
                nodes_emitted=len(nodes),
                duration_s=round(time.time() - t0, 3),
            ),
        )

    def _make_req_node(
        self, rid: str, text: str, block, doc: ParsedDoc, ctx: ExtractContext
    ) -> Node:
        return Node(
            id=make_node_id(ctx.family, NodeKind.REQUIREMENT, rid, doc_id=doc.doc_id),
            kind=NodeKind.REQUIREMENT,
            name=rid,
            qualified_name=rid,
            doc_id=doc.doc_id,
            location=Location(page=block.page, section_path=block.section_path),
            summary=text[:240],
            attrs={
                "id": rid,
                "text": text,
                "source": "text_entity:requirement",
                "source_block_ids": [block.id],
                "source_chunk_ids": [],
                "extraction_confidence": "deterministic",
            },
            hash=content_hash(f"req|{rid}|{text}"),
        )

    def _make_errata_node(
        self, eid: str, text: str, block, doc: ParsedDoc, ctx: ExtractContext
    ) -> Node:
        return Node(
            id=make_node_id(ctx.family, NodeKind.ERRATA, eid, doc_id=doc.doc_id),
            kind=NodeKind.ERRATA,
            name=eid,
            qualified_name=eid,
            doc_id=doc.doc_id,
            location=Location(page=block.page, section_path=block.section_path),
            summary=text[:240],
            attrs={
                "id": eid,
                "description": text,
                "source": "text_entity:errata",
                "source_block_ids": [block.id],
                "source_chunk_ids": [],
                "extraction_confidence": "deterministic",
            },
            hash=content_hash(f"err|{eid}|{text}"),
        )
