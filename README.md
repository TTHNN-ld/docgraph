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

默认会扫描 `docs/**/*.pdf` 和 `spec/**/*.pdf`，PDF 默认走自动路由：PyMuPDF 做轻量预检和兜底，Docling 处理可复制文本质量好的 Word/tagged PDF，MinerU 处理扫描、OCR 和图片密集文档。用户级模型、embedding、VLM 和密钥配置放在 `~/.docgraph/`，项目内 `.docgraph/` 只保存生成的数据库、缓存和日志。

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

## Agent 使用模式

```
1. search_chunks("PCIe MSI-X doorbell")  → L1 发现：chunk ID + snippet + page + block_ids
2. fetch(chunk_id)                       → L0 原文：完整表格/图/文本 + 嵌入的 L2 entities
3. search("per_vector_misc")             → L2 加速：实体查（每条带 source_quality）
```

**核心原则**：L0 原文是权威，L2 实体是候选。`fetch` 返回完整原文（不截断表格），同时嵌入对应 L2 entities 及其 `source_quality.needs_source_check` 标注——agent 自己判断是否信任抽取结果，还是回到原表验证。

MCP 工具共 **7 个**（按层次）：

| 层次 | 工具 | 作用 |
|---|---|---|
| L0 原文 | `fetch` | chunk + 完整 L0 blocks + 嵌入 entities |
| L1 发现 | `search_chunks`, `section` | 关键词/语义搜 chunks + 章节树导航 |
| L2 提示 | `search` | 实体查，每条标注 `needs_source_check` |
| 图谱 | `neighbors` | 邻域关系浏览 |
| 元信息 | `status`, `files` | 图谱统计 |

## 评测结果

基于 2 份 PCIe spec（84 页）的 17 个芯片工程 case，Baseline（docling 全文）vs DocGraph MCP 对照：

| 场景 | DocGraph 表现 | 说明 |
|---|---|---|
| **寄存器/RAL 抽取** | ✅ **-29% 成本** | register/bitfield 确定性抽取，agent 直接拿到 bit range/access/reset |
| **MSI-X UVM sequence** | ≈ 持平 | L2 bitfield 实体提供精确字段值 |
| **跨文档地址转换** | +32% | 小文档集上全文检索更经济 |
| **Clock/Reset 验证** | ❌ **+219% 成本** | clock 实体仅 ~15% 覆盖，多来自 VLM |
| **STA/SDC 约束** | ❌ +96% | 同上，clock 覆盖不足 |
| **CDC/RDC sign-off** | ❌ +211% | 同上 |

**关键结论**：
- DocGraph 在**表格式信息**（register/bitfield/signal/interface）上有明确价值——确定性抽取直接提供结构化字段
- **clock/reset** 是当前最大短板——实体覆盖率 ~15%，全来自 VLM 图抽取
- 在**小文档集（~100KB）**上，全文塞进上下文窗口比 MCP 结构化检索更经济。DocGraph 的规模优势需要在 10+ 文档、1000+ 页时才能体现
- 架构契约成立：L2 缺失时 L1/L0 永远可回退

详见 [评测报告](./benchmark_runs/baseline_docgraph_compare/FULL_REPORT.md) 和 [case 设计评审](./benchmark_runs/case_design_review.py)。

## 项目状态

**Beta 可用** — L0/L1 已由 `docgraph doctor --strict` 做质量门禁；L2 已有 provenance、强结构校验、候选覆盖审计 `docgraph l2 audit` 和 golden 评估入口 `docgraph l2 eval`。

**当前重点工作**（M7 分层重构）：
- 提升 clock/reset 实体覆盖率（从接口表确定性抽取，当前 ~15%）
- 填充 register 实体的 address/offset/access/reset 属性
- 更大规模文档集评测验证规模优势

License: Apache 2.0（计划）
