# Extractor 层

> 对应 DESIGN.md §8。从 `ParsedDoc` / L0 Blocks / L1 Chunks 中抽出强结构化的 `Node` 和 `Edge`。

Extractor 不负责 PDF 解析，也不感知具体 parser。PyMuPDF、MinerU、Docling、Marker、XLSX 等后端必须先归一到 `ParsedDoc`，Extractor 只消费统一 IR。

## 1. 接口

```python
class Extractor(Protocol):
    name: str
    kinds: set[NodeKind]
    requires: set[str] = set()

    def extract(self, doc: ParsedDoc, ctx: ExtractContext) -> ExtractResult: ...

class ExtractResult(BaseModel):
    nodes: list[Node]
    edges: list[Edge]
    chunks: list[Chunk] = []
    stats: ExtractStats
```

Extractor 之间通过 `requires` 声明依赖，由 orchestrator 拓扑排序执行。

## 2. 当前内置 Extractor

当前实现注册的内置 extractor：

| 名称 | 产出 | 职责 |
|---|---|---|
| `section` | `SECTION` | 从 TOC / heading block 构建章节节点 |
| `table_entity` | 动态：register / pin / signal / interface / requirement / ... | 通用 schema-guided 实体抽取 |
| `figure` | `FIGURE` + 可选芯片语义节点/边 | 从 figure block / image block 生成图节点，并按需用 VLM 做图语义增强 |
| `glossary` | `TERM` | 抽取术语、缩写、别名 |

旧的 `RegisterExtractor` / `PinExtractor` / `TimingExtractor` 不再作为独立内置类注册。寄存器、管脚、时序、接口、需求等实体通过 `TableEntityExtractor + schema registry` 统一抽取。

## 3. TableEntityExtractor

`TableEntityExtractor` 是 L2 表格/文本实体增强的统一入口。它不绑定具体 parser，而是消费由 L1 chunk + L0 block 生成的 `EntityCandidate`。

```text
L1 chunk + L0 blocks
  └─ EntityCandidate
       ├─ kind=table       → cells/html → deterministic normalizer → schema LLM 兜底
       ├─ kind=table_image → 表格裁剪图 → VLM/OCR/table-recognizer → schema 抽取
       ├─ kind=text        → 章节/页面文本候选 → LLM schema 抽取
       └─ kind=page_image  → 整页渲染图 → VLM schema 抽取
```

每个候选必须携带 `chunk_ids` / `block_ids` / page / section / text/table/image。普通表格、文本和图候选通常对应一个 L1 chunk；整页渲染图候选对应同页覆盖到的 L1 chunk 集合。L2 物化出的节点必须写回 `source_chunk_ids`、`source_block_ids` 和节点级 `evidence`。

`docgraph l2-audit` 可在不调用 LLM/VLM 的情况下审计候选覆盖：统计 table/text/figure candidate 数量、schema 命中数量、已物化 L2 节点数量和未命中的文档样例。它用于定位漏抽来自候选层、schema 路由还是后续模型抽取。

### 3.1 确定性 Normalizer

L2 不能把所有结构化事实都交给模型猜测。对于列语义明确的高收益表格，`TableEntityExtractor` 会先运行确定性 normalizer；只有 normalizer 判断为不明确时才进入 LLM/VLM 兜底。

当前确定性路径覆盖 register/bitfield 表，识别以下通用列语义：

- register：`Reg name` / `Register` / `寄存器`
- bitfield：`Field` / `Bit field` / `字段`
- 位段：`Msb` + `Lsb`，或 `Bits 4:3` / `Bit` 列
- 属性：`SWaccess` / `Access` / `Default` / `Reset` / `Description`

Normalizer 只依赖表头和单元格结构，不绑定文档名或协议名。它会处理常见 OCR/表格解析错位：寄存器名被移入相邻列、续行为空或纯数字、位段列缺失但同表存在重复 `bit_N` 模式。无法可靠恢复时不强行产出，保留给 LLM/VLM 兜底和 L0/L1 原文回溯。

### 3.2 Schema Registry

新增实体类型优先通过 schema registry，而不是新增专用 extractor。

已有预设 schema（高收益集合，分两档）：

| schema | NodeKind | 典型输入 | 档位 |
|---|---|---|---|
| `register` | `REGISTER` + `BITFIELD` | register/bitfield 表 | 核心 |
| `pin` | `PIN` | pin/package/interface 表 | 核心 |
| `memory_map` | `MEMORY_MAP` | 地址映射表 | 核心 |
| `interrupt` | `INTERRUPT` | IRQ/MSI/MSI-X 表 | 核心 |
| `errata` | `ERRATA` | errata 条目 + workaround | 核心 |
| `signal` | `SIGNAL` | 接口信号表 | 扩展 |
| `interface` | `INTERFACE` | 总线/协议接口表 | 扩展 |
| `timing` | `PARAMETER` | min/typ/max 时序或电气参数表 | 扩展 |
| `clock_reset` | `CLOCK` | 时钟、复位、电源域表 | 扩展 |
| `requirement` | `REQUIREMENT` | requirement / feature 列表 | 扩展 |

每个 schema 声明：
- `table_header_hints`：双语（中英）匹配词，决定哪些表/段落走该 schema。
- `negative_hints`：排除词，命中则不抽（如 `clock_reset` 排除 SoC/地址映射/封装条目，避免污染）。
- `doc_types`：该 schema 默认启用的文档类型（见下"文档类型路由"）。
- `min_confidence`：物化时写的置信度。

新增 schema 的最小内容：

```python
EntitySchema(
    kind=NodeKind.SIGNAL,
    target_model=SignalDef,
    list_wrapper=SignalDefList,
    description="接口/内部信号定义",
    prompt_template="...",
    table_header_hints=["signal", "direction", "width"],
    doc_types=(DocType.DATASHEET, DocType.PROTOCOL),
)
```

### 3.3 文档类型路由

`TableEntityExtractor` 按 `parsed.metadata.type` 选择默认启用的 schema 子集，
避免 9 个 schema 对每份文档全扫：

| DocType | 默认 schema |
|---|---|
| datasheet | register, pin, memory_map, interrupt, signal, interface, timing |
| reference_manual / trm | register, memory_map, interrupt, signal, interface, clock_reset, requirement |
| errata | errata, register |
| app_note | register, signal, timing, requirement |
| protocol | signal, interface, timing, memory_map, interrupt |
| unknown | register, pin, memory_map, interrupt（核心子集） |

显式指定 `schema_names` 或环境变量 `DOCGRAPH_TABLE_ENTITY_SCHEMAS` 时覆盖路由，全扫。

### 3.4 Parser 输出差异的处理

Parser 后端能力不同，Extractor 不能假设表格一定有单元格。

| Parser 输出 | L0 表达 | Extractor 策略 |
|---|---|---|
| PyMuPDF `find_tables()` 单元格 | `table_source=cells` | 先走 deterministic normalizer；不明确再 markdown 化按 schema 抽取 |
| MinerU 表格裁剪图 | `table_source=image` + `image_path` | 单表图片 VLM/OCR/table-recognizer |
| Docling HTML/结构表 | `table_source=html/cells` | 先解析 HTML/cells 并运行 normalizer；不明确再抽取 |
| OCR 文本块 | `table_source=text` 或 paragraph blocks | 文本候选窗口抽取 |

这样下游抽取逻辑复用同一套 schema，不需要按 parser 分叉。

## 4. 失败兜底

L2 是可选增强，失败不得影响 L0/L1。

```text
结构化表格抽取失败
  ├─ 缩短输入 / 重试
  ├─ 使用表格图片 VLM/OCR
  ├─ 使用页面文本候选
  └─ 记录失败，build 继续
```

要求：

- 所有 LLM/VLM 调用走统一 client
- 调用必须缓存
- 输出必须 Pydantic schema 校验
- 节点必须带真实 evidence / source 信息，且 `source_block_ids` / `source_chunk_ids` 可回溯
- 失败只能降低 L2 召回，不能丢失 L0/L1 原文

## 5. FigureExtractor

`FigureExtractor` 直接从 L0 `BlockKind.FIGURE` 生成图节点，保留：

- `image_path`
- caption
- page / bbox
- surrounding context（如可得）

`attrs.semantic_role=decoration` 的 L0 图块不会进入 L2。Parser 可用它保留封面背景、装饰网格等非信息图，既不丢 L0 证据，也不污染图谱。

图的语义增强可以使用 VLM，但增强结果不是唯一入口。L0 的图片路径、图注、坐标始终保留；VLM 失败只降低 L2 召回，不影响 L0/L1。

### 5.1 Prompt 路由

`FigureExtractor` 根据文档标题、family、source path、同页文本和 caption 判断文档领域：

| 路由 | 适用场景 | 输出 |
|---|---|---|
| `chip` | SoC / IP / datasheet / TRM / register / signal / bus / AXI / PCIe 等芯片文档 | 芯片语义 JSON：modules / signals / interfaces / clocks_resets / address_regions / connections |
| `general` | 非芯片技术文档、流程图、截图、普通关系图 | 通用图语义 JSON：entities / relationships / summary / mermaid |

两套 prompt 都要求严格 JSON，并由 Pydantic schema 校验。校验失败不会污染图谱。

### 5.2 芯片图语义

芯片图不会只生成自然语言描述。VLM 输出会被归一为：

- `modules` → `MODULE`
- `signals` → `SIGNAL`
- `interfaces` → `INTERFACE`
- `clocks_resets` → `CLOCK`
- `address_regions` → `MEMORY_MAP`
- `connections` → `CONNECTS_TO` / `DEPENDS_ON` / `CONTROLS` / `REFERENCES`
- 实体到图 → `ILLUSTRATED_BY`

所有由图增强产生的节点都会写入：

- `attrs.source = "figure@<version>"`
- `attrs.source_figure_id`
- `attrs.source_block_ids`
- `attrs.source_chunk_ids`

`FIGURE` 节点使用以下语义增强字段：

- `vlm_desc`
- `semantic_summary`
- `semantic_entities`
- `mermaid`
- `wavejson`
- `plantuml`

### 5.3 通用图语义

通用文档不会物化芯片实体。VLM 结果只增强 `FIGURE` 节点的 `semantic_entities`、`semantic_summary` 和可渲染表示，避免把普通流程图误判为芯片结构。

## 6. GlossaryExtractor

`GlossaryExtractor` 抽取缩写、术语表和常见定义模式，产出 `TERM` 节点，并可由 linker 建立 `ALIAS_OF` 等关系。

## 7. 写一个新 Extractor

只有当一种任务无法通过 schema registry 表达时，才新增 extractor。

```python
from docgraph.extractors.base import ExtractContext, ExtractResult
from docgraph.graph.schema import NodeKind, ParsedDoc

class MyExtractor:
    name = "my_custom"
    kinds = {NodeKind.PARAMETER}
    requires = {"section"}

    def extract(self, doc: ParsedDoc, ctx: ExtractContext) -> ExtractResult:
        ...
```

通过 entry points 注册，见 [plugins.md](./plugins.md)。

## 相关文档

- 上一阶段 → [parsers.md](./parsers.md)
- 分层契约 → [layered-architecture.md](./layered-architecture.md)
- 后一阶段 → [linker.md](./linker.md)
