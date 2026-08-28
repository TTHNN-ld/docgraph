# DocGraph 设计入口

DocGraph 将文档归一为可追溯证据（L0）、可检索内容（L1）和可选知识图谱（L2）。默认查询路径是“L1 定位 → L0 取证 → L2 加速”；L2 或模型失败不能阻断 L0/L1。

## 权威顺序

1. [分层数据契约](./docs/architecture/data-layers.md)定义跨模块不可违反的约束。
2. [架构总览](./docs/architecture/overview.md)定义系统边界和数据流。
3. 架构专题定义导入、知识图谱、检索和联邦行为。
4. [RFC](./docs/decisions/README.md)记录重大变更的理由和取舍。
5. [Roadmap](./docs/project/roadmap.md)记录当前缺口；[需求记录](./docs/project/requirements-history.md)保存演进历史。

实现与稳定设计冲突时，先判断是实现偏差还是设计需要改变。前者修代码；后者先更新 RFC 和稳定设计，再实现。

## 专题索引

| 领域 | 文档 |
|---|---|
| 系统边界与数据流 | [架构总览](./docs/architecture/overview.md) |
| L0/L1/L2、存储与迁移 | [分层数据契约](./docs/architecture/data-layers.md) |
| Parser、格式、增量与缓存 | [文档导入](./docs/architecture/ingestion.md) |
| Extractor、Linker 与可信状态 | [知识图谱构建](./docs/architecture/knowledge-graph.md) |
| 查询、取证与 MCP 契约 | [检索架构](./docs/architecture/retrieval.md) |
| 同项目多文档关系 | [多文档关系](./docs/architecture/federation.md) |
| 配置和运行 | [配置指南](./docs/guides/configuration.md)、[运维指南](./docs/guides/operations.md) |
| 第三方扩展 | [插件开发](./docs/development/plugins.md) |

完整导航见 [docs/README.md](./docs/README.md)。
