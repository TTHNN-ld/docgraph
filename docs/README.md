# DocGraph 文档

README 负责安装和首次构建；本目录只保存稳定说明、操作指南、决策和可识别的历史材料。

源码仓库使用 `uv sync` 管理环境、`uv run docgraph` 执行命令。指南为简洁起见写作 `docgraph`，需要时在前面加 `uv run`。

## 按任务查找

| 目标 | 从这里开始 |
|---|---|
| 安装并构建第一份文档 | [项目 README](../README.md) |
| 修改配置或接入模型 | [配置指南](./guides/configuration.md) |
| 理解数据如何流转 | [架构总览](./architecture/overview.md) |
| 确认 L0/L1/L2 硬约束 | [分层数据契约](./architecture/data-layers.md) |
| 比较 PDF 后端或新增格式 | [文档导入](./architecture/ingestion.md) |
| 调整实体、关系或可信状态 | [知识图谱构建](./architecture/knowledge-graph.md) |
| 通过 Agent/MCP 查询 | [MCP 接入](./guides/mcp.md)、[工具参考](./reference/mcp-tools.md)、[检索架构](./architecture/retrieval.md) |
| 运行质量门禁或恢复数据 | [运维指南](./guides/operations.md) |
| 开发第三方扩展 | [插件开发](./development/plugins.md) |

## 目录职责

- [`architecture/`](./architecture/)：当前实现必须遵守的系统和数据契约。
- [`guides/`](./guides/)：面向使用者的配置与操作步骤。
- [`development/`](./development/)：扩展接口和开发约束。
- [`reference/`](./reference/)：[MCP 工具参考](./reference/mcp-tools.md)、[术语表](./reference/glossary.md)等精确接口和低频查阅材料。
- [`project/`](./project/)：当前路线和需求演进。
- [`decisions/`](./decisions/)：重大设计决策及其取舍；不是当前使用说明。
- [`research/`](./research/)：带时间点的[后端选型](./research/parser-backends.md)、[L2 验证快照](./research/evaluation/l2-validation.md)和 [PCIe Agent 评测协议](./research/evaluation/pcie-agent.md)，不代表产品承诺。

## 权威边界

- 命令和参数以 `docgraph --help` 为准。
- 配置字段以 `docgraph/core/config.py` 的 Pydantic 模型为准。
- 跨模块数据结构以 `docgraph/graph/schema.py` 为准。
- 数据库结构以 migrations 和 `SQLiteGraphStore` 为准。
- 设计文档的优先级和变更流程见 [DESIGN.md](../DESIGN.md)。

文档只描述稳定事实。短期进度进入 [Roadmap](./project/roadmap.md)，历史需求进入[需求记录](./project/requirements-history.md)，未落地方案进入 RFC。
