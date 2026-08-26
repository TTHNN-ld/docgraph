# RFC: 按上下文预算提供 L1 文档视图

- **ID**: 0016
- **状态**: Accepted
- **作者**: @ld
- **起草日期**: 2026-07-14
- **关联**: ADR-011（三层架构）、ADR-016（自适应查询）

## 摘要

`docgraph_context` 作为 Agent 的统一文档入口，提供一个受预算约束、可解释、可继续展开的文档视图。文档集的 L1 在单次上下文预算内时，工具直接返回完整 L1；超出预算时，自动切换到 L1 检索。服务端公开选择过程和覆盖范围，不替 Agent 总结内容或判断结论。

## 动机

只走实体图谱对小项目并不划算。文档很少时，L2 可能还没有形成足够多的实体和关系，而完整 L1 本身已经不大，直接读取更完整，也更容易核对上下文。

反过来，不能因为项目只有一个文件就读取全文。一份大型 TRM 可能有上千页，必须先检索。系统需要根据实际内容大小选择路径，而不是根据文件数量做猜测。

预算限制的是单次响应，不是底层数据。L0/L1 始终完整保存在索引中；响应没有返回的内容必须能够通过分页、重新检索或 fetch 继续取得。

## 详细设计

### 统一入口

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

`auto` 同时检查选定语料的 L1 字符数和 chunk 数。两项都在预算内时返回完整 L1；任一超出预算时按 `task` 检索。`full` 顺序读取并分页，`search` 始终检索。

### 服务端与 Agent 的边界

服务端负责：

- 计算语料规模并选择默认读取模式。
- 按预算返回原始 L1 chunk。
- 公开检索方法、候选数、遗漏量、排序理由、截断状态和继续读取入口。
- 把 L2/VLM 结果作为独立 enrichment 关联到来源 chunk/block。

Agent 负责：

- 判断返回内容是否回答了当前问题。
- 决定是否相信 enrichment，是否回到 L0 核对。
- 改写查询、指定文档、切换模式、调整预算或继续分页。

服务端不得把多个 chunk 改写成摘要，不得静默截断，也不得把检索无结果解释为文档中不存在相关信息。

### 返回内容

完整读取返回 L1 chunk，不默认展开所有 L0 block。每个 chunk 保留 `block_ids`；需要表格单元格、图片、公式和坐标时，继续调用 `docgraph_fetch`。

响应必须带 `selection`，说明请求模式、实际模式、选择原因、语料总量、返回量、是否截断和下一页游标。

完整性声明使用固定语义：

- 选定范围的所有 L1 已返回：`coverage=complete_l1`、`l1_complete=true`。
- 任何游标分页响应：`coverage=paginated_l1`、`l1_complete=false`；最后一页也只是整个读取序列的一部分。
- 检索模式：`coverage=retrieval_candidates`、`l1_complete=false`。

检索模式还必须返回所用方法、候选总数、返回数量、遗漏数量和每个 chunk 的 `rank_reasons`。任何检索结果都不能声称覆盖了全部相关信息。

检索视图每页最多返回 20 个高排序 chunk，避免 MCP 客户端把过大的工具结果转存为临时文件并引发额外读取；其余候选使用游标继续展开。该限制不影响完整模式。

### 稳定顺序与游标

完整 L1 按 `doc_id → page_start → page_end → chunk id` 排序。游标绑定筛选条件和索引版本。索引重建后旧游标失效，服务端返回明确错误。

检索游标保留原始 task，续读不要求 Agent 重复提交查询文本；显式改写 task 时视为新查询，不能沿用旧游标。

### L2

L2 候选可以随响应返回，但不用于决定是否完整读取。L2 覆盖不足时，Agent 仍可使用完整 L1；L2 命中时仍需遵守 evidence 和 `needs_source_check` 规则。

VLM 描述、图语义和其他模型结果放在 `enrichments`，使用独立字符预算，不写入 L1 `text`。enrichment 截断不能改变 `l1_complete`，需要单独标记 `enrichments_truncated`。

## 备选方案

### 始终检索

实现简单，但小文档会因为关键词或向量召回遗漏上下文，也放大了 L2 覆盖不足的问题。

### 按文件数量判断

不采用。文件数量和内容大小没有稳定关系，无法区分两页说明与千页手册。

### 始终返回完整 L1

不采用。大型文档会占满 Agent 上下文，增加延迟，也让真正相关的内容更难被注意到。

## 迁移路径

- 新增 `docgraph_context`，不修改现有 MCP 工具签名。
- 查询引擎复用现有 chunks 表、FTS、向量索引和 `fetch`，不需要数据库迁移。
- Agent 默认入口逐步从 `search_chunks` 调整为 `context`；现有调用继续可用。
- `search_chunks`、`fetch` 等底层工具继续保留，作为 Agent 覆盖自动选择和继续取证的入口。
- 增加批量取证工具 `docgraph_fetch_many(chunk_ids)`。它不改变检索排序，也不替 Agent 总结证据，只把多个 chunk 的完整 L1、对应 L0 blocks 和相关 L2 候选一次返回，并对重复 blocks/entities 去重。

## 未决问题

- 默认字符预算是否需要根据 MCP 客户端声明的上下文窗口动态调整。
- 跨联邦库读取时，游标如何绑定各库的索引版本。

## 时间线

| 阶段 | 日期 | 备注 |
|---|---|---|
| 设计确认 | 2026-07-14 | Accepted |
| Query Engine 实现 | 2026-07-14 | 语料统计、稳定分页、检索元数据和独立 enrichment 预算 |
| MCP 接入 | 2026-07-14 | 注册 `docgraph_context`，结构化返回可恢复错误 |
| 行为回归测试 | 2026-07-14 | 覆盖完整读取、自动检索、游标失效和 VLM/L1 分离 |
