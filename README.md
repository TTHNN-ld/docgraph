# DocGraph

> 面向芯片 Spec 的文档知识图谱引擎 — 让 Spec-driven 芯片开发 Agent 真正用得上 spec。

DocGraph 把 PDF/Word/Excel/Markdown 形态的芯片 spec 文档解析为 L0 无损版面、L1 可检索索引，并按需增强为 L2 实体图谱，通过 Web / MCP / CLI 暴露稳定、可追溯的查询接口。

灵感来源：[`codegraph`](https://github.com/) —— 把代码索引为图后，agent 查询的精度和成本同时大幅改善。DocGraph 把同一套心智搬到芯片文档。

---

## 5 分钟上手

```bash
pip install 'docgraph[web]'              # 装核心 + Web UI

cd my-chip-spec/                         # 项目目录，下面有 docs/*.pdf
docgraph init                            # 初始化 .docgraph/；普通项目不需要配置文件
docgraph build                           # 全量构建图谱
docgraph status                          # 看看建了什么
docgraph doctor --strict                 # 检查 L0/L1/L2 provenance
docgraph l2 audit                        # 检查 L2 候选覆盖与 schema 命中
docgraph l2 eval --golden examples/golden # 对人工标注集算 precision/recall

docgraph inspect register PWM_CTRL       # 直接查寄存器
docgraph search "PLL 复位流程"           # 自然语言

docgraph serve --web                     # 启动 Web UI（http://127.0.0.1:8000）
docgraph serve --mcp                     # 启动 MCP server，对接 Claude Code
```

默认会扫描 `docs/**/*.pdf` 和 `spec/**/*.pdf`，PDF 默认走轻量 PyMuPDF，适合开箱即用。复杂芯片 PDF 可以在可选的项目级 `docgraph.yaml` 中切到 MinerU；用户级模型、embedding、VLM 和密钥配置放在 `~/.docgraph/`，项目内 `.docgraph/` 只保存生成的数据库、缓存和日志。

---

## 它能做什么

- 将文档解析为 **L0 blocks**：标题、段落、表格 cells、图、坐标和页码都可回溯
- 构建 **L1 chunks**：章节、表格、图独立成可检索单元，支持 FTS + 语义索引
- 按需把寄存器、管脚、时序参数、信号、接口、章节、图表抽成 **L2 结构化节点**
- 覆盖芯片前端与后端 spec：RTL/验证接口文档、SDC/STA 约束、floorplan/placement/routing/power-grid 约束都走同一套 L0/L1 底座
- 建立**跨章节、跨文档的引用边**（"see Section 5.3"、"PLL_CFG controls SYSCLK"）
- 支持 **datasheet + reference manual + errata 联邦**（errata 自动覆盖原条目）
- 支持 **页级质量评估 + VLM 兜底**：扫描页 / 表格密集页 / 图重页自动渲染为 PNG，供 VLM 抽取
- 把时序图/框图喂给 VLM 输出 **Mermaid / WaveJSON / PlantUML**
- 通过 **MCP 协议** 让 Agent 拿到精确而非模糊的上下文
- **本地优先**：所有数据在 `.docgraph/`，离线可用

---

## 文档

- 顶层架构：[DESIGN.md](./DESIGN.md)
- 详细话题文档：[docs/](./docs/)
  - [架构总览](./docs/architecture.md)
  - [数据模型](./docs/data-model.md)
  - [Parser 层](./docs/parsers.md)
  - [Extractor 层](./docs/extractors.md)
  - [Linker 层](./docs/linker.md)
  - [检索与 MCP](./docs/retrieval.md)
  - [联邦机制](./docs/federation.md)
  - [增量与缓存](./docs/incremental.md)
  - [插件系统](./docs/plugins.md)
  - [配置参考](./docs/configuration.md)
  - [运维与安全](./docs/operations.md)
  - [贡献指南](./docs/contributing.md)
  - [路线图](./docs/roadmap.md)
  - [需求变更记录](./docs/requirements-changelog.md)
  - [术语表](./docs/glossary.md)

---

## 项目状态

**Beta 可用** — L0/L1 已由 `docgraph doctor --strict` 做质量门禁；L2 已有 provenance、强结构校验、候选覆盖审计 `docgraph l2 audit` 和 golden 评估入口 `docgraph l2 eval`。生产导入前应基于目标文档集建立 golden set，并校准 L2 schema 与模型配置。

License: Apache 2.0（计划）
