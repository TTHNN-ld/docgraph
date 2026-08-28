# MCP 工具参考

DocGraph 通过 6 个只读工具向 Agent 提供已构建的文档和图谱。一般问题从 `docgraph_query` 开始；返回的 chunk 已经包含完整 L1 文本，不需要逐条再次读取。

```text
docgraph_query：找到相关原文
  ├─ 需要核对表格、图片或版面 → docgraph_read
  ├─ 需要精确实体或关系       → docgraph_entities / docgraph_neighbors
  └─ 需要浏览范围或结构       → docgraph_documents / docgraph_outline
```

成功结果同时包含 MCP 文本内容和 `structuredContent`。支持结构化输出的 host 可以直接读取字段，不需要再解析一层 JSON 字符串。

## 工具怎么选

| 目标 | 工具 |
|---|---|
| 回答文档问题，或按顺序浏览原文 | `docgraph_query` |
| 展开 chunk 对应的 L0 原始证据 | `docgraph_read` |
| 搜索寄存器、位域、信号等 L2 实体 | `docgraph_entities` |
| 从实体继续查看关系 | `docgraph_neighbors` |
| 查看文档章节结构 | `docgraph_outline` |
| 获取文档 ID、构建状态和索引概况 | `docgraph_documents` |

## `docgraph_query`

这是默认入口。有 `task` 时检索相关 L1；不传 `task` 时按稳定顺序浏览文档。

```json
{
  "task": "DMA 中断状态如何清除？",
  "doc_ids": ["dma-spec"],
  "include_entities": false
}
```

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `task` | 空 | 问题或检索意图；空值表示顺序浏览 |
| `doc_ids` | 全部文档 | 限定文档范围，ID 来自 `docgraph_documents` |
| `cursor` | 空 | 上一次返回的 `next_cursor`；续读时不用重复其他参数 |
| `include_entities` | `false` | 同时返回与 chunk 关联的 L2 实体 |

服务端管理字符预算、chunk 数和候选池，Agent 不需要调这些内部参数。结果中的关键字段是：

- `chunks`：完整 L1 文本及页码、章节、来源 block ID；
- `coverage`：本次结果覆盖到什么程度；
- `next_cursor`：非空时可以继续；
- `entities`：仅在请求增强信息时返回；
- `warnings`：重要的完整性或取证提醒。

`coverage` 有三种取值：

- `complete_l1`：本次已包含选定范围的全部 L1；
- `paginated_l1`：这是顺序浏览中的一页；
- `retrieval_candidates`：这是检索候选，空结果也不能证明原文没有相关信息。

游标会恢复原 task 和 `doc_ids`；检索续页使用首轮冻结的候选顺序，不会再次调用 embedding。L1 重建后游标失效，应从第一次查询重新开始。

## `docgraph_read`

一次读取 1–20 个 `chunk_ids`，用于核对表格单元格、图片、公式、坐标、阅读顺序或 L2 来源。

```json
{"chunk_ids": ["chunk-a", "chunk-b"]}
```

结果包含完整 `chunks`、去重后的 L0 `blocks`、关联的 `entities`，以及每个 chunk 到 block/entity 的 `links`。部分 ID 不存在时，已有结果仍会返回，并在 `missing_chunk_ids` 和 `warnings` 中说明；全部不存在时调用失败。

## `docgraph_entities`

按名称或别名搜索 L2 实体。

```json
{
  "query": "DMA_INT_STATUS",
  "kind": "register",
  "doc_ids": ["dma-spec"],
  "limit": 10
}
```

| 参数 | 默认值 | 限制 |
|---|---:|---|
| `query` | 必填 | 不能是空白字符串 |
| `kind` | 空 | 可选的 `NodeKind` |
| `doc_ids` | 全部文档 | 精确的文档 ID |
| `limit` | `20` | 1–50 |

每个实体使用统一的来源字段和 `source_quality`。当 `needs_source_check=true` 时，应通过 `source_chunk_ids` 调用 `docgraph_read`，不要把模型抽取结果直接当成原文事实。

## `docgraph_neighbors`

从一个精确的 L2 `node_id` 展开关系图。

```json
{
  "node_id": "node-id",
  "edge_kinds": ["has_bitfield"],
  "depth": 1,
  "max_nodes": 50
}
```

`depth` 允许 1–3，`max_nodes` 允许 1–100。图超过上限时返回 `truncated=true`；此时应缩小 `edge_kinds` 或深度。返回的 `nodes` 与实体搜索使用相同的来源和可信度字段。

## `docgraph_outline`

查看一份文档的章节结构，或从一个精确章节继续展开。

```json
{"doc_id": "dma-spec", "depth": 2}
```

| 参数 | 默认值 | 限制 |
|---|---:|---|
| `doc_id` | 必填 | 来自 `docgraph_documents` |
| `section_id` | 空 | 精确的章节节点 ID，不做模糊匹配 |
| `depth` | `1` | 1–3 |
| `limit` | `200` | 1–500 |

精确 ID 可以避免多份文档或同一文档中同名章节造成歧义。

## `docgraph_documents`

不需要参数。返回：

- `documents`：每份已索引文档的 `doc_id`、路径、parser、构建/质量状态、降级原因、chunk 数和字符数；
- `graph`：节点、关系、类型分布和向量条目数；
- `build`：最近一次整体构建的 success/degraded/failed、失败文件数、降级原因和模型费用；
- `derived`：Linker、Embedding 等全局派生索引的状态、时间、错误、条目数和模型费用。

它适合在不知道文档范围时先调用，也能判断索引是否存在或处于降级状态；统计数字不能证明抽取质量已经达标。

## 错误与协议

参数类型、枚举和范围由工具的 JSON Schema 校验。未知文档、失效游标或不存在的 ID 等可修正错误以 MCP `isError=true` 返回；意外异常不会把内部堆栈暴露给 Agent。

服务基于官方 MCP Python SDK v2，使用 `2026-07-28` 协议和本地 stdio transport。工具只读取现有索引，不会自动执行 `docgraph build`。运行时 schema 以 [`docgraph/mcp/server.py`](../../docgraph/mcp/server.py) 为准，查询与取证语义以 [`QueryEngine`](../../docgraph/query/engine.py) 为准。
