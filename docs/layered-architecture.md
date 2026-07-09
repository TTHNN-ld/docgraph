# 分层架构（L0 / L1 / L2）—— 权威设计

> 状态：**Accepted（权威）**  
> 日期：2026-07-02  
> 关联：ADR-006（结构化 > 语义检索，已被本文档修订）、ADR-011（见 [roadmap](./roadmap.md)）  
> 影响范围：Parser / Extractor / Storage / Query / MCP —— 全栈

> ⚠️ **本文档是 DocGraph 数据架构的最高权威。** 任何与本文档冲突的旧设计或代码，以本文档为准，并应被改造对齐。后续所有 PR 必须符合这里定义的层次边界。

---

## 1. 设计目标

DocGraph 的事实底座不是某一次实体抽取结果，而是可审计、可回溯、可增量更新的文档结构镜像。芯片文档的寄存器、管脚、时序、接口、需求和图表常分布在表格、正文、章节标题和图片中，因此系统必须同时满足：

- **解析不丢信息**：Parser 负责保留页面、表格单元格、图、公式、坐标、顺序和章节归属。
- **定位足够精准**：Agent 先定位相关 chunk，再按需拉取 L0 原文片段，避免把全文当上下文。
- **实体可增强但不可独占**：L2 图谱提升查询效率和结构化程度；L2 缺失时，L1/L0 仍然能回答问题。
- **新增文档类型不改底座**：新 parser 只需归一到 `ParsedDoc/Block`，新实体类型优先通过 schema registry 扩展。

---

## 2. 核心决策：三层架构

> DocGraph 采用"无损保存 + 强检索 + 渐进增强"的数据架构。

```
┌──────────────────────────────────────────────────────────────┐
│ L0  高保真版面层（Faithful Layout）—— 完全通用，永不丢信息       │
│  每页完整保留：文本块 + 表格(单元格结构) + 图 + 公式 + 坐标 + 顺序 │
│  = 原文的"结构化镜像"；任何芯片文档同样处理                       │
│  agent 任何时候可回到这层拿到无损原文                            │
├──────────────────────────────────────────────────────────────┤
│ L1  切块与多索引层（Chunk & Index）—— 完全通用                   │
│  章节 / 表格 / 图 各自成可寻址 chunk（稳定 ID + 来源回溯）        │
│  向量索引 + 全文索引 + 章节路径索引                              │
│  agent "先定位，再按需拉取"——省上下文的关键在这层                │
├──────────────────────────────────────────────────────────────┤
│ L2  实体增强层（Entity Enrichment）—— 领域相关，但只是"可选加速"  │
│  register / pin / signal / interface / requirement / param …    │
│  抽到 → agent 精确查询；抽不到 → L0/L1 兜底，信息不丢            │
└──────────────────────────────────────────────────────────────┘
```

### 不可违反的层次契约

1. **L0 必须无损**：任何 PDF/DOCX/XLSX/MD 进来，L0 都要保留到"能重建原文语义"的程度——尤其是表格单元格、图、公式。Parser **不允许**像现在这样把表格丢成 `[]`。
2. **L1 必须可寻址、可回溯**：每个 chunk 有稳定 ID，能反查到 L0 的页/坐标/原文。
3. **L2 必须是可选增强，不得成为唯一入口**：L2 抽取失败**绝不能**导致信息丢失；agent 永远能绕过 L2 直达 L1/L0。
4. **agent 默认走 "L1 检索 → 按需取 L0 片段 → L2 命中则直接用"**，禁止"读全文"作为常规路径。

---

## 3. 数据模型

### 3.1 L0：Block（版面块）—— 新增一等公民

L0 的原子是 **Block**，而不是现在被弱化的 `TextBlock`。它必须能装下表格/图/公式：

```python
class BlockKind(str, Enum):
    PARAGRAPH = "paragraph"
    HEADING   = "heading"
    TABLE     = "table"
    FIGURE    = "figure"
    FORMULA   = "formula"
    CODE      = "code"
    LIST      = "list"
    CAPTION   = "caption"

class Block(BaseModel):
    id: str                      # 稳定 ID：<doc_id>#p<page>#b<idx>
    doc_id: str
    page: int
    kind: BlockKind
    reading_order: int
    bbox: BBox | None
    text: str | None             # 文本类
    table: TableData | None      # 表格类：保留 headers + rows(单元格) + 合并信息
    image_path: str | None       # 图/公式渲染
    latex: str | None            # 公式
    html: str | None             # 表格原始 HTML（如 parser 提供）
    section_path: str | None     # 归属章节
    attrs: dict

class TableData(BaseModel):
    headers: list[str]
    rows: list[list[str]]        # 单元格级别，禁止再揉成纯文本
    n_rows: int
    n_cols: int
    merged_cells: list[dict] = []  # 合并单元格信息（可选）
    caption: str | None = None
```

**L0 存储**：所有 Block 落库（`blocks` 表），保留页码/坐标/章节，可全量回溯。

### 3.2 L1：Chunk（检索单元）

```python
class Chunk(BaseModel):
    id: str                      # 稳定 ID
    doc_id: str
    kind: str                    # section | table | figure
    chunk_type: str              # 与 kind 对齐，保留显式类型字段便于索引
    section_id: str | None       # 人读章节号，如 "5.3.2"
    section_node_id: str | None  # 稳定 SECTION 节点 ID
    block_ids: list[str]         # 反查 L0 的来源（关键！）
    text: str                    # 用于嵌入/全文检索的文本表示
    page_start: int | None
    page_end: int | None
    source_hash: str | None
    attrs: dict
```

切块原则：
- 章节 → section-aware chunk；同一章节可跨页归并，必要时按长度切分
- 每个表格 → 独立 chunk（不切碎，整表是一个语义单元）
- 每张图 → 独立 chunk
- chunk 必须保留 `block_ids`，并尽量绑定 `section_node_id`
- 前言、目录、封面等无法稳定归属章节的内容允许 `section_node_id=None`

**L1 存储**：`chunks` 表落地；`chunk → block_ids` 保留以支持"回到原文"；已支持 FTS5 全文索引、章节路径、`section_node_id` 与页范围。table chunk 带 `table_profile`（类型、continued、质量标记），并保守合并相邻 continued 表为 `logical_table`。L1 chunk 已接入可插拔语义索引：默认 `sqlite_json` 本地轻量后端，也可配置 LanceDB；检索采用 FTS/LIKE + semantic candidate + 规则重排的 hybrid ranking。

### 3.3 分层生产质量门禁

L0/L1 是 DocGraph 的事实底座，L2 是可选增强，但所有层都必须可审计、可回归。`docgraph doctor` 对当前库执行分层质量审计：

- L0 每个文档必须有 block；L1 每个文档必须有 chunk。
- 每个 L1 chunk 必须有 `block_ids`，且所有引用必须能命中 L0 block。
- `chunks_fts` 行数必须与 `chunks` 一致。
- chunk 文本不能为空，页范围必须合法，`source_hash` 应存在。
- 表格 cell 保留率、原始证据覆盖率、figure image/caption-only 覆盖率作为质量输出。
- 高价值 L2 实体必须带 `source_block_ids`、`source_chunk_ids` 和真实 `evidence.extractor`，且引用必须命中 L0/L1。
- 强结构 L2 实体必须通过确定性约束：register/bitfield 校验位宽、位域范围、重叠和 register 引用；signal/interface 校验名称与宽度表达；interrupt 校验编号表达；memory_map 校验名称与地址/目标/尺寸定位信息。
- L2 抽取器必须在入库前处理模型/OCR 造成的结构冲突，例如同一 register 下重叠 bitfield 只保留确定性选择出的无重叠集合，并在 register attrs 中记录 `dropped_bitfields`。

硬错误必须归零；软告警进入质量校准，不得被解释为 L2 抽取问题。

### 3.4 L2：Node / Edge（实体图谱）

保留现有 `Node` / `Edge` 模型，但语义重定位：

- 每个 L2 节点 **必须**带 `source_block_ids` / `source_chunk_ids`（指回 L0/L1）
- `evidence` 字段强制非空，必须记录 extractor、page、chunk 等证据（ADR-008）
- L2 节点带 `attrs.extraction_confidence` 分层：`deterministic`（normalizer/正则抽的，高可信）/ `llm` / `vlm`（模型抽的，agent 用时建议回溯 L0 原文）
- L2 抽取失败只记 `*.failed.jsonl`，**不影响 L0/L1 的完整性**

L2 实体按信息载体分三类，各有抽取路径：

| 载体 | 实体 | 抽取路径 |
|---|---|---|
| 表格 | register / pin / timing / memory_map / interrupt / signal / interface / constraint | `TableEntityExtractor` deterministic normalizer 优先，LLM 兜底 |
| 正文段落 | requirement / errata / glossary term | `TextEntityExtractor` 正则确定性抽取 |
| 框图 | module / interface / connection / clock_reset | `FigureExtractor` VLM 语义抽取 |

三条路径都遵循同一约束：节点带 `source_block_ids` 回溯、`extraction_confidence` 分层、失败不影响 L0/L1。

### 3.5 语义关系层（ADR-015）

L2 不仅是"实体 + 出处"，还要有实体间的语义关系。本体对齐 IP-XACT（IEEE 1685-2022），关系类型化（`belongs_to` / `contained_in` / `mapped_to` / `drives` / `clocks` / `resets` / `implements`，新增 `EdgeKind`）。抽取分三层：

- **A 确定性事实**：表格 → 实体 + 精确属性 + `has_bitfield`（保留，结构拆解非溯源）。
- **B 确定性关系推断**（`RelationInferExtractor`，新增）：章节归属（实体 → 所属 module）、地址 join（memory_map → register）、名字 join（跨来源同名实体合并）。零 LLM、高精度，补大半语义边。
- **C LLM 开放 IE**（`LLMIEExtractor`，新增）：B 未覆盖的文本 chunk 调 LLM 抽三元组，约束在本体关系类型，evidence 必填，confidence 门槛 0.6。受 `llm.enabled` 控制。

**溯源移出图边**：`illustrated_by` / `contains` 标 deprecated（保留节点 attrs 的 `source_block_ids` / `source_chunk_ids`），图谱边只留语义关系 + `has_bitfield`。详见 [RFC 0015](./rfcs/0015-semantic-kg-hybrid-extraction.md)。

---

## 4. L2 抽取：从"专用正则"到"通用 schema-guided"

去掉"每项目调"的关键改造：

### 旧（不可持续）
```
RegisterExtractor: 手写 _BITFIELD_RE 正则 + 手写触发词
PinExtractor:      手写 _PIN_HEADER_HINT
TimingExtractor:   手写 _TIMING_ROW
新文档新概念 → 新写一个 extractor
```

### 新（通用）
```
EntityCandidate（统一候选层）:
  输入：L1 chunk + L0 block
  产出：table / text / table_image / page_image / figure 候选
  每个候选带 chunk_ids + block_ids + page/section/image/text/table
  page_image 候选也必须绑定同页 L1 chunk，不能只保留整页图片

TableEntityExtractor（通用）:
  输入：EntityCandidate
  过程：确定性 normalizer 优先；无法可靠规整时再由 LLM/VLM 按"目标 schema"抽取
  schema 由"实体类型注册表"提供，而非硬编码 extractor

实体类型 = 配置 + schema：
  register / bitfield / pin / signal / interface /
  parameter / requirement / memory_map / interrupt / …
  新增一种实体 = 注册一个 schema + 一段说明，不写新代码
```

领域 extractor（register/pin/...）保留为"特例预设"，但底座是同一个通用 `TableEntityExtractor` + `schema registry`。**新文档类型来了，是加一个 schema 条目，不是写一个新 extractor。**

芯片文档的 L2 质量策略：

- 表格是硬证据，优先走确定性 normalizer。当前已覆盖 register/bitfield、memory_map、interrupt、signal、interface 的常见 IP spec/TRM 表型，包括 `Interface Group / 方向 / Description`、`irq_src信号 / 位宽 / Description`、`Interface / Base Address`、`Fields for Register` 等行业常见格式。
- VLM/figure 抽取用于补充框图语义、模块互连、时钟复位、地址图和数据通路，不应覆盖表格中更精确的结构化字段。
- 同一实体来自表格和图时，图存储必须做多源合并：保留 table_entity 的确定性字段，合并 `source_block_ids` / `source_chunk_ids` / evidence / aliases，并记录 `sources`，避免后写入的软证据覆盖硬证据。
- 抽取器入库前要规整 OCR/VLM 常见噪声，如 `null`、`1 1`、`512b` 等宽度表达；不合规字段不得依赖 doctor 事后兜底。
- 文档类型路由影响 schema 启用范围：`spec/protocol/interface spec/subsystem spec` 默认按 protocol 启用 register/pin/signal/interface/memory_map/interrupt 等核心 schema；协议规范和 subsystem spec 也经常包含寄存器表，不能误关 register/pin 抽取。TRS 等无法确定类型的文档走 UNKNOWN 核心路由（register/pin/memory_map/interrupt），避免误关寄存器抽取。
- build 结束后按当前 include 文件的 doc_id 集合清理 stale docs，支持新增、删除、重命名或类型路由变化后的增量图谱一致性。

---

## 5. Agent 使用模式（回答"省上下文 + 不丢信息"）

> 常规 agent "全量读全文"被显式禁止为默认路径。

```
1. 定位(L1 检索优先)
   docgraph_context / docgraph_search_chunks："PCIe MSI-X doorbell 配置在哪？"
   → 命中章节 4.6 + 相关 table chunk

2. 按需取原文(L0 片段)
   docgraph_fetch(chunk_id) / docgraph_blocks(block_ids)
   → 只拉相关的几千 token 无损原文（含完整表格），不是 42 页全文

3. 实体直取(L2 命中时)
   docgraph_register("USP") / docgraph_search("USP")
   → 先拿结构化候选，再用 source_chunk_ids/source_block_ids 回溯验证
```

L2 图谱是加速索引，不是唯一事实源。MCP 输出中的 `needs_source_check=true`
表示该节点来自 VLM/LLM 或缺少确定性置信度，agent 必须用 `docgraph_sources`
回到 L1/L0 证据后再生成结论。

各芯片设计阶段拿到"自己要的那部分全量"：

| 阶段 | L1 检索什么 | 拿到 |
|---|---|---|
| RTL 设计 | register map / interface 信号 / 时钟复位 / 参数 | L2 结构化 + L0 表格原文兜底 |
| 验证 | register（建 UVM model）/ 错误注入点 / 协议 | 同上，L2 缺则退 L0 |
| 测试用例 | 寄存器访问序列 / 中断 / 边界值 | L2 + L1 检索原文 |
| 物理/封装 | pin / ball / package | L0 表格（最权威） |
| 后端实现 | SDC/STA 约束 / floorplan / placement / routing / power grid / PVT corner | L2 constraint/physical_constraint + L0 表格原文兜底 |

**省上下文** = 只拉相关 chunk；**不丢信息** = 拉到的是 L0 无损原文，且永远有回原文的路径。两者不再矛盾。

---

## 6. 当前实现基线

| 层 | 应达到 | 当前实现 | 状态 |
|---|---|---|---|
| L0 高保真 | 表格单元格 + 图 + 公式 + 坐标全入库 | `blocks` 表落地；MinerU/PyMuPDF 等 parser 统一归一为 `Block`；表格保留 cells/html/image evidence；装饰图保留为 `FIGURE semantic_role=decoration` | ✅ 就绪 |
| L1 切块索引 | chunk 落地 + block 回溯 + 全文 + 语义索引 | `chunks` + `chunks_fts` + `block_ids` + page range + table profile + continued table 合并；语义索引后端可插拔（默认 `sqlite_json`，可选 LanceDB） | ✅ 就绪 |
| L0/L1 质量门 | 可审计、可回归 | `docgraph doctor` / `--strict` 校验 block、chunk、FTS、表格证据、图证据和回溯链 | ✅ 就绪 |
| L2 实体增强 | 通用 schema-guided + 指回 L0/L1 | `TableEntityExtractor` 是统一入口；schema registry 已有 register/pin/timing/signal/interface/requirement/memory_map/interrupt/constraint/physical_constraint 等预设；`doctor --strict` 校验 provenance 和强结构实体约束 | ✅ 可试生产 |
| Agent 接口 | 检索→取原文片段 | Web/search/chunk detail 已能回溯 L1/L0；MCP/CLI 的 blocks/fetch 入口仍需补齐 | ⚠️ 补齐 |

对外命令保持少量核心入口：`docgraph init`、`docgraph build`、`docgraph doctor`、`docgraph serve` 和检索/查询类命令。

---

## 7. 配置与运行目录

DocGraph 按 codegraph/Claude Code 风格区分用户配置、项目覆盖和生成物：

- `~/.docgraph/config.yaml`：用户级模型、embedding、VLM、API key、base URL、成本和默认偏好。
- `~/.docgraph/.env`、`~/.docgraph/.env.local`：可选环境变量兼容路径。
- `<project>/docgraph.yaml`：可选项目级覆盖，用于文档范围、芯片 family、parser/extractor 策略。
- `<project>/.docgraph/`：纯生成目录，保存 `graph.db`、缓存、日志、manifest、导出结果，不保存人工维护配置。

普通项目无需 `docgraph.yaml` 也能运行；默认扫描 `docs/**/*.pdf` 与 `spec/**/*.pdf`。PDF 默认使用轻量 PyMuPDF，复杂版面可在项目覆盖中配置 MinerU + PyMuPDF fallback。

---

## 8. 治理：设计文档是唯一权威

- 本文档 + DESIGN.md + `docs/` 是 DocGraph 的**唯一权威设计**。
- **代码必须紧跟设计文档**：任何实现与本文档冲突时，改代码、不改文档（除非先走 RFC 修订文档）。
- 重大架构变更先改文档（走 [RFC 流程](./rfcs/)），再写代码。
- 每个 PR 描述需注明：遵循/修订了哪一条设计条款。
- L0/L1/L2 的层次契约（§2）是硬约束，review 时必须检查：
  - Parser 是否把表格无损入库（不允许丢成 `[]`）
  - L2 抽取失败是否影响了 L0/L1（不允许）
  - L2 节点是否带 `source_block_ids`（必须）
