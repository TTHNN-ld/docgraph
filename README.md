# DocGraph

> 面向芯片 Spec 的文档知识图谱引擎 — 让 Spec-driven 芯片开发 Agent 真正用得上 spec。

DocGraph 把 PDF/Word/Excel/Markdown 形态的芯片 spec 文档解析为 L0 无损版面、L1 可检索索引，并按需增强为 L2 实体图谱，通过 Web / MCP / CLI 暴露稳定、可追溯的查询接口。

灵感来源：`codegraph` —— 把代码索引为图后，agent 查询的精度和成本同时大幅改善。DocGraph 把同一套心智搬到芯片文档。

---

## 安装

PyPI 发布后可直接安装（当前尚未发布）：

```bash
pip install docgraph
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
可用 `docgraph build --install-missing` 明确授权安装，或提前执行
`docgraph setup parsers` 安装推荐的 Docling 和 Office/Markdown 依赖。

安装 parser 不等于预下载模型。Docling 在首次实际解析时由上游下载并缓存模型；
模型下载或初始化失败也会触发 parser 回退，并记录在 `.docgraph/manifest.json`。
当前 MinerU 接口仍是兼容旧版 `magic-pdf` 的 adapter，需显式执行
`docgraph setup parsers --parser mineru`；在升级到 MinerU 当前 API 前不作为推荐安装项。

---

## 5 分钟跑通

```bash
docgraph init                             # 在当前目录创建 .docgraph/
docgraph build                            # 解析 docs/**/*.pdf 和 spec/**/*.pdf，构建 L0/L1/L2
docgraph build --install-missing          # 非交互环境中允许补装缺失 parser
docgraph build --strict-parsers           # parser 缺失/失败时直接报错，不降级
docgraph status                           # 节点/边/文档统计
docgraph doctor --strict                  # L0/L1 完整性 + L2 provenance/强结构检查
docgraph l2 audit --strict                # L2 候选覆盖与 schema 质量审计

docgraph search "per_vector_misc"         # 按名称查寄存器
docgraph search --kind clock "core"       # 按类型查 clock 实体
docgraph inspect register freeze_reg      # 查看寄存器详情 + bitfields

docgraph serve --mcp                      # 启动 MCP server，供 Claude Code 等 agent 调用
```

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

License: Apache 2.0
