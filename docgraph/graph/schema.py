"""核心 schema 定义 —— 所有跨模块数据的真相之源。

设计原则：
- 每个 Node / Edge 都带 schema_version，方便迁移。
- 每个 Node / Edge 都必须带 evidence，让 Agent 可追溯。
- attrs 是开放的 dict，由对应 extractor 约束其子 schema。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Schema 版本号（变更时务必加 migration）
# ---------------------------------------------------------------------------

NODE_SCHEMA_VERSION = 1
EDGE_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class NodeKind(str, Enum):
    DOCUMENT = "document"
    SECTION = "section"
    REGISTER = "register"
    BITFIELD = "bitfield"
    PIN = "pin"
    SIGNAL = "signal"
    MODULE = "module"
    INTERFACE = "interface"
    PARAMETER = "parameter"
    INTERRUPT = "interrupt"
    CLOCK = "clock"
    POWER_DOMAIN = "power_domain"
    MEMORY_MAP = "memory_map"
    REQUIREMENT = "requirement"
    ERRATA = "errata"
    FIGURE = "figure"
    TABLE = "table"
    FORMULA = "formula"
    CODEBLOCK = "codeblock"
    TERM = "term"
    CHUNK = "chunk"


class EdgeKind(str, Enum):
    CONTAINS = "contains"
    DEFINES = "defines"
    HAS_BITFIELD = "has_bitfield"
    BELONGS_TO = "belongs_to"
    CONNECTS_TO = "connects_to"
    CONTROLS = "controls"
    DEPENDS_ON = "depends_on"
    CONSTRAINS = "constrains"
    REFERENCES = "references"
    ILLUSTRATED_BY = "illustrated_by"
    ALIAS_OF = "alias_of"
    SUPERSEDES = "supersedes"
    DERIVED_FROM = "derived_from"
    # ADR-015 语义关系层（IP-XACT 对齐）
    CONTAINED_IN = "contained_in"  # memory_map → register
    MAPPED_TO = "mapped_to"  # interrupt → register
    DRIVES = "drives"  # signal → pin | interface
    CLOCKS = "clocks"  # clock → module | signal
    RESETS = "resets"  # reset_domain → module | register
    IMPLEMENTS = "implements"  # module → interface


class DocType(str, Enum):
    DATASHEET = "datasheet"
    REFERENCE_MANUAL = "reference_manual"
    TRM = "trm"
    ERRATA = "errata"
    APP_NOTE = "app_note"
    USER_GUIDE = "user_guide"
    PROTOCOL = "protocol"
    UNKNOWN = "unknown"


class L2Status(str, Enum):
    """Trust state for an L2 graph item.

    Extraction remains recall-oriented: uncertain results stay queryable as
    candidates instead of being discarded.  Only validated, traceable items
    may be promoted to facts.
    """

    DOCUMENT_ENTITY = "document_entity"
    CANDIDATE = "candidate"
    FACT = "fact"
    NEEDS_REVIEW = "needs_review"
    CONFLICT = "conflict"
    REJECTED = "rejected"


class DerivationMethod(str, Enum):
    DETERMINISTIC = "deterministic"
    LLM_INFERRED = "llm_inferred"
    VLM_INFERRED = "vlm_inferred"
    MERGED = "merged"
    MANUAL = "manual"


class DerivationConfidence(str, Enum):
    EXACT = "exact"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


# ---------------------------------------------------------------------------
# 基础类型
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class BBox(BaseModel):
    """Page-relative bounding box: [x0, y0, x1, y1]，单位为 pt。"""

    model_config = ConfigDict(frozen=True)

    x0: float
    y0: float
    x1: float
    y1: float
    page: int | None = None


class Location(BaseModel):
    """节点在文档中的位置。"""

    page: int | None = None
    bbox: BBox | None = None
    section_path: str | None = None  # e.g. "3.2.1"


class Evidence(BaseModel):
    """节点 / 边的来源证据。**永不为空**。"""

    chunk_ids: list[str] = Field(default_factory=list)
    pages: list[int] = Field(default_factory=list)
    bboxes: list[BBox] = Field(default_factory=list)
    extractor: str  # "register@0.1" 之类
    raw_snippet: str | None = None


class Derivation(BaseModel):
    """How an L2 graph item was produced and whether it was verified."""

    method: DerivationMethod
    extractor: str
    confidence: DerivationConfidence
    verified: bool = False


class ValidationIssue(BaseModel):
    """Machine-readable reason why a candidate was not promoted to a fact."""

    code: str
    message: str
    severity: ValidationSeverity = ValidationSeverity.ERROR
    field: str | None = None


# ---------------------------------------------------------------------------
# 文档级元信息
# ---------------------------------------------------------------------------


class DocMetadata(BaseModel):
    """从文档头部 / 用户配置中拿到的元数据。"""

    title: str | None = None
    family: str | None = None  # e.g. "stm32f407"（项目级命名空间，进 node_id）
    # 芯片型号/IP 实例（比 family 更细，用于消歧判断"同一实例"）。
    # 缺省时由文档名推断；推断不出则为 None，消歧回退到 family（兼容旧项目）。
    chip_model: str | None = None
    type: DocType = DocType.UNKNOWN
    version: str | None = None
    date: str | None = None
    priority: int = 10
    supersedes: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Parser 输出的统一 IR
# ---------------------------------------------------------------------------


# === L0 高保真版面层（docs/architecture/data-layers.md）===


class BlockKind(str, Enum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    TABLE = "table"
    FIGURE = "figure"
    FORMULA = "formula"
    CODE = "code"
    LIST = "list"
    CAPTION = "caption"


class TableData(BaseModel):
    """表格数据 —— 单元格级别，禁止再揉成纯文本（L0 契约）。"""

    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    n_rows: int = 0
    n_cols: int = 0
    merged_cells: list[dict] = Field(default_factory=list)
    caption: str | None = None
    html: str | None = None


class Block(BaseModel):
    """L0 版面块 —— 原文的结构化镜像原子。一等公民，落库可回溯。

    稳定 ID 形如：<doc_id>#p<page>#b<idx>
    """

    id: str
    doc_id: str
    page: int
    kind: BlockKind
    reading_order: int = 0
    bbox: BBox | None = None
    text: str | None = None  # 文本类
    table: TableData | None = None  # 表格类
    image_path: str | None = None  # 图 / 公式渲染
    latex: str | None = None  # 公式
    section_path: str | None = None  # 归属章节
    heading_level: int | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)


# === Parser 内部派生视图 ===
#
# L0 的权威表示是 ParsedPage.blocks。下面几个轻量模型仅用于 parser
# 适配器内部组织文本、表格和图片，再归一为 Block 入库；Extractor 不应
# 把它们作为事实入口。


class TextBlock(BaseModel):
    text: str
    bbox: BBox | None = None
    reading_order: int = 0
    is_heading: bool = False
    heading_level: int | None = None  # 1..N


class ParsedTable(BaseModel):
    """Parser adapter 的表格中间视图，最终必须归一为 TableData/Block。"""

    html: str | None = None
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    bbox: BBox | None = None
    caption: str | None = None


class ParsedFigure(BaseModel):
    image_path: str | None = None  # 相对 cache 根的路径
    bbox: BBox | None = None
    caption: str | None = None


class ParsedFormula(BaseModel):
    latex: str | None = None
    bbox: BBox | None = None


class ParsedPage(BaseModel):
    page_no: int
    blocks: list[Block] = Field(default_factory=list)  # L0 一等公民
    # 派生视图：方便 parser adapter 组织数据；L0 权威入口始终是 blocks。
    text_blocks: list[TextBlock] = Field(default_factory=list)
    tables: list[ParsedTable] = Field(default_factory=list)
    figures: list[ParsedFigure] = Field(default_factory=list)
    formulas: list[ParsedFormula] = Field(default_factory=list)
    quality: PageQuality | None = None
    rendered_image_path: str | None = None  # 整页渲染 PNG（VLM 兜底用）

    @property
    def text(self) -> str:
        """所有 text_blocks 按 reading_order 拼接。"""
        ordered = sorted(self.text_blocks, key=lambda b: b.reading_order)
        return "\n".join(b.text for b in ordered)


class PageQuality(BaseModel):
    """页级质量评估 —— 智能路由 + VLM 兜底的依据。

    各路 extractor 看 `needs_vlm` + `vlm_reasons` 决定是否走 VLM 路径。
    """

    text_chars: int = 0
    text_blocks: int = 0
    text_density: float = 0.0  # 字符数 / 页面面积（粗略）
    image_area_ratio: float = 0.0  # 图片像素面积占比 [0,1]
    has_text_layer: bool = True  # False = 扫描版无文本层
    table_keyword_hits: int = 0  # "Table N: …" 命中
    register_keyword_hits: int = 0  # register / bit field / 寄存器 / 位域 …
    figure_caption_hits: int = 0  # "Figure N: …"
    pin_keyword_hits: int = 0  # pin / 管脚 / direction / function
    timing_keyword_hits: int = 0  # min/typ/max + 单位
    needs_vlm: bool = False  # 综合判定
    vlm_reasons: list[str] = Field(default_factory=list)


class TocEntry(BaseModel):
    level: int
    title: str
    page: int | None = None
    section_path: str | None = None


class ParsedDoc(BaseModel):
    doc_id: str
    source_path: str
    pages: list[ParsedPage]
    metadata: DocMetadata = Field(default_factory=DocMetadata)
    toc: list[TocEntry] = Field(default_factory=list)
    parser: str = "unknown"
    parser_version: str = "0"


# ---------------------------------------------------------------------------
# 图谱核心类型
# ---------------------------------------------------------------------------


class Node(BaseModel):
    """图谱节点。"""

    schema_version: int = NODE_SCHEMA_VERSION
    id: str
    kind: NodeKind
    name: str
    qualified_name: str | None = None
    aliases: list[str] = Field(default_factory=list)
    doc_id: str
    location: Location = Field(default_factory=Location)
    evidence: Evidence = Field(default_factory=lambda: Evidence(extractor="unknown"))
    attrs: dict[str, Any] = Field(default_factory=dict)
    summary: str | None = None
    embedding_id: int | None = None
    hash: str | None = None
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _ensure_l2_metadata(self) -> Node:
        _ensure_graph_item_metadata(self.attrs, self.kind, self.evidence.extractor)
        return self


class Edge(BaseModel):
    """图谱边。"""

    schema_version: int = EDGE_SCHEMA_VERSION
    src: str
    dst: str
    kind: EdgeKind
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: Evidence
    attrs: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _ensure_l2_metadata(self) -> Edge:
        _ensure_graph_item_metadata(self.attrs, None, self.evidence.extractor)
        return self


class Chunk(BaseModel):
    """检索用文本片段（L1）。

    block_ids 指回 L0 版面块，满足 L1 到 L0 的可回溯契约。
    """

    id: str
    doc_id: str
    page: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    section_id: str | None = None
    section_node_id: str | None = None
    text: str
    hash: str | None = None
    source_hash: str | None = None
    block_ids: list[str] = Field(default_factory=list)
    kind: str = "section"  # section | table | figure | paragraph-group
    chunk_type: str | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Extractor 协议输入输出
# ---------------------------------------------------------------------------


class ExtractStats(BaseModel):
    nodes_emitted: int = 0
    edges_emitted: int = 0
    duration_s: float = 0.0
    llm_calls: int = 0
    cost_usd: float = 0.0
    failed: int = 0


class ExtractResult(BaseModel):
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    stats: ExtractStats = Field(default_factory=ExtractStats)


_DOCUMENT_ENTITY_KINDS = {
    NodeKind.DOCUMENT,
    NodeKind.SECTION,
    NodeKind.FIGURE,
    NodeKind.TABLE,
    NodeKind.FORMULA,
    NodeKind.CODEBLOCK,
    NodeKind.CHUNK,
}


def _ensure_graph_item_metadata(
    attrs: dict[str, Any],
    kind: NodeKind | None,
    extractor: str,
) -> None:
    """Populate conservative metadata for legacy and third-party producers.

    This is intentionally non-promoting: unknown outputs become candidates (or
    document entities), never facts.  Extractors must explicitly validate and
    promote deterministic results.
    """

    if "l2_status" not in attrs:
        attrs["l2_status"] = (
            L2Status.DOCUMENT_ENTITY.value
            if kind in _DOCUMENT_ENTITY_KINDS
            else L2Status.CANDIDATE.value
        )
    source = str(attrs.get("source") or extractor or "unknown")
    if "derivation" not in attrs:
        lowered = source.lower()
        if "vlm" in lowered:
            method = DerivationMethod.VLM_INFERRED
            confidence = DerivationConfidence.LOW
        elif "llm" in lowered:
            method = DerivationMethod.LLM_INFERRED
            confidence = DerivationConfidence.LOW
        elif attrs.get("extraction_confidence") == "deterministic":
            method = DerivationMethod.DETERMINISTIC
            confidence = DerivationConfidence.HIGH
        else:
            method = DerivationMethod.MERGED
            confidence = DerivationConfidence.MEDIUM
        attrs["derivation"] = Derivation(
            method=method,
            extractor=source,
            confidence=confidence,
            verified=False,
        ).model_dump(mode="json")
    attrs.setdefault("validation_issues", [])


# ---------------------------------------------------------------------------
# 寄存器子 schema —— table_entity:register 产出节点的 attrs 严格遵循
# ---------------------------------------------------------------------------


class BitFieldDef(BaseModel):
    name: str
    bit_high: int
    bit_low: int
    access: str | None = None  # "RW", "RO", "WO", "W1C", ...
    reset: str | None = None
    description: str = ""

    @property
    def bit_range(self) -> tuple[int, int]:
        return (self.bit_high, self.bit_low)


class RegisterDef(BaseModel):
    """table_entity:register 的严格输出 schema。LLM 必须吐这个。"""

    name: str
    address: str | None = None
    offset: str | None = None
    width: int = 32
    access: str | None = None
    reset_value: str | None = None
    description: str = ""
    bitfields: list[BitFieldDef] = Field(default_factory=list)


__all__ = [
    "EDGE_SCHEMA_VERSION",
    "NODE_SCHEMA_VERSION",
    "BBox",
    "BitFieldDef",
    "Block",
    "BlockKind",
    "Chunk",
    "Derivation",
    "DerivationConfidence",
    "DerivationMethod",
    "DocMetadata",
    "DocType",
    "Edge",
    "EdgeKind",
    "Evidence",
    "ExtractResult",
    "ExtractStats",
    "L2Status",
    "Location",
    "Node",
    "NodeKind",
    "PageQuality",
    "ParsedDoc",
    "ParsedFigure",
    "ParsedFormula",
    "ParsedPage",
    "ParsedTable",
    "RegisterDef",
    "TableData",
    "TextBlock",
    "TocEntry",
    "ValidationIssue",
    "ValidationSeverity",
]
