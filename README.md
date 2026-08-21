# DocGraph

> 面向芯片 Spec 的文档知识图谱引擎 — 让 Spec-driven 芯片开发 Agent 真正用得上 spec。

DocGraph 把 PDF/Word/Excel/Markdown 形态的芯片 spec 文档解析为 L0 无损版面、L1 可检索索引，并按需增强为 L2 实体图谱，通过 Web / MCP / CLI 暴露稳定、可追溯的查询接口。

灵感来源：`codegraph` —— 把代码索引为图后，agent 查询的精度和成本同时大幅改善。DocGraph 把同一套心智搬到芯片文档。

---

## 安装

PyPI 发布后可直接安装（当前尚未发布）：

```bash
pip install docgraph-core
```

从源码参与开发：

```bash
git clone https://github.com/TTHNN-ld/docgraph.git
cd docgraph
python -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

`llm` extra 只安装模型 provider；调用 LLM/VLM 前还需要按
[启用 LLM 抽取](./docs/cookbook/02-enable-llm.md) 配置 provider、model 和 API key。
基础包自带 PyMuPDF。`build` 选中尚未安装的内置 parser 时，交互终端会询问是否
安装对应 extra；CI 等非交互环境不会擅自修改环境，而是自动尝试下一 parser。
首次使用可运行 `docgraph setup` 查看当前环境是否已准备好。需要提前安装推荐
parser 时，执行 `docgraph setup parsers`；需要在一次构建中明确授权补装依赖时，
使用 `docgraph build --install-missing`。

安装 parser 不等于预下载模型。Docling 在首次实际解析时由上游下载并缓存模型；
模型下载或初始化失败也会触发 parser 回退，并记录在 `.docgraph/manifest.json`。
MinerU adapter 使用 3.x 客户端。可将 PDF 编排与 L0 归一化保留在 DocGraph
所在机器，只把 VLM 推理发往独立的 vLLM/SGLang/OpenAI-compatible 模型服务；
配置方式见 [Parser 文档](docs/parsers.md#44-mineru-远程模型服务)。

---

## 5 分钟跑通

```bash
docgraph init                             # 在当前目录创建 .docgraph/
docgraph setup                            # 可选：检查 parser、LLM/VLM 和 embedding 环境
docgraph build                            # 解析 docs/**/*.pdf 和 spec/**/*.pdf，构建 L0/L1/L2
docgraph status                           # 节点/边/文档统计
docgraph doctor --strict                  # L0/L1 完整性 + L2 provenance/强结构检查
docgraph l2 audit --strict                # L2 候选覆盖与 schema 质量审计

docgraph search "per_vector_misc"         # 按名称查寄存器
docgraph search --kind clock "core"       # 按类型查 clock 实体
docgraph inspect register freeze_reg      # 查看寄存器详情 + bitfields

docgraph serve --mcp                      # 启动 MCP server，供 Claude Code 等 agent 调用
```

日常路径只需要 `init → build`。`setup` 是环境检查和准备入口，不是必经步骤；
`--install-missing`、`--strict-parsers` 和 `--quality` 保留给 CI 或质量门禁等专家场景。

---

## 三层数据架构

```
L0  Block — 原文无损镜像
     每页的段落、表格(cells)、图、公式、阅读顺序、坐标和页码完整保留。
     表格不允许丢成纯文本，图/公式保留渲染产物和原始证据。

L1  Chunk — 可寻址检索单元
     章节、表格、图各自成 chunk，带稳定 ID 和 block_ids 回溯链。
     支持 FTS5 全文检索 + 语义向量检索，按章节路径和页范围过滤。

L2  Node/Edge — 实体知识图谱（可选增强）
     寄存器、bitfield、管脚、信号、接口、中断、memory_map、时钟、复位、
     需求、时序参数等实体。每条标注抽取来源和可信度——
     deterministic = 表格确定性抽取，可信；
     vlm/llm = 模型抽取，需回到 L0 原文验证。
     L2 缺失不影响信息获取，L1/L0 永远可直达。
```

## 芯片工程场景

| 阶段 | 典型任务 | 用到什么 |
|---|---|---|
| RTL 设计 | 模块边界、接口清单、寄存器 map、地址空间 | L2 register/memory_map/interface + L0 原表兜底 |
| DV 验证 | test plan、UVM RAL 建模、coverage item | L2 register/bitfield 精确字段 + L0 寄存器表 |
| 中后端 | STA/SDC 约束、CDC/RDC 检查、floorplan 集成 | L1 时钟/复位章节定位 + L0 结构图原文 |
| Bring-up | LTSSM debug、JTAG 可测性、中断状态观测 | L2 register + L1 figure/section 联合检索 |

**当前强项**：寄存器/bitfield 确定性抽取。从表格中提取的字段（bit range、access、reset value）agent 可直接使用，无需人肉对齐原表。

**当前短板**：时钟/复位实体覆盖率偏低（~15%），主要来自框图 VLM 抽取。相关场景 agent 需更多回退到 L1/L0 读原文。

详见 [评测报告](./benchmark_runs/baseline_docgraph_compare/FULL_REPORT.md)。

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
1. context(task, mode="auto")            → 默认入口：小语料完整 L1，大语料透明检索视图
2. fetch_many(chunk_ids)                 → 批量取证：完整 L1 + 去重 L0 blocks + L2 candidates
3. search("per_vector_misc")             → L2 加速：实体查（每条带 source_quality）
```

**核心原则**：MCP 提供透明、可解释、可继续展开的文档视图，不替 agent 写答案。L0 原文是权威，L1 chunk 是主要阅读材料，L2 实体是候选和加速索引。Agent 自己决定关注什么、相信什么、是否继续取证。

MCP 工具共 **9 个**（按层次）：

| 层次 | 工具 | 作用 |
|---|---|---|
| 默认入口 | `docgraph_context` | 按语料规模返回完整 L1 或检索候选，公开覆盖范围、排序理由和游标 |
| L0 原文 | `docgraph_fetch`, `docgraph_fetch_many` | 单个或批量读取完整 chunk、L0 blocks 和相关 L2 candidates |
| L1 发现 | `docgraph_search_chunks`, `docgraph_section` | 关键词/语义搜 chunks + 章节树导航 |
| L2 提示 | `docgraph_search` | 实体查，每条标注 `needs_source_check` 和来源 |
| 图谱 | `docgraph_neighbors` | 邻域关系浏览 |
| 元信息 | `docgraph_status`, `docgraph_files` | 图谱统计和文档列表 |

## 评测结果

基于 2 份 PCIe spec（84 页）的代表性芯片工程 case，Baseline（直接读取 PDF）vs 当前 DocGraph MCP 对照：

| 场景 | DocGraph 表现 | 说明 |
|---|---|---|
| **寄存器/RAL 抽取** | ✅ 13 turns / 12 tools / 120.2s / $0.646 | Baseline 为 25 turns / 24 tools / 349.0s / $0.904；结构化表格任务收益明显 |
| **跨文档地址转换** | ✅ 9 turns / 8 tools / 196.7s / $0.719 | Baseline 为 17 turns / 14 tools / 188.5s / $0.510；DocGraph 成本略高，但证据覆盖更系统，且未突破页数预算 |
| **Clock/Reset 验证** | ✅ 26 turns / 25 tools / 243.6s / $1.173 | Baseline 480s 超时；L2 覆盖不足时仍可依赖 L1 完成 |

**关键结论**：
- DocGraph 在**表格式信息**（register/bitfield/signal/interface）上有明确价值，确定性抽取可以直接提供 bit range、access、reset 等字段。
- `docgraph_context` 让小语料直接返回完整 L1，大语料自动切到检索候选，避免把 L2 当成唯一入口。
- `docgraph_fetch_many` 能显著减少宽问题中逐条回到 L0 的工具往返。
- Clock/reset 仍是实体覆盖短板，但当前 MCP 路径已经能通过 L1/L0 完成相关任务。

详见 [自适应上下文评测报告](./benchmark_runs/adaptive_context_compare_20260714/REPORT.md) 和 [历史评测报告](./benchmark_runs/baseline_docgraph_compare/FULL_REPORT.md)。

## 项目状态

**Beta 可用** — L0/L1 已由 `docgraph doctor --strict` 做质量门禁；L2 已有 provenance、强结构校验、候选覆盖审计 `docgraph l2 audit` 和 golden 评估入口 `docgraph l2 eval`。

**当前重点工作**（M7 分层重构）：
- 提升 clock/reset 实体覆盖率（从接口表确定性抽取，当前 ~15%）
- 填充 register 实体的 address/offset/access/reset 属性
- 更大规模文档集评测验证规模优势

License: Apache 2.0
