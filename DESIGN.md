# DocGraph 设计文档

> 面向芯片 Spec 的文档知识图谱引擎  
> *Spec-driven Chip Development Agent 的底层数据基座*

---

## 文档导航

本设计文档已拆分为 `docs/` 下的多份话题文档。本文件作为顶层索引。

> ⭐ **数据架构最高权威：[docs/layered-architecture.md](./docs/layered-architecture.md)**（L0 无损版面 / L1 检索 / L2 实体增强）。任何与之冲突的旧设计或代码以它为准。**代码必须紧跟设计文档**；冲突时改代码、不改文档（除非先走 [RFC](./docs/rfcs/) 修订）。

| 话题 | 文档 | 内容 |
|---|---|---|
| ⭐ **分层架构（权威）** | [docs/layered-architecture.md](./docs/layered-architecture.md) | L0/L1/L2 三层、层次契约、通用 schema-guided 抽取、agent 使用模式 |
| 🏛️ **架构总览** | [docs/architecture.md](./docs/architecture.md) | 项目定位、设计原则、分层架构、数据流 |
| 📊 **数据模型** | [docs/data-model.md](./docs/data-model.md) | 节点 / 边 / Evidence、SQLite schema、迁移 |
| 📄 **Parser 层** | [docs/parsers.md](./docs/parsers.md) | PDF/Word/MD/Excel 解析、`ParsedDoc` IR、缓存 |
| 🔍 **Extractor 层** | [docs/extractors.md](./docs/extractors.md) | 寄存器/管脚/时序/图表抽取、LLM 兜底策略 |
| 🔗 **Linker 层** | [docs/linker.md](./docs/linker.md) | 交叉引用、实体消歧、联邦合并 |
| 🔎 **检索与 MCP** | [docs/retrieval.md](./docs/retrieval.md) | 嵌入、查询引擎、MCP 工具集、CLI、SDK |
| 🌐 **联邦机制** | [docs/federation.md](./docs/federation.md) | 多 spec 共存、SUPERSEDES、命名空间 |
| ⚡ **增量与缓存** | [docs/incremental.md](./docs/incremental.md) | watch 模式、缓存层级、删除处理 |
| 🔌 **插件系统** | [docs/plugins.md](./docs/plugins.md) | entry points、自定义 Parser/Extractor |
| ⚙️ **配置参考** | [docs/configuration.md](./docs/configuration.md) | 用户级 `~/.docgraph/config.yaml`、可选项目级 `docgraph.yaml`、可选 `.env` |
| 🛡️ **运维与安全** | [docs/operations.md](./docs/operations.md) | 日志、成本、质量评估、安全边界 |
| 🧭 **解析工具调研** | [docs/parser-tooling-research.md](./docs/parser-tooling-research.md) | Docling、MinerU、Marker、MarkItDown、PixelRAG/Visual RAG 对比与接入建议 |
| 🤝 **贡献指南** | [docs/contributing.md](./docs/contributing.md) | 治理、测试、CI、技术栈 |
| 🗺️ **路线图与决策** | [docs/roadmap.md](./docs/roadmap.md) | 当前产品基线、近期工程重点、ADR 记录 |
| 📝 **需求变更记录** | [docs/requirements-changelog.md](./docs/requirements-changelog.md) | 稳定记录需求变更、决策和影响范围 |
| 📖 **术语表** | [docs/glossary.md](./docs/glossary.md) | 缩写、项目术语、芯片术语 |

---

## 一句话定义

**DocGraph 把不可结构化的芯片 Spec 文档解析、抽取、链接为一个本地可查询的知识图谱，并通过 MCP / SDK / CLI 向 Agent 暴露稳定的语义接口。**

---

## 文档元信息

| 项 | 内容 |
|---|---|
| 项目代号 | DocGraph |
| 状态 | Active v0.4 |
| 创建日期 | 2026-06-25 |
| 最近更新 | 2026-07-03 |
| 类型 | 架构设计 |
| 适用范围 | 芯片 Spec（datasheet、reference manual、TRM、errata、app note 等） |
| 目标用户 | 芯片设计/验证工程师、Spec-driven Agent 开发者 |
| License | 计划开源 Apache 2.0 |

---

## 快速链接

- 5 分钟上手：[README.md](./README.md)
- 当前路线图：[docs/roadmap.md](./docs/roadmap.md)
- 想新增 Parser/Extractor？[docs/plugins.md](./docs/plugins.md)
- 配置文件参考：[docs/configuration.md](./docs/configuration.md)
- 需求变更记录：[docs/requirements-changelog.md](./docs/requirements-changelog.md)

---

> 本文档为活文档（living doc），随项目演进持续更新。重大架构变更走 RFC 流程，归档于 `docs/rfcs/`。
