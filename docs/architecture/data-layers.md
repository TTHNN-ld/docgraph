# 分层数据契约

本文是 Parser、Chunker、Extractor、Store、Query 和 MCP 共同遵守的权威契约。精确字段以 [`docgraph/graph/schema.py`](../../docgraph/graph/schema.py) 为准；本文只保留跨模块不变量。

## 三层模型

| 层 | 角色 | 核心对象 | 是否可选 |
|---|---|---|---|
| L0 | 可追溯原文和版面证据 | `ParsedDoc`、`ParsedPage`、`Block`、`TableData` | 否 |
| L1 | 可寻址的阅读与检索单元 | `Chunk`、FTS、向量索引 | 否 |
| L2 | 领域实体与关系增强 | `Node`、`Edge`、candidate/fact 状态 | 是 |

## 不可违反的约束

1. L0 应保留后端能够取得的文本、表格单元格、图片、公式、页码、坐标、阅读顺序和章节关系；能力缺口必须显式暴露。
2. L1 chunk 必须有稳定 ID、非空文本和 `block_ids`，所有来源引用都必须命中 L0。
3. L2 失败不能阻断 L0/L1，也不能成为访问文档内容的唯一入口。
4. L2 节点必须携带 `source_block_ids`、`source_chunk_ids` 和非空 `evidence`；边必须能解释其推导来源。
5. LLM/VLM 输出默认是 candidate。只有来源完整、确定性或人工验证、且结构校验无错误的结果才能成为 fact。
6. 查询默认从 L1 开始；需要表格 cells、图片、公式或坐标时回到 L0，L2 只用于加速。

## L0：Block

`Block` 是权威证据单元，常见 `kind` 包括 paragraph、heading、table、figure、formula、code、list 和 caption。关键字段是：

- 稳定 `id`、`doc_id`、页码和 `reading_order`。
- 文本或结构化 `TableData`；表格不能只保存拼接后的纯文本。
- 可用时保存 bbox、图片路径、公式、HTML、章节路径和 parser 属性。

不同格式的保真能力可以不同，但不能虚构缺失证据。轻量 DOCX/XLSX parser 的能力边界见[文档导入](./ingestion.md)。

## L1：Chunk

Chunk 按章节、表格或图组织用于阅读和检索，至少包含稳定 `id`、`doc_id`、`text`、`kind`、`block_ids` 和页范围。

- 章节可以按长度继续切分。
- 每张表和图保持独立语义单元；跨页 continued 表可以保守合并为 logical table。
- `block_ids` 是回到 L0 的权威路径。
- FTS、向量和排序分数都是派生索引，不改变 chunk 原文。

## L2：Node、Edge 与可信状态

L2 覆盖 register、bitfield、pin、signal、interface、interrupt、memory map、requirement、figure 等领域对象。它允许覆盖不完整，但必须可审计。

`attrs` 中的关键可信字段：

| 字段 | 含义 |
|---|---|
| `l2_status` | `candidate` 或 `fact` |
| `derivation` | deterministic、LLM、VLM、manual 等来源 |
| `validation_issues` | 结构校验发现的问题 |
| `extraction_confidence` | 抽取方式或置信层级 |

多来源命中同一实体时合并来源和 evidence，不覆盖已有确定性字段。结构化表格证据优先于 VLM 或自由文本推断。具体抽取和连接规则见[知识图谱构建](./knowledge-graph.md)。

## 身份与回溯

- document ID 必须包含稳定的项目相对来源路径语义，避免同名或不同扩展名互相覆盖。
- block、chunk、node 和 edge ID 必须在输入及相关配置未变时保持稳定。
- node/edge → chunk → block → source document 的链路必须可查询。
- 重建可以替换派生对象，但不得静默覆盖其他文档或丢失失败前的可恢复状态。

## 存储职责

SQLite 保存 documents、blocks、chunks、nodes、edges、FTS 和 schema version。向量后端保存可重建索引；manifest 保存来源 hash、实际 parser、fallback、状态和阶段统计。

单文档重建必须在一个事务中替换该文档的 L0/L1/L2。完整构建负责删除对账；migration 必须先备份，失败时回滚且不能提前写入新版本号。

精确数据库结构以 [`SQLiteGraphStore`](../../docgraph/graph/sqlite_store.py) 和 migrations 为准，不在文档中复制 SQL。

## 质量门禁

`docgraph doctor --strict` 至少验证：

- 每个文档都有 L0 block 和 L1 chunk。
- chunk 来源、页范围、文本、source hash 和 FTS 行数一致。
- 表格与图证据满足已声明的 parser 能力。
- L2 来源链真实存在，fact 晋升合法。
- register/bitfield 等强结构实体通过位宽、范围、重叠和引用校验。

设计背景见 [RFC 0015](../decisions/0015-semantic-kg-hybrid-extraction.md)、[RFC 0016](../decisions/0016-adaptive-l1-context.md)和 [RFC 0017](../decisions/0017-l2-candidate-fact-trust-model.md)。
