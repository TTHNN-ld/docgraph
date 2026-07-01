# 分层架构（L0 / L1 / L2）—— 权威设计

> 状态：**Accepted（权威）**  
> 日期：2026-06-30  
> 关联：ADR-006（结构化 > 语义检索，已被本文档修订）、ADR-011（见 [roadmap](./roadmap.md)）  
> 影响范围：Parser / Extractor / Storage / Query / MCP —— 全栈

> ⚠️ **本文档是 DocGraph 数据架构的最高权威。** 任何与本文档冲突的旧设计或代码，以本文档为准，并应被改造对齐。后续所有 PR 必须符合这里定义的层次边界。

---

## 1. 背景：两个根本问题

实跑 ARM Cortex-M4 TRM 和 PCIe Subsystem Spec 后暴露出两个致命问题：

### 问题 A：抽取方法不通用

旧架构是 **"预定义本体 + 专用抽取器"**：

```
先定死 NodeKind（register / pin / timing / figure …）
  → 每个 kind 写专用 Extractor（手工正则 + 手工 prompt）
    → 新文档出现新概念（Requirement / Interface / Signal / MemoryMap）
      → 没有对应 extractor → 抽不到 / 抽错
```

后果：**每来一种文档就要调一轮抽取器**。PCIe spec 一来，register 召回低、pin 抽出一堆 `AXI/BAR/CFG/LTSSM` 噪声、还得现加 Requirement/Interface extractor。这条路不可持续。

**结论：通用性不能靠规则，要靠架构。** 行业里没有"通用抽取规则"可抄（IP-XACT / SystemRDL / UPF / SDC 都是输出格式，不是抽取方法）。

### 问题 B：预处理会丢信息

旧架构把"实体抽取"当成系统唯一产出，导致：

- 解析阶段 PyMuPDF 把**表格揉成文本块**（寄存器/引脚/时序 90% 在表格里）→ 信息在第一步就丢
- 抽不到的实体 = 彻底丢失，agent 想回原文也没有结构化版本
- agent 要么拿到残缺实体，要么退回去读 PDF 全文 → 既丢信息又费上下文

**结论：预处理的价值应该是"无损 + 可检索"，而不是"有损压缩成几个实体"。**

---

## 2. 核心决策：三层架构

> **不再追求"一次抽全所有领域实体"。改为"无损保存 + 强检索 + 渐进增强"。**

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

硬错误必须归零；软告警进入质量校准，不得被解释为 L2 抽取问题。

### 3.4 L2：Node / Edge（实体图谱）

保留现有 `Node` / `Edge` 模型，但语义重定位：

- 每个 L2 节点 **必须**带 `source_block_ids` / `source_chunk_ids`（指回 L0/L1）
- `evidence` 字段强制非空，必须记录 extractor、page、chunk 等证据（ADR-008）
- L2 抽取失败只记 `*.failed.jsonl`，**不影响 L0/L1 的完整性**

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
  过程：LLM 按"目标 schema"把表格规整成结构化条目
  schema 由"实体类型注册表"提供，而非硬编码 extractor

实体类型 = 配置 + schema：
  register / bitfield / pin / signal / interface /
  parameter / requirement / memory_map / interrupt / …
  新增一种实体 = 注册一个 schema + 一段说明，不写新代码
```

领域 extractor（register/pin/...）保留为"特例预设"，但底座是同一个通用 `TableEntityExtractor` + `schema registry`。**新文档类型来了，是加一个 schema 条目，不是写一个新 extractor。**

---

## 5. Agent 使用模式（回答"省上下文 + 不丢信息"）

> 常规 agent "全量读全文"被显式禁止为默认路径。

```
1. 定位（L1 检索）
   docgraph_search / docgraph_context："PCIe MSI-X doorbell 配置在哪？"
   → 命中章节 4.6 + 相关 table chunk

2. 按需取原文（L0 片段）
   docgraph_blocks(chunk_id) / docgraph_section(path)
   → 只拉相关的几千 token 无损原文（含完整表格），不是 42 页全文

3. 实体直取（L2 命中时）
   docgraph_register("USP") → 直接拿结构化 register + bitfields，连读都不用
```

各芯片设计阶段拿到"自己要的那部分全量"：

| 阶段 | L1 检索什么 | 拿到 |
|---|---|---|
| RTL 设计 | register map / interface 信号 / 时钟复位 / 参数 | L2 结构化 + L0 表格原文兜底 |
| 验证 | register（建 UVM model）/ 错误注入点 / 协议 | 同上，L2 缺则退 L0 |
| 测试用例 | 寄存器访问序列 / 中断 / 边界值 | L2 + L1 检索原文 |
| 物理/封装 | pin / ball / package | L0 表格（最权威） |

**省上下文** = 只拉相关 chunk；**不丢信息** = 拉到的是 L0 无损原文，且永远有回原文的路径。两者不再矛盾。

---

## 6. 当前实现基线

| 层 | 应达到 | 当前实现 | 状态 |
|---|---|---|---|
| L0 高保真 | 表格单元格 + 图 + 公式 + 坐标全入库 | `blocks` 表落地；MinerU/PyMuPDF 等 parser 统一归一为 `Block`；表格保留 cells/html/image evidence；装饰图保留为 `FIGURE semantic_role=decoration` | ✅ 就绪 |
| L1 切块索引 | chunk 落地 + block 回溯 + 全文 + 语义索引 | `chunks` + `chunks_fts` + `block_ids` + page range + table profile + continued table 合并；语义索引后端可插拔（默认 `sqlite_json`，可选 LanceDB） | ✅ 就绪 |
| L0/L1 质量门 | 可审计、可回归 | `docgraph doctor` / `--strict` 校验 block、chunk、FTS、表格证据、图证据和回溯链 | ✅ 就绪 |
| L2 实体增强 | 通用 schema-guided + 指回 L0/L1 | `TableEntityExtractor` 是统一入口；schema registry 已有 register/pin/timing/signal/interface/requirement 等预设；仍需继续校准生产准确率和覆盖率 | ⚠️ 收紧 |
| Agent 接口 | 检索→取原文片段 | Web/search/chunk detail 已能回溯 L1/L0；MCP/CLI 的 blocks/fetch 入口仍需补齐 | ⚠️ 补齐 |

当前阶段不再新增旧版兼容入口；设计与代码以本文档和当前 L0/L1 契约为准。对外命令保持少量核心入口：`docgraph build`、`docgraph doctor`、`docgraph serve` 和检索/查询类命令。

---

## 7. 改造路线（M7）

详见 [roadmap.md](./roadmap.md) 的 M7。摘要：

1. **M7-P1（P0）L0 无损版面**：真实接入 MinerU/PyMuPDF；`Block` + `TableData` 入库（`blocks` 表）；表格保留单元格和原始证据。
2. **M7-P2（P0）L1 落地**：`chunks` 表 + `chunk→block` 回溯 + FTS5 全文 + 可插拔 embedding/vector store。
3. **M7-P3（P1）L2 通用化**：`TableEntityExtractor` + schema registry；register/pin/timing/signal/interface/requirement 等降为 schema 预设；节点带 `source_block_ids`。
4. **M7-P4（P1）Agent 接口**：新增 `docgraph_blocks` / `docgraph_fetch` / 增强 `docgraph_context` 返回可回溯片段。
5. **M7-P5（P2）领域 schema 包**：register/pin/signal/interface/requirement/memory_map/interrupt 的 schema 预设集。

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
