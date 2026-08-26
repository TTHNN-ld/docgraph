# 检索与 MCP

DocGraph 的目标不是把整份文档塞给 Agent，而是让 Agent 用尽量少的调用拿到足够原文，并知道证据是否完整。L1 chunk 是默认阅读材料，L0 block 用于核对原始版面，L2 实体和关系只负责加速定位。

## 默认查询

`docgraph_query` 只有两种直接语义：

```text
提供 task → 检索相关 L1
省略 task → 按稳定顺序浏览 L1
提供 cursor → 延续上一次查询
```

Agent 不控制字符预算、候选池或内部检索模式。这些是服务端执行策略，避免不同 host 反复猜参数，也方便实现根据模型上下文能力统一调整。

默认单次预算是 40,000 字符、80 个顺序浏览 chunk；检索每页最多返回 20 个 chunk。预算只限制一次响应，不会删除或截断数据库中的原文。

## 完整性不是命中率

每次查询都明确返回 `coverage`：

- `complete_l1`：本次确实包含所选范围的全部 L1；
- `paginated_l1`：这是顺序浏览的一部分；
- `retrieval_candidates`：这是排序后的候选，不代表覆盖全文。

只有 `complete_l1` 能设置 `l1_complete=true`。检索无结果不能解释成“文档中不存在”；Agent 可以改写问题、缩小文档范围或顺序浏览。

完整读取顺序固定为 `doc_id → page_start → page_end → chunk id`。游标绑定原查询、文档范围和索引版本；条件变化或索引重建后必须失效，不能静默跳页。

## 检索与取证

检索候选来自可用的 FTS5、LIKE 和语义索引，再结合名称、章节和 chunk 类型重排。返回结果会说明实际使用的 `retrieval_methods` 和剩余候选数，但不会声称候选足以回答问题。

`chunks[].text` 保持入库后的完整 L1 文本，不由 MCP 再总结或改写。只有需要表格单元格、图片、公式、bbox、阅读顺序或来源复核时，才把 chunk ID 交给 `docgraph_read`。批量读取会去重共同引用的 blocks 和 entities。

L2 不参与 L1 完整性判断，也不能成为唯一证据路径：

- deterministic/verified 结果可以作为高可信结构信息；
- LLM/VLM 或来源不完整的结果标记 `needs_source_check=true`；
- 实体必须能通过 source chunk/block 回到原文；
- `docgraph_neighbors` 返回的关系也遵守同一来源规则。

## 六个工具的边界

| 工具 | 系统保证 |
|---|---|
| `docgraph_query` | 返回完整 L1 chunk，并明确覆盖范围和续读位置 |
| `docgraph_read` | 批量展开 L1、L0 与相关 L2，去重共同证据 |
| `docgraph_entities` | 在可选文档范围内查找统一格式的 L2 实体 |
| `docgraph_neighbors` | 按关系、深度和节点上限展开图，不无限遍历 |
| `docgraph_outline` | 用精确文档/章节 ID 浏览结构，避免同名歧义 |
| `docgraph_documents` | 合并文档构建元数据和当前索引统计 |

这个边界把“选什么查询策略”留给 Agent，把预算、完整性、来源、状态和规模上限留给系统保证。旧的单条/批量读取、文档列表/状态等薄工具已合并，不保留兼容别名。

## 一次调用如何执行

```text
MCP host
  → 官方 SDK 的 stdio 会话
  → JSON Schema 输入校验
  → MCP tool handler
  → QueryEngine
     ├─ SQLiteGraphStore：L0、L1/FTS、L2
     └─ VectorStore + EmbeddingProvider：可选语义候选
  → 预算、排序、去重和来源关联
  → outputSchema 对应的 structuredContent
```

SDK 负责协议协商、工具发现、schema、错误封装和生命周期；DocGraph 只负责查询及证据语义。启动时服务从 `cwd` 定位项目，加载配置并打开 `.docgraph/graph.db`；查询不会在后台触发构建。

可修正的输入或查询错误以 MCP `isError=true` 返回。意外异常由 SDK 隐藏内部细节并写入 stderr，避免污染 stdio 协议输出。

接口参数见 [MCP 工具参考](../reference/mcp-tools.md)，host 配置见 [MCP 接入](../guides/mcp.md)，数据硬约束见[分层数据契约](./data-layers.md)。当前接口决策见 [RFC 0018](../decisions/0018-mcp-v2-agent-interface.md)；L1 预算与完整性设计沿用 [RFC 0016](../decisions/0016-adaptive-l1-context.md)。
