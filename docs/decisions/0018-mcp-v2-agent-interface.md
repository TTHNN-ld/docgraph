# RFC: 面向 Agent 的 MCP v2 接口

- **ID**: 0018
- **状态**: Accepted
- **起草日期**: 2026-08-26
- **取代**: RFC 0016 中的 MCP 工具集合；L1 预算与完整性规则继续有效

## 问题

旧接口随着功能增加形成了 9 个工具。`context` 与 `search_chunks` 都能发现内容，`fetch` 与 `fetch_many` 只有数量差异，`files` 与 `status` 又分别暴露很薄的一部分状态。Agent 必须先理解 DocGraph 的内部实现，才能选择入口。

协议层也由项目自行处理 JSON-RPC，固定为 `2024-11-05`。业务结果被编码为 `content[0].text` 中的 JSON 字符串，输入校验、结构化输出、错误语义和生命周期需要重复实现。

## 决策

DocGraph 直接使用官方 MCP Python SDK v2 和 `2026-07-28` 协议，不保留旧协议或旧工具名兼容层。MCP 是核心使用路径，因此 SDK 属于核心依赖。

公开工具收敛为 6 个：

| 工具 | 唯一职责 |
|---|---|
| `docgraph_query` | 默认入口：有 task 时检索，没有 task 时顺序浏览 L1 |
| `docgraph_read` | 批量读取一个或多个 chunk 的 L1/L0/L2 证据 |
| `docgraph_entities` | 在指定文档范围内搜索 L2 实体 |
| `docgraph_neighbors` | 有上限地展开一个 L2 节点的关系 |
| `docgraph_outline` | 查看指定文档的章节结构 |
| `docgraph_documents` | 返回文档、构建状态和索引概况 |

旧的 `context`、`search_chunks`、`fetch`、`fetch_many`、`search`、`section`、`files` 和 `status` 全部移除。开发阶段不承担兼容成本。

## 契约

- 所有工具只读，并声明 `readOnlyHint=true`、`openWorldHint=false`。
- 输入通过 Python 类型和 Pydantic 约束生成 JSON Schema，并在进入 handler 前校验。
- 成功结果使用 Pydantic model，SDK 同时生成 `outputSchema` 和 `structuredContent`。
- Agent 能修正的失败使用 `ToolError`，以 `isError=true` 返回；意外异常由 SDK 隐藏内部细节并记录到 stderr。
- 结果字段按用途统一使用 `documents`、`chunks`、`blocks`、`entities`、`nodes`、`edges`、`coverage`、`truncated`、`next_cursor` 和 `warnings`。
- L2 节点使用同一套 `source_quality` 与 source IDs；图关系不能绕过原文取证规则。
- 查询预算由服务端控制。Agent 只提供任务、文档范围和游标，不调整内部字符预算或候选池。

## 查询语义

```text
task 非空  → 检索相关 L1
task 为空  → 按稳定顺序浏览 L1
cursor     → 延续原查询；游标绑定 task、文档范围和索引版本
```

`coverage=complete_l1` 才表示本次响应包含选定范围的全部 L1。`retrieval_candidates` 永远只是候选集合，空结果不能证明原文没有相关内容。

## 验证

- 使用官方 SDK 的内存 Client 验证工具发现、输入 schema、output schema、structured content 和 ToolError。
- 覆盖文档限定、同名章节、未知 ID、分页、图规模限制和来源可信度。
- 端到端 benchmark 记录正确率、工具调用数、输入 token、延迟和证据覆盖。
