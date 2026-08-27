"""L2 实体 schema 注册表（ADR-012）—— 高收益实体集合 + 文档类型路由。

新增一种实体类型 = 注册一个 schema，不写新 extractor。
覆盖芯片设计高收益环节：寄存器/位域、管脚、存储器映射、中断、时序、信号、
接口、时钟/电源域、需求、勘误。其余长尾实体（封装尺寸、电气曲线、性能指标）
不进 L2，交给 L1 检索 + L0 原文片段由 agent 现场理解。

设计原则：
- 所有 prompt 双语化，不写死特定协议词汇（AMBA/AXI/PCIe 专用词不进 prompt）。
- table_header_hints 中英双词表，匹配大小写不敏感。
- 每种 schema 声明适用的 DocType 子集（见 SCHEMA_DOC_TYPES），供路由使用。
- NodeKind 不再借壳：interrupt/clock/power_domain/memory_map/requirement/errata
  各有独立 NodeKind。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from docgraph.graph.schema import DocType, NodeKind, RegisterDef

# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------


@dataclass
class EntitySchema:
    """一种 L2 实体的定义。"""

    kind: NodeKind
    target_model: type[BaseModel]
    list_wrapper: type[BaseModel]
    description: str
    prompt_template: str
    table_header_hints: list[str] = field(default_factory=list)
    min_confidence: float = 0.85
    # 该 schema 默认启用的文档类型；None = 所有文档类型
    doc_types: tuple[DocType, ...] | None = None
    # 负向排除词：表头/正文命中则不抽（避免 SoC/地址映射等被误判为该实体）
    negative_hints: tuple[str, ...] = ()

    @property
    def items_field(self) -> str:
        for k in self.list_wrapper.model_fields:
            return k
        return "items"


# ---------------------------------------------------------------------------
# 列表包装（LLM 整页输出格式）
# ---------------------------------------------------------------------------


class RegisterDefList(BaseModel):
    registers: list[RegisterDef] = Field(default_factory=list)


class PinDef(BaseModel):
    name: str
    direction: str | None = None  # IN / OUT / IO / POWER / GND / ANALOG
    pin_no: str | None = None
    description: str = ""
    voltage: str | None = None  # 供电电压（电源 pin 用）


class PinDefList(BaseModel):
    pins: list[PinDef] = Field(default_factory=list)


class TimingParam(BaseModel):
    symbol: str
    min: str | None = None
    typ: str | None = None
    max: str | None = None
    unit: str | None = None
    condition: str = ""


class TimingParamList(BaseModel):
    params: list[TimingParam] = Field(default_factory=list)


class SignalDef(BaseModel):
    name: str
    direction: str | None = None  # IN / OUT / IO / BIDIR
    width: str | None = None  # 位宽（如 32、[31:0]）
    description: str = ""


class SignalDefList(BaseModel):
    signals: list[SignalDef] = Field(default_factory=list)


class InterfaceDef(BaseModel):
    name: str  # 接口名 e.g. AXI4, APB, PIPE
    protocol: str | None = None  # 协议 e.g. AMBA AXI, APB, PIPE
    direction: str | None = None  # master / slave / initiator / target
    width: str | None = None
    description: str = ""


class InterfaceDefList(BaseModel):
    interfaces: list[InterfaceDef] = Field(default_factory=list)


class RequirementDef(BaseModel):
    id: str | None = None
    text: str
    category: str | None = None


class RequirementDefList(BaseModel):
    requirements: list[RequirementDef] = Field(default_factory=list)


class ConstraintDef(BaseModel):
    name: str
    target: str | None = None
    constraint_type: str | None = None
    value: str | None = None
    unit: str | None = None
    condition: str = ""
    description: str = ""


class ConstraintDefList(BaseModel):
    constraints: list[ConstraintDef] = Field(default_factory=list)


class PhysicalConstraintDef(BaseModel):
    name: str
    object: str | None = None
    constraint_type: str | None = None
    value: str | None = None
    layer: str | None = None
    region: str | None = None
    description: str = ""


class PhysicalConstraintDefList(BaseModel):
    physical_constraints: list[PhysicalConstraintDef] = Field(default_factory=list)


class InterruptDef(BaseModel):
    name: str
    number: str | None = None
    type: str | None = None  # level / pulse / MSI / MSI-X
    description: str = ""


class InterruptDefList(BaseModel):
    interrupts: list[InterruptDef] = Field(default_factory=list)


class ClockResetDef(BaseModel):
    name: str
    type: str = "clock"  # clock / reset / power
    frequency: str | None = None  # 时钟频率
    domain: str | None = None  # 时钟域
    polarity: str | None = None  # 极性（active high/low）
    description: str = ""


class ClockResetDefList(BaseModel):
    items: list[ClockResetDef] = Field(default_factory=list)


class MemoryMapDef(BaseModel):
    name: str
    address: str
    size: str | None = None
    description: str = ""


class MemoryMapDefList(BaseModel):
    entries: list[MemoryMapDef] = Field(default_factory=list)


class ErrataDef(BaseModel):
    """勘误条目 —— errata 文档核心实体。"""

    id: str | None = None  # errata 编号 e.g. ERR012345
    title: str = ""
    affected: str | None = None  # 受影响版本/修订 e.g. r0p0-r2p3
    severity: str | None = None  # critical / major / minor
    description: str = ""
    workaround: str = ""  # 规避方法（agent 最需要的结构化字段）


class ErrataDefList(BaseModel):
    errata: list[ErrataDef] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 预设实体类型（高收益集合，支持后续 register_schema 扩展）
# ---------------------------------------------------------------------------
#
# 分档：
# - 第一档（核心强 schema）：register / pin / memory_map / interrupt / errata
#   结构最规整、查询价值最高，带完整 source_block_ids 回溯。
# - 第二档（扩展 schema）：signal / interface / timing / clock_reset / requirement
#   保留但去协议词、降 confidence，靠 _table_matches 自然过滤。
# - 后端 spec（实现约束）：constraint / physical_constraint
#   覆盖 STA/SDC、floorplan、placement、routing、power/physical implementation specs。
#
# prompt 统一约束：name/description 按文档原文语言输出，不要翻译。

PRESET_SCHEMAS: dict[str, EntitySchema] = {
    # === 第一档：核心强 schema ===
    "register": EntitySchema(
        kind=NodeKind.REGISTER,
        target_model=RegisterDef,
        list_wrapper=RegisterDefList,
        description="register and bitfield definitions / 寄存器及位域定义",
        prompt_template=(
            "You are a chip spec extractor. The input is a register definition table.\n"
            "Extract **all** registers and their bitfields into JSON.\n"
            "Rules: bit_high >= bit_low; access uses RO/RW/WO/W1C; leave empty string if unknown.\n"
            "Keep name/description in the document's original language; do not translate.\n\n"
            "{table_text}"
        ),
        table_header_hints=[
            "bit",
            "field",
            "bits",
            "name",
            "access",
            "reset",
            "description",
            "address",
            "offset",
            "register",
            "位",
            "字段",
            "访问",
            "复位",
            "描述",
            "地址",
            "寄存器",
            "偏移",
        ],
        doc_types=(
            DocType.DATASHEET,
            DocType.REFERENCE_MANUAL,
            DocType.TRM,
            DocType.APP_NOTE,
            DocType.ERRATA,
            DocType.PROTOCOL,
        ),
    ),
    "pin": EntitySchema(
        kind=NodeKind.PIN,
        target_model=PinDef,
        list_wrapper=PinDefList,
        description="physical pin definitions / 物理管脚定义",
        prompt_template=(
            "You are a chip spec extractor. The input is a pin table.\n"
            "Extract **all** physical chip pins into JSON.\n"
            "Key: entries with a clear pin name / pin number / direction are pins.\n"
            "direction uses IN/OUT/IO/POWER/GND/ANALOG.\n"
            "Only physical pins, not pure logical signal names.\n"
            "Keep name/description in the document's original language.\n\n"
            "{table_text}"
        ),
        table_header_hints=[
            "pin",
            "name",
            "no",
            "number",
            "direction",
            "function",
            "type",
            "voltage",
            "ball",
            "管脚",
            "引脚",
            "编号",
            "方向",
            "功能",
            "封装",
        ],
        min_confidence=0.9,
        doc_types=(DocType.DATASHEET, DocType.REFERENCE_MANUAL),
    ),
    "memory_map": EntitySchema(
        kind=NodeKind.MEMORY_MAP,
        target_model=MemoryMapDef,
        list_wrapper=MemoryMapDefList,
        description="address space / memory map / 地址空间与存储器映射",
        prompt_template=(
            "You are a chip spec extractor. The input is an address map table.\n"
            "Extract **all** entries into JSON.\n"
            "Keep address/size in original form (including 0x prefix).\n"
            "Keep name/description in the document's original language.\n\n"
            "{table_text}"
        ),
        table_header_hints=[
            "address",
            "offset",
            "size",
            "range",
            "base",
            "bar",
            "memory",
            "region",
            "地址",
            "偏移",
            "大小",
            "范围",
            "内存",
            "区域",
            "映射",
        ],
        min_confidence=0.75,
        doc_types=(DocType.DATASHEET, DocType.REFERENCE_MANUAL, DocType.TRM, DocType.PROTOCOL),
    ),
    "interrupt": EntitySchema(
        kind=NodeKind.INTERRUPT,
        target_model=InterruptDef,
        list_wrapper=InterruptDefList,
        description="interrupt definitions / 中断定义",
        prompt_template=(
            "You are a chip spec extractor. The input is an interrupt definition table.\n"
            "Extract **all** interrupt entries into JSON.\n"
            "type uses level/pulse/edge/MSI/MSI-X where applicable.\n"
            "Keep name/description in the document's original language.\n\n"
            "{table_text}"
        ),
        table_header_hints=[
            "interrupt",
            "irq",
            "msi",
            "vector",
            "number",
            "priority",
            "中断",
            "向量",
            "优先级",
            "中断号",
        ],
        min_confidence=0.8,
        doc_types=(DocType.DATASHEET, DocType.REFERENCE_MANUAL, DocType.TRM, DocType.PROTOCOL),
    ),
    "errata": EntitySchema(
        kind=NodeKind.ERRATA,
        target_model=ErrataDef,
        list_wrapper=ErrataDefList,
        description="errata entries with workaround / 勘误条目及规避方法",
        prompt_template=(
            "You are a chip spec extractor. The input is an errata section/table.\n"
            "Extract **all** errata entries into JSON.\n"
            "workaround must capture the concrete mitigation steps; if none, use empty string.\n"
            "severity uses critical/major/minor.\n"
            "Keep title/description/workaround in the document's original language.\n\n"
            "{table_text}"
        ),
        table_header_hints=[
            "errata",
            "workaround",
            "affected",
            "severity",
            "silicon",
            "勘误",
            "勘误表",
            "规避",
            "影响",
            "严重",
            "修订",
            "变通",
        ],
        min_confidence=0.75,
        doc_types=(DocType.ERRATA,),
    ),
    # === 第二档：扩展 schema ===
    "signal": EntitySchema(
        kind=NodeKind.SIGNAL,
        target_model=SignalDef,
        list_wrapper=SignalDefList,
        description="interface / internal signal definitions / 接口与内部信号定义",
        prompt_template=(
            "You are a chip spec extractor. The input is a signal table.\n"
            "Extract **all** signals into JSON.\n"
            "width keeps original form (e.g. 32, [31:0]). direction uses IN/OUT/IO/BIDIR.\n"
            "Keep name/description in the document's original language.\n\n"
            "{table_text}"
        ),
        table_header_hints=[
            "signal",
            "direction",
            "width",
            "port",
            "信号",
            "位宽",
            "端口",
            "方向",
        ],
        min_confidence=0.8,
        doc_types=(DocType.DATASHEET, DocType.REFERENCE_MANUAL, DocType.TRM, DocType.PROTOCOL),
    ),
    "interface": EntitySchema(
        kind=NodeKind.INTERFACE,
        target_model=InterfaceDef,
        list_wrapper=InterfaceDefList,
        description="bus / protocol interface definitions / 总线与协议接口定义",
        prompt_template=(
            "You are a chip spec extractor. The input is an interface definition table.\n"
            "Extract **all** bus/protocol interfaces into JSON.\n"
            "direction uses master/slave/initiator/target/host/device. width keeps original form.\n"
            "Keep name/description in the document's original language.\n\n"
            "{table_text}"
        ),
        table_header_hints=[
            "interface",
            "bus",
            "protocol",
            "master",
            "slave",
            "width",
            "port",
            "接口",
            "总线",
            "协议",
            "主",
            "从",
            "端口",
        ],
        min_confidence=0.8,
        doc_types=(DocType.DATASHEET, DocType.REFERENCE_MANUAL, DocType.TRM, DocType.PROTOCOL),
    ),
    "timing": EntitySchema(
        kind=NodeKind.PARAMETER,
        target_model=TimingParam,
        list_wrapper=TimingParamList,
        description="timing / electrical parameters / 时序与电气参数",
        prompt_template=(
            "You are a chip spec extractor. The input is a timing/electrical parameter table.\n"
            "Extract **all** parameters into JSON.\n"
            "unit must keep original units; condition keeps the test condition text.\n"
            "Keep condition in the document's original language.\n\n"
            "{table_text}"
        ),
        table_header_hints=[
            "min",
            "max",
            "typ",
            "unit",
            "symbol",
            "condition",
            "parameter",
            "参数",
            "时序",
            "最小",
            "最大",
            "典型",
            "单位",
            "条件",
        ],
        doc_types=(DocType.DATASHEET, DocType.REFERENCE_MANUAL, DocType.PROTOCOL),
    ),
    "clock_reset": EntitySchema(
        kind=NodeKind.CLOCK,
        target_model=ClockResetDef,
        list_wrapper=ClockResetDefList,
        description="clock / reset / power domain definitions / 时钟复位与电源域定义",
        prompt_template=(
            "You are a chip spec extractor. The input is a clock/reset/power domain table.\n"
            "Extract **all** entries into JSON.\n"
            "type uses clock/reset/power. frequency keeps original form.\n"
            "Keep name/description in the document's original language.\n\n"
            "{table_text}"
        ),
        table_header_hints=[
            "clock",
            "reset",
            "frequency",
            "period",
            "polarity",
            "时钟",
            "复位",
            "频率",
            "周期",
            "极性",
        ],
        # 排除 SoC 拓扑 / 地址映射 / 封装条目，它们不是时钟复位实体
        negative_hints=(
            "soc die",
            "chip ",
            "bar ",
            "iova",
            "address map",
            "address space",
            "memory + io",
            "host ddr",
            "gpu",
            "smmu",
            "iommu",
            "reserved",
            "拓扑",
            "地址映射",
            "地址空间",
            "封装",
        ),
        min_confidence=0.8,
        doc_types=(DocType.DATASHEET, DocType.REFERENCE_MANUAL, DocType.TRM),
    ),
    "requirement": EntitySchema(
        kind=NodeKind.REQUIREMENT,
        target_model=RequirementDef,
        list_wrapper=RequirementDefList,
        description="design requirements / feature list / 设计需求与功能列表",
        prompt_template=(
            "You are a chip spec extractor. The input is a requirement/feature list.\n"
            "Extract **all** requirement entries into JSON.\n"
            "Only entries with an explicit requirement id or conditional description.\n"
            "Keep text in the document's original language.\n\n"
            "{table_text}"
        ),
        table_header_hints=[
            "requirement",
            "category",
            "feature",
            "id",
            "需求",
            "功能",
            "条件",
            "要求",
        ],
        min_confidence=0.7,
        doc_types=(DocType.REFERENCE_MANUAL, DocType.USER_GUIDE, DocType.APP_NOTE),
    ),
    "constraint": EntitySchema(
        kind=NodeKind.REQUIREMENT,
        target_model=ConstraintDef,
        list_wrapper=ConstraintDefList,
        description="implementation timing/design constraints / 后端实现时序与设计约束",
        prompt_template=(
            "You are a chip implementation spec extractor. The input is a constraint table.\n"
            "Extract **all** backend implementation constraints into JSON.\n"
            "Include SDC/STA constraints such as clock uncertainty, max transition, max fanout, "
            "false path, multicycle path, input/output delay, setup/hold margin, PVT/corner constraints.\n"
            "target is the constrained clock/net/path/module/object. value/unit keep original form.\n"
            "Keep description/condition in the document's original language.\n\n"
            "{table_text}"
        ),
        table_header_hints=[
            "constraint",
            "target",
            "object",
            "value",
            "unit",
            "condition",
            "sdc",
            "sta",
            "setup",
            "hold",
            "uncertainty",
            "transition",
            "fanout",
            "false path",
            "multicycle",
            "input delay",
            "output delay",
            "corner",
            "pvt",
            "约束",
            "目标",
            "对象",
            "取值",
            "单位",
            "条件",
            "时序",
            "建立",
            "保持",
            "不确定度",
            "转换",
            "扇出",
            "假路径",
            "多周期",
        ],
        negative_hints=(
            "register",
            "bit",
            "reset",
            "address",
            "pin no",
            "管脚编号",
            "寄存器",
            "floorplan",
            "placement",
            "routing",
            "layer",
            "region",
            "macro",
            "keepout",
            "blockage",
            "power grid",
            "布局",
            "摆放",
            "布线",
            "层",
            "区域",
            "宏",
            "禁布",
            "阻塞",
            "电源网格",
        ),
        min_confidence=0.8,
        doc_types=None,
    ),
    "physical_constraint": EntitySchema(
        kind=NodeKind.REQUIREMENT,
        target_model=PhysicalConstraintDef,
        list_wrapper=PhysicalConstraintDefList,
        description="floorplan / placement / routing / physical implementation constraints / 后端物理实现约束",
        prompt_template=(
            "You are a chip backend implementation spec extractor. The input is a physical constraint table.\n"
            "Extract **all** floorplan/placement/routing/power-grid/keepout/layer/region constraints into JSON.\n"
            "object is the constrained macro/net/region/layer. value keeps original form.\n"
            "Keep description in the document's original language.\n\n"
            "{table_text}"
        ),
        table_header_hints=[
            "floorplan",
            "placement",
            "route",
            "routing",
            "layer",
            "region",
            "macro",
            "keepout",
            "blockage",
            "halo",
            "channel",
            "spacing",
            "width",
            "density",
            "utilization",
            "power grid",
            "voltage area",
            "物理",
            "布局",
            "布图",
            "摆放",
            "布线",
            "层",
            "区域",
            "宏",
            "禁布",
            "阻塞",
            "间距",
            "线宽",
            "密度",
            "利用率",
            "电源网格",
            "电压区域",
        ],
        negative_hints=("register", "bit", "reset", "irq", "interrupt", "寄存器", "中断"),
        min_confidence=0.8,
        doc_types=None,
    ),
}


# ---------------------------------------------------------------------------
# 文档类型 → 默认 schema 子集路由（docs/architecture/knowledge-graph.md）
# ---------------------------------------------------------------------------

# UNKNOWN 文档保守启用核心强 schema 子集，避免 9 schema 全扫每份文档。
_DEFAULT_SCHEMA_BY_DOCTYPE: dict[DocType, tuple[str, ...]] = {
    DocType.DATASHEET: (
        "register",
        "pin",
        "memory_map",
        "interrupt",
        "signal",
        "interface",
        "timing",
    ),
    DocType.REFERENCE_MANUAL: (
        "register",
        "memory_map",
        "interrupt",
        "signal",
        "interface",
        "clock_reset",
        "requirement",
    ),
    DocType.TRM: ("register", "memory_map", "interrupt", "signal", "interface", "clock_reset"),
    DocType.ERRATA: ("errata", "register"),
    DocType.APP_NOTE: ("register", "signal", "timing", "requirement"),
    DocType.USER_GUIDE: ("register", "requirement", "constraint", "physical_constraint"),
    DocType.PROTOCOL: (
        "register",
        "pin",
        "signal",
        "interface",
        "timing",
        "memory_map",
        "interrupt",
        "constraint",
    ),
    DocType.UNKNOWN: (
        "register",
        "pin",
        "memory_map",
        "interrupt",
        "constraint",
        "physical_constraint",
    ),
}

# 全集（向后兼容：显式指定 schema_names 时仍可全扫）
ALL_SCHEMA_NAMES: tuple[str, ...] = tuple(PRESET_SCHEMAS.keys())


def schemas_for_doctype(doc_type: DocType | str | None) -> list[str]:
    """按文档类型返回默认启用的 schema 名列表。

    返回的每个名字一定存在于 PRESET_SCHEMAS（双重过滤：路由表 ∩ 注册表）。
    """
    if isinstance(doc_type, str):
        try:
            doc_type = DocType(doc_type)
        except ValueError:
            doc_type = DocType.UNKNOWN
    if doc_type is None:
        doc_type = DocType.UNKNOWN
    names = _DEFAULT_SCHEMA_BY_DOCTYPE.get(doc_type, _DEFAULT_SCHEMA_BY_DOCTYPE[DocType.UNKNOWN])
    return [n for n in names if n in PRESET_SCHEMAS]


def get_schema(name: str) -> EntitySchema | None:
    return PRESET_SCHEMAS.get(name)


def list_schemas() -> list[str]:
    return list(PRESET_SCHEMAS)


def register_schema(name: str, schema: EntitySchema) -> None:
    PRESET_SCHEMAS[name] = schema
