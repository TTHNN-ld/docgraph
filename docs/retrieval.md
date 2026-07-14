# 检索与 MCP

> 本文说明 DocGraph 怎样把 L0、L1、L2 交给 Agent。MCP 提供受预算约束的文档视图，不替 Agent 组织答案。数据分层以 [layered-architecture.md](./layered-architecture.md) 为准。

## 1. 先说清三层各自做什么

- **L0 是原文证据**：段落、表格单元格、图片、公式、页码和坐标都在这里。
- **L1 是阅读材料**：原文按章节、表格和图切成 chunk，适合检索，也适合直接放进 Agent 上下文。
- **L2 是快捷索引**：寄存器、位域、管脚、接口等实体可以快速命中，但覆盖不全时不能挡住 L1/L0。

Parser 生成的是 L0。L1 由 chunker 从 L0 构建，不是 parser 的另一份输出。MCP 返回完整 L1 时，仍然带着 `block_ids`，所以随时可以回到 L0 核对原始表格或图片。

`docgraph_context` 只负责选择读取方式和搬运内容。它不合并改写 chunk，不根据自己的理解删除段落，也不把 VLM 描述写进 L1 原文。最终关注什么、相信什么、是否继续读取，由 Agent 决定。

## 2. 小文档和大文档怎么读

查询不再固定走一条路。`docgraph_context` 先计算选定语料的 L1 大小，再选择读取方式。

```text
L1 字符数和 chunk 数都在预算内
    → 按 doc_id、页码、阅读顺序返回完整 L1

任一指标超出预算
    → 根据 task 检索相关 L1 chunk
    → 需要细节时 fetch 对应 L0 blocks
```

判断依据是内容大小，不是文件数量。一个只有两页的 Markdown 和一份两千页的 TRM 都算“一个文件”，但显然不能用同一种读取方式。

默认预算：

- `max_chars = 40000`
- `max_chunks = 80`

这两个值是单次工具调用的安全上限，不是项目规模限制。超过上限的内容仍然可以通过检索或游标继续读取。

数据库里的 L0/L1 完整性和单次 MCP 响应的完整性是两回事。预算只限制这次返回多少内容，不删除、不覆盖索引中的数据。部分响应必须可继续展开，不能把“本次没返回”说成“系统没有保存”。

### 三种模式

| 模式 | 行为 | 适用情况 |
|---|---|---|
| `auto` | 预算内完整返回 L1，超预算自动检索 | 默认模式 |
| `full` | 顺序读取 L1，达到上限后返回下一页游标 | 通读小文档、分批阅读指定文档 |
| `search` | 始终按任务检索 L1 | 已知问题、较大文档集 |

`full` 不是无限制输出。客户端调高 `max_chars` 时，服务端仍应有硬上限，防止一次响应占满 Agent 上下文。

## 3. `docgraph_context` 返回什么

接口签名：

```python
docgraph_context(
    task: str | None = None,
    mode: Literal["auto", "full", "search"] = "auto",
    doc_ids: list[str] | None = None,
    max_chars: int = 40000,
    max_chunks: int = 80,
    include_enrichments: bool = True,
    max_enrichment_chars: int = 8000,
    cursor: str | None = None,
) -> ContextResult
```

`search` 模式必须提供 `task`。`auto` 模式在语料超预算时也需要 `task`；没有 task 时返回清楚的参数错误，不静默截取开头若干 chunk。

响应至少包含：

```json
{
  "selection": {
    "requested_mode": "auto",
    "mode": "full",
    "reason": "corpus_within_budget",
    "coverage": "complete_l1",
    "l1_complete": true,
    "total_docs": 2,
    "total_chunks": 31,
    "total_chars": 28420,
    "returned_chunks": 31,
    "returned_chars": 28420,
    "candidate_chunks": 31,
    "unreturned_candidates": 0,
    "corpus_chunks_not_returned": 0,
    "truncated": false,
    "next_cursor": null,
    "enrichments_truncated": false
  },
  "chunks": [],
  "enrichments": []
}
```

每个 chunk 返回完整 `text`，并带上：

- `id`、`doc_id`、`kind`
- `page_start`、`page_end`
- `section_id`、`section_node_id`
- `block_ids`
- 与切块有关的 `attrs`

`selection` 不能省略。调用方必须知道这次拿到的是完整 L1、分页结果，还是检索候选。

只有本次响应包含选定范围的所有 L1 chunk 时，才能标记 `l1_complete=true`。一旦使用游标分页，每一页（包括最后一页）都是局部视图，因此保持 `l1_complete=false`；是否还有下一页由 `next_cursor` 表示。检索模式固定返回：

```json
{
  "coverage": "retrieval_candidates",
  "l1_complete": false
}
```

检索无结果只表示“当前查询没有找到候选”，不能表述为“文档中没有相关信息”。

## 4. 排序、分页和预算

完整读取必须使用稳定顺序：

```text
doc_id → page_start → page_end → chunk id
```

不能依赖 SQLite 当前碰巧返回的顺序。游标记录最后一个已返回 chunk 的稳定位置，并绑定本次筛选条件；文档集发生重建后，旧游标应返回过期错误，不能悄悄跳页。

检索游标同时保留原始 task。续读时只传 `cursor` 和原文档范围即可，不要求 Agent 重复拼接同一段查询；若调用方显式传入不同 task，返回游标不匹配错误。

`max_chars` 只计算返回的 L1 chunk 文本。表格的 L1 文本表示计入预算，L0 的完整 cells 和图片不默认展开。VLM/L2 enrichment 使用独立预算，默认最多 8000 字符；达到上限时标记 `enrichments_truncated=true`，不能挤掉已经承诺返回的 L1。

检索模式每页最多返回 20 个高排序 chunk，即使 `max_chunks` 更大也不一次塞满响应；其余候选通过 `next_cursor` 继续读取。`max_chunks` 仍是调用方允许的上限，完整模式不受这个检索分页上限影响。

`chunks[].text` 已是完整 L1 文本。Agent 不需要对每个命中再次调用 `docgraph_fetch`；只有需要 L0 表格 cells、图片/版面证据或来源复核时才 fetch。需要同时核对多个命中时，使用 `docgraph_fetch_many` 一次取回证据，避免重复展开同一页、同一表或同一实体来源。

## 5. 检索方式

超出预算后，L1 使用混合检索：

1. FTS5 处理精确名称、寄存器名和普通关键词。
2. LIKE 为中文和 FTS 无结果的情况兜底。
3. 配置了向量后端时加入语义候选。
4. 最后按名称命中、章节、chunk 类型和语义分数统一重排。

底层 `docgraph_search_chunks` 返回候选和 snippet。`docgraph_context` 的检索模式对候选统一重排后，在预算内装入完整 chunk 文本，并为每个结果保留 `rank_reasons`。Agent 可以直接使用这些 chunk，也可以调用 `docgraph_fetch` 读取对应 L0 blocks。

检索响应必须公开：

- 实际使用了哪些检索方法，例如 exact、FTS、LIKE、semantic。
- 总共形成多少候选，本次返回多少，多少候选因预算未返回，以及整个语料还有多少 chunk 没有出现在本次响应中。
- 每个 chunk 的分数和主要排序理由。
- 下一批候选的游标或继续读取方法。

这些信息用于解释“为什么返回这些内容”，不是让服务端替 Agent 判断它们是否足够。

## 6. L2 在查询中的位置

L2 不参与“小不小”的判断。它可以作为 `enrichments` 随 `docgraph_context` 返回，也可以通过 `docgraph_search` 单独查询。

- `deterministic` 或 `verified` 结果可以直接作为高可信结构化信息使用。
- 来自 LLM、VLM 或来源不明的结果必须标记 `needs_source_check=true`。
- 所有 L2 候选必须带 `source_chunk_ids` 和 `source_block_ids`，方便回到 L1/L0。
- VLM 摘要、Mermaid 和结构化图语义只存在于 enrichment，不写入 L1 `text`。

小文档的 L2 即使很少，也不会影响使用，因为完整 L1 已经在上下文里。大文档仍然能借助 L2 快速命中实体，但最终事实可以回原文确认。

## 7. MCP 工具面

当前 MCP server 已提供：

| 工具 | 用途 |
|---|---|
| `docgraph_context` | 按语料规模返回完整 L1 或检索视图，并公开覆盖范围和续读游标 |
| `docgraph_status` | 查看节点、边和文档统计 |
| `docgraph_files` | 列出已索引文档 |
| `docgraph_search_chunks` | 检索 L1 chunk |
| `docgraph_fetch` | 读取完整 L1 chunk、对应 L0 blocks 和相关 L2 候选 |
| `docgraph_fetch_many` | 批量读取多个 chunk 的 L0/L1 证据，并对 blocks/entities 去重 |
| `docgraph_search` | 搜索 L2 实体 |
| `docgraph_section` | 浏览章节结构 |
| `docgraph_neighbors` | 查看实体邻居和关系 |

`docgraph_context` 是 Agent 的默认入口。它复用现有 chunks 表、全文索引、向量索引和查询引擎能力，不另建一套索引，也不引入新的摘要层。

工具保持少而清楚。寄存器、管脚、时序等专项查询可以继续留在 Query Engine 或 CLI；只有确实能减少调用次数、并且有稳定输入输出契约时，才加入 MCP。

## 8. Agent 的常用路径

### 小文档集

```text
docgraph_context(task, mode="auto")
    → 完整 L1
    → 根据 block_ids 按需 fetch 原始表格或图片
```

### 大文档集

```text
docgraph_context(task, mode="auto")
    → 相关 L1 chunks
    → docgraph_fetch_many(chunk_ids) 或 docgraph_fetch(chunk_id)
    → 必要时结合 L2 候选
```

### 精确实体查询

```text
docgraph_search("PWM_CTRL")
    → L2 候选
    → 按 source_chunk_ids fetch 原文确认
```

## 9. 输出约束

- 输出使用 JSON 和 UTF-8。
- L1 chunk 必须带 `block_ids`。
- L2 候选必须带来源和 `needs_source_check`。
- 所有列表接口必须有数量限制；大文本接口必须支持游标。
- 达到预算时明确返回 `truncated=true` 和 `next_cursor`，不能静默丢内容。
- `full` 只有真正返回选定范围的全部 L1 时才能声明完整；`search` 永远不能声明完整。
- chunk 的 `text` 保持入库内容，不由 MCP 改写或总结。
- 检索结果必须带候选规模、遗漏量、检索方法和排序理由。
- 自动模式必须允许 Agent 用 `full`、`search`、`doc_ids`、预算和 cursor 覆盖或继续。
- `not_found`、参数不足、游标过期和索引未构建要使用不同错误码。

## 相关文档

- 分层契约：[layered-architecture.md](./layered-architecture.md)
- 数据模型：[data-model.md](./data-model.md)
- 配置：[configuration.md](./configuration.md)
- MCP 运维：[operations.md](./operations.md)
