# DocGraph RFCs

重大变更（破坏性 API / schema / 跨模块设计）走 RFC 流程。

## 流程

1. 复制 [0000-template.md](./0000-template.md) → `XXXX-your-proposal.md`（X 用下一个空闲编号）
2. 提 PR 标题：`RFC: <标题>`
3. 由维护者评审设计、兼容性、迁移和验证方案
4. 标记为 accepted / rejected / postponed；accepted 后再同步稳定设计和实现

## 当前清单

| ID | 标题 | 状态 |
|---|---|---|
| [0015](./0015-semantic-kg-hybrid-extraction.md) | 语义知识图谱：IP-XACT 对齐本体与混合抽取 | Accepted |
| [0016](./0016-adaptive-l1-context.md) | 按上下文预算提供 L1 文档视图 | Accepted |
| [0017](./0017-l2-candidate-fact-trust-model.md) | L2 候选与事实可信状态模型 | Accepted |
| [0018](./0018-mcp-v2-agent-interface.md) | 面向 Agent 的 MCP v2 接口 | Accepted |
| [0019](./0019-explicit-semantic-retrieval.md) | 显式语义检索与可解释候选融合 | Accepted |
| [0020](./0020-stage-aware-index-build.md) | 分阶段失效与可恢复索引构建 | Accepted |

RFC 是决策历史，不应被当作当前使用说明。稳定结论需同步到对应架构文档，当前工作状态由 [Roadmap](../project/roadmap.md)维护。
