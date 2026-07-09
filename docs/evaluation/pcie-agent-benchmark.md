# PCIe Case Agent Benchmark

> 用于评估 Agent 在接入 DocGraph 前后的效果差异。输入文档限定为：
>
> - `spec/PCIE Subsystem Spec_v3.21.pdf`
> - `spec/PCIe Subsystem TRS_r2p0.pdf`

## 评测目标

这组 case 不测试“能否泛泛总结 PDF”，而测试 Agent 在真实芯片生产流程中的工程辅助能力。任务覆盖 RTL 设计、微架构澄清、UVM/RAL 建模、test plan、test case、coverage、assertion、后端约束、物理集成、DFT/JTAG、bring-up debug 等阶段。

- 是否能快速定位 RTL/验证任务需要的章节、表格、图和实体。
- 是否能跨 `Spec` 与 `TRS` 合并需求、接口、寄存器、数据路径和约束。
- 是否能输出可直接进入评审的工程交付物，例如接口清单、寄存器模型输入、test plan、UVM sequence 草案、coverage item、assertion checklist、SDC/STA 约束 checklist、CDC/RDC checklist、physical integration checklist、DFT/JTAG checklist、debug checklist。
- 是否能给出页码、章节、图号、需求编号、寄存器/bitfield 证据。
- 是否减少全文阅读、减少漏答和幻觉。

## 前置检查：实体覆盖

DocsGraph 模式下，Agent 的 KG 查询精度取决于实体是否已被 L2 抽取覆盖。执行每个 case 的 Baseline/DocGraph 跑分之前，**应先检查 DocGraph 中目标实体是否已入库**：

| Case | 期望实体类型 | 覆盖率 | 缺失示例 | 影响 |
|---|---|---|---|---|
| 2 clock | clock | ~14% | core_clk, mstr_aclk, slv_aclk, cfg_clk | 时钟接口清单不准 |
| 2 module | module | ~70% | PCIe_top, Irq_aggregator | 模块边界清单不完整 |
| 8A signal | signal/interrupt_source | ~60% | 部分 irq_src 信号未入库 | 中断源信号清单不完整 |
| 8B bitfield | bitfield | ~70% | vf, vfactive | RAL 字段不完整 |
| 9 INT_NUM | bitfield | ~67% | vf, vfactive | RAL 字段不完整 |
| 11 clock | clock | ~25% | clk_ref_in, axi_clk, core_clk | STA/SDC 时钟源不完整 |

**规则**：若某个 case 依赖的实体类型覆盖率 < 50%，应先修复 KG 构建（调整解析/VLM prompt/alias），再跑 benchmark。否则测的是"KG 没抽到"而非"Agent 用 KG 好不好"。

> 每次 `docgraph build` 之后、跑分之前，执行 `python check_coverage.py`（或等效 SQL）输出当前各实体类型的数量和覆盖率，记录在 run log 的 `notes` 字段中。

## 生产角色与交付物

| 阶段 | 典型用户 | 评测交付物 |
|---|---|---|
| RTL 设计 | 子系统 RTL / IP 集成工程师 | 模块边界、接口清单、寄存器/状态位语义、数据路径设计约束 |
| 微架构评审 | 架构/设计 owner | 跨文档需求追踪、图到模块关系、风险点和未决问题 |
| DV test plan | 验证 owner | feature/testpoint/coverage/scoreboard/检查点 |
| DV test case | UVM test writer | sequence 步骤、寄存器配置、激励、期望结果、错误注入 |
| RAL/寄存器验证 | UVM RAL owner | register/field/access/reset/功能分类 |
| 综合/STA | 后端约束 / STA 工程师 | clock/reset 约束、generated clock、false/multicycle path 候选、异步边界 |
| P&R/物理集成 | 后端 floorplan / physical integration 工程师 | PHY/PIPE/NoC/AXI/JTAG/clock/reset 物理边界和接口风险 |
| CDC/RDC/低功耗 | CDC/RDC/low-power 工程师 | clock domain、reset domain、power/low-power 状态、隔离/复位检查点 |
| DFT/JTAG | DFT / bring-up 工程师 | JTAG decoder、isolation、level shifter、PHY test 连接、可测性检查 |
| Bring-up/debug | FPGA/仿真/软件联调 | debug 寄存器、状态位、观测点、问题定位流程 |

## 对照方式

对每个任务跑两组：

- **Baseline**：Agent 只能读取两个 PDF 文件，不允许使用 DocGraph、预生成 JSON、RDL、CSV。**必须限制页读取量**：每个 case 最多读 15 页 PDF（避免全文抽取淹没检索效率差异）。如果 agent 需要读更多页，必须在答案中明确标注"超出 page budget"并说明需要哪些额外页面。
- **DocGraph**：Agent 可以使用 DocGraph MCP 工具查询，但最终答案仍必须给出证据页或来源节点。不做调用次数限制，让 agent 自主决定检索路径。

DocGraph 的离线构建成本不计入单题 Agent 运行成本；若评估"首次导入 + 多题摊销"，单独记录并摊到任务数：

| 构建指标 | 说明 |
|---|---|
| `docgraph_build_s` | 三份 case 文档的一次 DocGraph 全量构建耗时（含 LLM/VLM） |
| `docgraph_build_llm_calls` | 构建过程 LLM 调用次数 |
| `docgraph_build_input_tokens` | 构建过程 LLM/VLM 输入 token |
| `docgraph_build_output_tokens` | 构建过程 LLM/VLM 输出 token |
| `amortized_build_s` | `docgraph_build_s / 16`（摊到 16 个 case 的单题均摊耗时） |

建议记录：

| 指标 | 说明 |
|---|---|
| correctness | 答案事实是否正确 |
| completeness | 是否覆盖题目要求的全部字段/关系 |
| evidence | 是否给出 doc/page/section/figure/table 等证据 |
| structure | 是否输出可复用的表格/JSON/步骤 |
| production_readiness | 输出是否能直接进入 RTL review、DV test plan、UVM sequence、RAL、coverage、STA/SDC、CDC/RDC、physical integration、DFT 或 debug 流程 |
| hallucination | 是否编造不存在的实体、页码、关系 |
| wall_time_s | 从任务开始到最终答案完成的总耗时，单位秒 |
| llm_calls | 任务过程中 Agent 发起的 LLM 调用次数 |
| input_tokens | 所有 LLM 调用的 prompt/input token 总量 |
| output_tokens | 所有 LLM 调用的 completion/output token 总量 |
| total_tokens | `input_tokens + output_tokens` |
| tool_calls | Agent 工具调用总次数，含 PDF 读取、搜索、DocGraph 查询等 |
| docgraph_calls | DocGraph CLI/MCP/Web API 调用次数；Baseline 固定为 0 |
| pdf_page_reads | 直接读取 PDF 页面的数量；可用于衡量是否在读全文 |
| context_blocks | 被拉入最终推理上下文的 chunk/block/page/table/figure 数量 |
| effective_queries | 工具调用（搜索/节点/邻域/block 查询）中返回非空结果的数量 |
| empty_queries | 工具调用中返回空或无关结果的数量 |
| query_precision | `effective_queries / (effective_queries + empty_queries)` |

评分建议：每题 0-5 分。3 分表示基本答对但证据或结构不完整；5 分表示答案完整、可追溯、无明显幻觉，并且输出能直接作为前端或后端生产交付物的草案。

## 运行统计要求

为了比较 DocGraph 对 Agent 成本和效率的影响，每次运行都应保存一份 run log。若使用的平台能导出 token usage，直接采用平台统计；否则至少手工记录 LLM 调用次数、耗时和工具调用次数。

推荐记录字段：

```json
{
  "case_id": "case-01",
  "mode": "baseline",
  "agent": "claude-code",
  "model": "claude-sonnet-4.6",
  "start_time": "2026-07-02T10:00:00+08:00",
  "end_time": "2026-07-02T10:03:42+08:00",
  "wall_time_s": 222.0,
  "llm_calls": 6,
  "input_tokens": 183420,
  "output_tokens": 8420,
  "total_tokens": 191840,
  "tool_calls": 18,
  "docgraph_calls": 0,
  "pdf_page_reads": 42,
  "context_blocks": 42,
  "notes": ""
}
```

DocGraph 模式额外建议记录：

```json
{
  "docgraph_calls": 9,
  "docgraph_query_types": {
    "search": 3,
    "node": 2,
    "neighbors": 1,
    "blocks": 3
  },
  "retrieved_chunks": 12,
  "retrieved_blocks": 18,
  "retrieved_figures": 3,
  "retrieved_tables": 2
}
```

建议计算派生指标：

| 指标 | 公式 | 含义 |
|---|---|---|
| token_reduction | `(baseline_total_tokens - docgraph_total_tokens) / baseline_total_tokens` | token 节省比例 |
| time_reduction | `(baseline_wall_time_s - docgraph_wall_time_s) / baseline_wall_time_s` | 端到端耗时下降比例 |
| call_reduction | `(baseline_llm_calls - docgraph_llm_calls) / baseline_llm_calls` | LLM 调用次数下降比例 |
| quality_delta | `docgraph_score - baseline_score` | 质量分提升 |
| evidence_delta | `docgraph_evidence_score - baseline_evidence_score` | 证据完整度提升 |
| production_delta | `docgraph_production_readiness - baseline_production_readiness` | 工程可用性提升 |

注意：DocGraph 本身构建图谱的离线成本不计入单题 Agent 运行成本；如果要评估“首次导入 + 多题摊销”，可单独记录 `docgraph_build_time_s`、`docgraph_build_input_tokens`、`docgraph_build_output_tokens`，并按任务数量摊销。

## 通用回答要求

每个 Agent 回答都必须包含：

- `answer`：结论。
- `engineering_artifact`：面向该任务的生产交付物，例如端口表、test plan、sequence、coverage、SDC checklist、CDC/RDC checklist、physical integration checklist、DFT checklist、debug 流程或 sign-off action item。
- `evidence`：至少包含文档名和页码；有图则给 figure caption；有实体则给实体名。
- `uncertainty`：如果无法确定，说明缺口，不允许编造。

**证据容差**：DocGraph 中的实体名可能与原文略有差异（大小写、下划线/空格、缩写）。评分时**不影响核心信息的名称归一化差异不扣分**，但实体类型必须正确。例如 `core_clk` ↔ `Core Clock` ↔ `CoreClk` 视为证据有效。

## Case 1：RTL 设计输入包：地址转换与地址空间

**Prompt**

你是 PCIe 子系统 RTL owner，准备实现/集成地址转换相关逻辑。请基于两个 spec 输出一份 RTL 设计输入包：按 `System Address Map`、`BAR`、`Inbound`、`Outbound`、`iATU/eATU`、`ATS/IOMMU` 分组列出相关章节、页码、设计含义、需要 RTL 暴露或依赖的配置/接口，以及需要向架构确认的问题。

**考察点**

- L1 section tree 与 chunk 检索。
- 能否跨两个文档返回相关章节，而不是只命中一个 PDF。
- 能否把文档内容转成 RTL 设计输入，而不是停留在摘要。

**期望证据**

- `PCIe Subsystem TRS_r2p0`：System Address Map p.16、Inbound data path p.18、BAR Space assignment p.19、Outbound data path p.20、disable IOMMU p.21、PCIE DMA p.23、enable IOMMU and Disable ATS p.25、enable IOMMU and enable ATS p.27、eATU/ATS 相关章节 p.33。
- `PCIE Subsystem Spec_v3.21`：Address Map p.20、BAR 空间 p.20、Clock/Reset/Interrupt/ReMap 附近章节按命中补充，iATU/Inbound/Outbound p.35-p.36。

**评分重点**

- 是否覆盖两个文档。
- 是否把目录页/列表页误当成正文。
- 是否能输出可用于 RTL owner 开工/评审的分组信息和待确认问题。

## Case 2：RTL 模块边界与接口清单

**Prompt**

你是 PCIe subsystem 集成工程师。请基于 `PCIE Subsystem Spec_v3.21` 生成一份 RTL module boundary/interface checklist：列出主要模块、上下游接口、clock/reset、PHY lane/参考时钟、AXI/CFG 通道，并给出每项的设计用途和证据。输出要能直接用于 RTL 顶层端口 review。

**考察点**

- Figure 语义抽取。
- 模块/接口/clock 实体召回。

**期望证据**

- `Figure 3-1 PCIe Subsystem Architecture`，p.15。
- 关键模块：PCIe subsystem、PCIe core、PCIe top、UPCS PIPE、PCIe CRG、MSI、MSIX2DBI、Irq_aggregator、PCIE_SS_CTRL、Debug。
- 关键接口：AXI master/slave、CFG AXI/AXI Lite、Clock/Reset、PERST#、PHY REF CLK、TX/RX lane。
- 关键时钟/复位：core_clk、cfg_clk、mstr_aclk、slv_aclk、pipe_rx_clk、pipe_tx_clk、cfg_rst_n、mstr_rst_n、slv_rst_n。（注：spec PDF 原文使用 mstr_aclk/slv_aclk，非 mstr_clk/slv_clk；dbi_clk 未在 PDF 中以该名称出现。）

**评分重点**

- 是否能从图中抽出实际芯片语义，而不是只写“这是一个框图”。
- 是否区分模块、接口和 clock/reset。
- 是否能形成顶层端口/模块边界 review checklist。

## Case 3：RTL 端口表与接口约束

**Prompt**

你是 RTL 设计工程师，需要从 spec 建立 PCIe subsystem 顶层端口表。请输出结构化表格：接口/信号组、方向或角色、位宽/lane 数、clock/reset 归属、用途、约束/注意事项、证据页。至少覆盖 AXI Master、AXI Slave、AXI Lite/CFG、TX/RX、PHY 参考时钟、复位/时钟接口。

**考察点**

- L0 表格/文本保真。
- L2 interface/signal 实体召回。

**期望证据**

- Interfaces 章节 p.13。
- AXI Master：标准 AXI4、512bit 数据位宽。
- AXI Slave：标准 AXI4、512bit 数据位宽。
- AXI Lite/CFG：配置接口，常见 32bit 配置语义。
- TXx_P/N：16 lane 差分串行输出。
- RXx_P/N：16 lane 差分串行输入。
- PHYx_PAD_REF_CLK_N/P、PHYx_RESREF、PERST#、cfg_clk/cfg_rst_n、mstr_aclk/mstr_rst_n、slv_aclk/slv_rst_n。

**评分重点**

- 是否输出结构化表格。
- 是否避免把普通模块名误当接口。
- 是否标注证据页。
- 是否包含 RTL 端口表需要的 direction/width/clock-reset/constraint 信息。

## Case 4：地址空间/BAR 配置验证计划

**Prompt**

你是 DV owner，要为 PCIe 地址空间、BAR 和内部寄存器空间制定 test plan。请综合两个文档输出：feature 列表、每个 feature 的配置对象/BAR/地址空间、正向测试、负向/越界测试、scoreboard/checker、coverage item、证据页。说明 TRS 的 System Address Map 与 Spec 的 Address Map/BAR 空间之间如何对应。

**考察点**

- memory_map 实体。
- 跨文档合并与对齐。

**期望证据**

- TRS：System Address Map p.16；BAR Space assignment p.19；DMA/Outbound 地址相关 p.21-p.23。
- Spec：Address Map/BAR 空间 p.20-p.21；iATU、Top CFG、DBICFG、ReMap CFG、MSIX2DBI CFG、PHY0-3 CFG、CRG CFG、BAR0/BAR1/BAR2/BAR4。
- 应说明 TRS 更偏系统级地址规划，Spec 更偏 PCIe 子系统内部寄存器/配置空间映射。

**评分重点**

- 是否跨文档而不是单文档回答。
- 是否能区分系统级 map、BAR、内部 CFG/DBI/ReMap/iATU 空间。
- 是否能产出 test plan 维度的 testpoint/coverage/checker。

## Case 5：数据路径 RTL/DV 方案：Inbound / Outbound / DMA

**Prompt**

你是设计和验证联合评审人。请比较 TRS 中 Inbound data path、Outbound without IOMMU、PCIE DMA 三类路径，输出每条路径的参与模块、数据方向、地址转换点、关键配置、应观测信号/状态、scoreboard 建议、corner case 和关键图号/页码。

**考察点**

- 图语义 + 章节检索。
- 工程化推理。

**期望证据**

- Figure 5-3 Inbound Path p.18。
- Figure 5-4 LD/ST Outbound without IOMMU p.21。
- Figure 5-5 DMA Access p.23。
- 关键模块：PCIe RC、PCIe EP/Synopsys IP、NoC、DMA、iATU/eATU、MMU/IOMMU、Host PA/Host DDR、Local Memory/I/O。

**评分重点**

- 是否分别解释三条路径。
- 是否能指出验证关注点，如地址映射、ATU 配置、IOMMU/ATS、DMA 描述符、host/local 方向。
- 是否能形成 RTL/DV 双方都可执行的观测点和 corner case。

## Case 6：IOMMU/ATS 功能验证矩阵

**Prompt**

你是 DV owner，要写 IOMMU/ATS 相关 test plan。请基于 TRS 输出一个功能验证矩阵：`IOMMU on + ATS off`、`IOMMU on + ATS on`、ATS/ATC miss/hit、translation failure、consistency/performance 关注点。每一项给出激励、预期行为、检查点、coverage 和证据图/章节。

**考察点**

- 相邻图与章节的对比。
- ATS/IOMMU 概念关系。

**期望证据**

- Figure 5-6 Enable IOMMU & No ATS p.25。
- Figure 5-7 Enable IOMMU & Enable ATS p.27。
- ATS(Address Translation Services) 章节 p.33，ATS Performance p.34，Consistency p.35。
- 关键实体：IOMMU、ATS/ATC、eATU/iATU、PCIe EP、Host PA。

**评分重点**

- 是否能明确 No ATS 与 Enable ATS 的转换路径差异。
- 是否能结合性能/一致性章节，而不仅复述图标题。
- 是否能输出可落地的 test matrix。

## Case 7：MSI/MSI-X/Legacy Interrupt Test Plan

**Prompt**

你是 PCIe 中断验证 owner。请综合 Spec 和 TRS 生成 MSI/MSI-X/Legacy Interrupt test plan：feature、需求编号、寄存器/配置对象、test scenario、预期结果、checker/scoreboard、coverage item、异常场景。必须覆盖 Function/Error interrupt、MSI、MSI-X、Legacy Interrupt、vector 数量、MSI-X Table/PBA SRAM、Host/SoC CPU 路由约束。

**考察点**

- interrupt 实体。
- requirement 列表和 spec 章节跨文档对齐。

**期望证据**

- Spec：Function and Error Interrupt p.24；MSI-X / Message Interrupt p.26-p.27；中断控制框图 p.27-p.28。
- TRS：MSI/MSI-X p.34；REQ_PCIE_TRS_450 至 REQ_PCIE_TRS_467。
- 关键事实：MSI 最多 32 vectors；MSI-X 最多 2048 vectors；MSI-X Table/PBA Table 放在 PCIE SS SRAM，约 32KB；通过 REG BAR 配置；SoC CPU 触发 MSI/MSI-X 的寄存器不应被 Host 访问；同一中断不应同时 route 到不同 CPU。

**评分重点**

- 是否把需求编号带上。
- 是否同时覆盖 Spec 的实现/接口侧和 TRS 的需求侧。
- 是否能生成验证团队可执行的 scenario/checker/coverage。

## Case 8A：Interrupt Source Signal 建模（irq_src 表）

**Prompt**

你是验证工程师。请基于 spec 为 `irq_src` 中断源信号表 (p.25) 生成一份结构化的中断源信号清单：signal name、位宽、功能描述、按功能分类（hot reset / PLL / LTSSM / refclk / completion timeout / PHY / 其他）。说明此表的结构特点（只有信号名+位宽+描述，无 offset/access/reset 字段），并与真正的 register/bitfield 表（如 p.27 的 per_vector_misc）做区分。

**考察点**

- Agent 是否能识别表格结构：此表是 interrupt source signal 表，不是 register 表。
- 是否能正确区分"信号名+位宽+描述"型表格与"寄存器字段+access+reset"型表格。
- 是否避免将 signal name 误当成 bitfield。

**期望证据**

- `irq_src` 表，spec p.25，约 20+ 个中断源信号。
- hot reset 相关：hot_reset_int (bit16)、link_req_rst_not_deassert_int (bit14)、link_req_rst_not_assert_int (bit13)、perst_int (bit3)。
- PLL/PHY 相关：pll_lost_lock_int (bit15)、phy_pll_unlock_int (bit10)、phy_pll_lock_int (bit9)、phy_reset_int (bit11)、phy_lane_rst_int (bit12)。
- LTSSM 相关：ltssm_into_gen5_int (bit6)。
- refclk 相关：ref_clk_req_deassert_int (bit5)、ref_clk_req_assert_int (bit4)。
- completion timeout 相关：trgt_cpl_timeout (bit1)、link_down_event_int (bit2)。

**评分重点**

- 是否正确识别"这不是 register 表"（关键判断）。
- 是否按功能分类信号。
- 是否标注"无 offset/access/reset，无法直接作为 RAL 输入"。
- 将"正确报告此表缺少寄存器字段"视为有效行为，不扣分。

## Case 8B：UVM RAL 输入：PCIe 子系统寄存器字段

**Prompt**

你是 UVM RAL owner。请基于 spec 找到真正包含寄存器字段定义（offset/access/reset/bit-range）的表格，为其中的寄存器生成 RAL 建模输入：register name、field 名、bit range、access、reset value、description、功能分类。指出哪些表是真正的 register table（有 offset/access/reset），哪些不是。建议 mirror/predict/check 策略。

**考察点**

- Agent 是否能定位到真正包含寄存器字段信息的页面（如 p.27 的 per_vector_misc）。
- 是否区分真正的寄存器表与中断源信号表 (p.25 irq_src)。
- L2 register/bitfield 实体的结构化收益。

**期望证据**

- p.27 `per_vector_misc` / MSI-X doorbell 相关字段（这些才是真正带 access/reset/bit-range 的寄存器字段）。
- `axis_awaddr[31:0]` RW reset `0x1000948`。
- `mask_bit[20]` RW reset `0x1`。
- `priority[19:17]` RW reset `0x1`。
- `pf[16:12]` RW reset `0x0`。
- `vf[11:4]` RW reset `0x0`。
- `vfactive[3]` RW reset `0x0`。
- `tc[2:0]` RW reset `0x0`。
- 其他 spec 中带完整 access/reset 字段的寄存器表。

**评分重点**

- bit 号和 access/reset 不能错。
- 是否明确指出 p.25 irq_src 表不是 register table（与 Case 8A 对应）。
- 需要按功能归类。
- 是否包含 RAL 建模和寄存器检查策略。

## Case 9：UVM Sequence：MSI-X doorbell programming

**Prompt**

你是 UVM test writer。请基于 `INT_NUM` / MSI-X doorbell 相关寄存器字段设计一个 UVM sequence 草案：配置步骤、每个 field 的编程值/合法范围、reset 检查、doorbell 触发、预期 AXI/MSI-X 行为、错误注入点和 coverage。说明 `tc` 字段控制什么，`pf/vf/vfactive` 分别表示什么。

**考察点**

- 寄存器字段、reset、access 的准确抽取。

**期望证据**

- `INT_NUM` register，p.27。
- `axi_awaddr[31:0]` RW reset `0x1000948`：AXI master AW channel address。
- `mask_bit[20]` RW reset `0x1`：per-vector mask。
- `priority[19:17]` RW reset `0x1`：per-vector QoS。
- `pf[16:12]` RW reset `0x0`：physical function。
- `vf[11:4]` RW reset `0x0`：virtual function。
- `vfactive[3]` RW reset `0x0`：virtual function active。
- `tc[2:0]` RW reset `0x0`：MSIX Doorbell Traffic Class。

**评分重点**

- reset 值、位宽、字段解释必须准确。
- 能否直接输出可用于 UVM/RDL 的结构化表格。
- 是否能从字段语义推导 sequence 步骤、检查点和 coverage。

## Case 10：Bring-up/debug：LTSSM State debug

**Prompt**

你是 bring-up/debug 工程师。请基于 Spec 生成 LTSSM State debug 调试流程：需要配置或读取的寄存器/字段、APB 访问路径、图中主要模块、状态捕获/冻结/读取流程、异常定位步骤，以及可写成 assertion/coverage 的检查点。

**考察点**

- 图语义 + register bitfield + section 回溯。

**期望证据**

- LTSSM State debug p.35。
- Figure `figure_p35`（即 spec p.35 LTSSM State debug 框图）。
- `ltssm_state_reg`：`ltssm_state_vld[6]` RO reset `0x0`，`ltssm_state[5:0]` RO reset `0x0`。
- 图中模块：pcie controller、shift_reg、freeze_reg、state_reg、DMUX、sync_pulse、APB。

**评分重点**

- 是否能把图模块和寄存器字段连接起来。
- 是否能说明 freeze/capture/read 这类调试流程。
- 是否能产出 bring-up 可执行步骤和 DV 检查点。

## Case 11：Clock/Reset Verification Plan

**Prompt**

你是 clock/reset 验证 owner。请基于 Spec 生成 PCIe 子系统 clock/reset verification plan：clock source、PLL/CRG/GFM/DIV/MUX 作用、reset source、冷/暖/热复位场景、复位释放顺序/依赖、应监控信号、assertion/checker、coverage item 和证据页/图号。

**考察点**

- clock/reset 图和接口信息整合。

**期望证据**

- Spec 时钟结构 p.21，Figure 4-1 PCIe 子系统时钟结构。
- Spec 复位结构 p.23，Figure 4-2 PCIe 子系统 reset 结构。
- 关键时钟：clk_ref_in、local_phy_ref_clk、axi_clk、core_clk/core_clk_ug、cfg_clk、pipe_rx_clk/pipe_tx_clk、aux_clk。（注：dbi_clk 未在 PDF 中以该名称独立出现。）
- 关键模块：CRG、PLL、GFM、DIV、MUX、PHY。
- 关键复位：cfg_rst_n、mstr_rst_n、slv_rst_n、pwr_rst_n、crg_reset_n_out、perst_sync_clk、pll_rst_n。

**评分重点**

- 是否区分 clock 与 reset。
- 是否能描述模块作用，而不是只列信号。
- 是否能转化为 reset/clock verification plan。

## Case 12：设计评审任务

**Prompt**

假设你是 PCIe 子系统 tape-in 前设计/验证联合评审人。基于这两个 spec，请列出 10 个 sign-off 前必须确认的问题，并把每个问题映射到相关章节、图、寄存器或需求编号。每个问题都要说明 owner（RTL/DV/FW/架构）、风险、推荐验证方法和完成标准。问题应覆盖地址映射、IOMMU/ATS、中断、DMA、时钟复位、LTSSM debug、PHY/JTAG。

**考察点**

- Agent 是否能从 DocGraph 召回多类实体并组织成工程评审清单。
- 是否能跨文档建立检查点，而不是泛泛列 checklist。

**期望覆盖**

- 地址映射/BAR/iATU/eATU。
- Inbound/Outbound/DMA 路径。
- IOMMU/ATS/ATC/Consistency。
- MSI/MSI-X/Legacy interrupt 和 vector/route 约束。
- `USP` interrupt/status bits。
- `INT_NUM` MSI-X doorbell fields。
- Clock/Reset/Hot reset。
- LTSSM State debug。
- PHY reference clock / PHY0-3。
- JTAG decoder / isolation / level shifter / gating。

**评分重点**

- 每个问题都必须带证据。
- 问题要可执行，例如“确认 MSI-X Table/PBA SRAM 32KB 是否满足 2048 vectors”，而不是“检查中断”。
- 是否符合 tape-in/sign-off 评审语境，能直接转成 action item。

## Case 13：STA/SDC 约束输入：PCIe clock/reset

**Prompt**

你是 STA/约束工程师。请基于两个 spec 生成 PCIe 子系统的 SDC/STA 约束输入 checklist：clock source、clock domain、generated clock 候选、PLL/CRG/GFM/DIV/MUX 关系、reset source、异步/同步 reset 边界、PERST#/hot reset 相关路径、需要架构或 RTL owner 确认的 false path / multicycle path / clock group 候选。必须给出证据页/图号。

**考察点**

- 从 spec 中抽取后端约束所需 clock/reset 语义。
- 能否区分“可直接约束的信息”和“必须向 RTL/架构确认的信息”。

**期望证据**

- Spec 时钟结构 p.21，Figure 4-1 PCIe 子系统时钟结构。
- Spec 复位结构 p.23，Figure 4-2 PCIe 子系统 reset 结构。
- 关键时钟：clk_ref_in、local_phy_ref_clk、axi_clk、core_clk/core_clk_ug、cfg_clk、pipe_rx_clk/pipe_tx_clk、aux_clk。（注：dbi_clk 未在 PDF 中以该名称独立出现。）
- 关键模块：CRG、PLL、GFM、DIV、MUX、PHY。
- 关键复位：cfg_rst_n、mstr_rst_n、slv_rst_n、pwr_rst_n、crg_reset_n_out、perst_sync_clk、pll_rst_n。

**评分重点**

- 是否把 spec 事实转成 STA/SDC checklist。
- 是否避免编造具体 SDC 命令或频率；不确定时应列为待确认。
- 是否包含 clock group、reset release、generated clock、async crossing 等后端关注点。

## Case 14：CDC/RDC 与 reset sign-off checklist

**Prompt**

你是 CDC/RDC sign-off 工程师。请基于 spec 输出 PCIe 子系统 CDC/RDC 检查计划：clock domain 列表、reset domain 列表、可能的跨域路径、PERST#/hot reset/warm reset/cold reset 相关风险、应加 synchronizer 或 reset bridge 的边界、需要在仿真/形式/静态 CDC 工具中检查的规则，并给出证据页。

**考察点**

- clock/reset 实体到 CDC/RDC 检查项的转换。
- 后端/前端交界处的 reset 风险识别。

**期望证据**

- Interfaces p.13 中 cfg_clk/cfg_rst_n、mstr_aclk/mstr_rst_n、slv_aclk/slv_rst_n、aux_clk、PERST#。
- 时钟结构 p.21。
- 复位结构 p.23。
- Hot Reset 相关章节 p.23-p.24。

**评分重点**

- 是否输出可执行的 CDC/RDC rule/checklist。
- 是否明确哪些 domain 来自 spec，哪些 crossing 需要 RTL netlist 确认。
- 是否覆盖 reset assertion/deassertion、warm/hot reset、PERST#。

## Case 15：P&R / floorplan / PHY integration checklist

**Prompt**

你是 physical integration owner。请基于两个 spec 生成 PCIe 子系统 P&R/floorplan 集成 checklist：PCIe core、UPCS PIPE、PHY0-3、CRG、NoC/AXI、JTAG、参考时钟、TX/RX lane、resref、reset/clock 入口的物理边界和集成风险。输出每项的 owner、需要约束或 floorplan 关注点、证据页/图号、待确认问题。

**考察点**

- 从架构图、接口表、PHY/JTAG 图中提取后端集成关注点。
- 能否形成物理集成 checklist，而不是只列模块名。

**期望证据**

- Figure 3-1 PCIe Subsystem Architecture p.15。
- Interfaces p.13，TXx_P/N、RXx_P/N、PHYx_PAD_REF_CLK_N/P、PHYx_RESREF、PERST#。
- PHY 重要功能点 p.37，Figure PHY 参考时钟连接框图。
- JTAG decoder 结构框图 p.38。

**评分重点**

- 是否覆盖 PHY0-3、lane、reference clock、resref、JTAG、CRG、AXI/NoC 边界。
- 是否能指出 physical owner 需要确认的约束/placement/routing/test access 风险。
- 是否避免给出 spec 中没有的版图尺寸、具体坐标或时序数值。

## Case 16：DFT/JTAG 与 bring-up 可测性计划

**Prompt**

你是 DFT/bring-up 工程师。请基于 Spec 生成 PCIe PHY/JTAG/Debug 可测性计划：JTAG decoder 结构、gating/level shifter/isolation、PHY0-3 测试连接、LTSSM debug、interrupt/status 观测点、bring-up 阶段需要的软件读写或寄存器检查、coverage/checker，以及证据页/图号。

**考察点**

- DFT/JTAG 图、debug register、interrupt/status register 的跨实体整合。
- 能否输出 bring-up 和 DFT 都能用的可测性计划。

**期望证据**

- Figure JTAG decoder 结构框图 p.38。
- Figure PHY 参考时钟连接框图 p.37。
- LTSSM State debug p.35，`ltssm_state_reg` 字段。
- `USP` interrupt/status bitfield p.25。

**评分重点**

- 是否把 JTAG/PHY/debug/status 观测点串起来。
- 是否输出可执行的 bring-up/DFT 检查步骤。
- 是否明确证据来源和不确定项。

## Case 17：Agent 自主发现 KG 缺失实体

**Prompt**

你是芯片 spec 审阅人。DocGraph 已构建了两份 PCIe spec 的知识图谱。
请检查 KG 中 `clock` 和 `register` 两类实体的完整性：找出 KG 中缺失或属性不完整、但在 spec 原文中存在的实体，说明你是如何追溯到原文、发现了什么 KG 没覆盖到的信息，并给出修复建议（缺失实体应加入 KG、缺失属性应补充）。

**考察点**

- Agent 是否能绕过 L2 缺陷直达 L1/L0 原文（架构契约 §2：L2 缺失时，L1/L0 仍然能回答问题）。
- Agent 是否能自主发现 KG 的覆盖盲区，而非只消费已有图谱。
- 是否能将发现转化为具体修复建议。

**期望证据**

- 列出 KG 中 clock 节点（21 个），对照 spec 接口表/时钟结构图 (Figure 4-1, p.21) 发现缺失（例如 `core_clk`、`mstr_aclk` 在原文中出现但未入库；注意 PDF 原文使用 mstr_aclk 而非 mstr_clk）。
- 列出 register 节点中 offset 为空的，对照 spec 寄存器表发现可以补的值。
- 每个发现都给出原文页码/图表号作为证据。

**评分重点**

- 是否真的读了原文而不是只看 KG 说"够了"。
- 是否能区分"KG 没抽到"vs"原文就没有"。
- 是否能给出可操作的修复建议（具体到哪个 parser/extractor，需补什么 prompt/logic）。

## 推荐总分表

| Case | 权重 | DocGraph 预期提升点 |
|---|---:|---|
| 1 RTL 地址转换输入包 | 1.2 | L1 section/chunk + memory_map 精准定位 |
| 2 RTL 模块边界 | 1.2 | Figure → module/interface/clock 语义 |
| 3 RTL 端口表 | 1.3 | L0 表格 + L2 interface/signal |
| 4 地址/BAR test plan | 1.4 | memory_map + 跨文档对齐 |
| 5 数据路径 RTL/DV 方案 | 1.4 | 多图对比 + 模块关系 |
| 6 IOMMU/ATS 验证矩阵 | 1.4 | 相邻章节/图对比 |
| 7 MSI/MSI-X test plan | 1.6 | interrupt + requirement 跨文档合并 |
| 8A irq_src 中断源建模 | 1.4 | 表格结构识别：signal vs register |
| 8B PCIe 寄存器 RAL 输入 | 1.6 | register/bitfield 结构化准确性 |
| 9 MSI-X UVM sequence | 1.6 | register field/reset/access 到 sequence |
| 10 LTSSM debug 流程 | 1.3 | 图 + 寄存器联合解释 |
| 11 Clock/reset 验证计划 | 1.3 | clock/reset 图谱召回 |
| 12 Tape-in 评审清单 | 1.6 | 多实体综合推理 |
| 13 STA/SDC 约束输入 | 1.4 | clock/reset 图谱到后端约束 checklist |
| 14 CDC/RDC sign-off | 1.4 | clock/reset domain 与 reset 风险召回 |
| 15 P&R/PHY 集成 | 1.4 | 架构图 + 接口 + PHY/JTAG 物理边界 |
| 16 DFT/JTAG bring-up | 1.4 | JTAG/PHY/debug/register 跨实体整合 |
| 17 KG 缺失发现 | 1.0 | Agent 自主发现 L2 盲区并回到 L1/L0 原文修复 |

## 对比报告模板

```markdown
### 质量对比

| Case | Baseline 分 | DocGraph 分 | 质量提升 | 证据提升 | 工程可用性提升 | Baseline 主要问题 | DocGraph 主要问题 |
|---|---:|---:|---:|---:|---:|---|---|
| 1 | | | | | | | |
| 2 | | | | | | | |
...

### 成本与效率对比

| Case | Mode | Wall time(s) | LLM calls | Input tokens | Output tokens | Total tokens | Tool calls | DocGraph calls | PDF pages read |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | Baseline | | | | | | | 0 | |
| 1 | DocGraph | | | | | | | | |
| 2 | Baseline | | | | | | | 0 | |
| 2 | DocGraph | | | | | | | | |

### 汇总

| Metric | Baseline | DocGraph | Delta |
|---|---:|---:|---:|
| 平均质量分 | | | |
| 平均 evidence 分 | | | |
| 平均 production readiness 分 | | | |
| 总 wall time(s) | | | |
| 总 LLM calls | | | |
| 总 input tokens | | | |
| 总 output tokens | | | |
| 总 tokens | | | |
| 总 tool calls | | | |
| 总 PDF pages read | | | |
| DocGraph 构建耗时(s) | — | | — |
| DocGraph 构建 LLM calls | — | | — |
| DocGraph 构建 total tokens | — | | — |
| 构建单题均摊耗时(s) | — | | — |
| DocGraph 查询精度 | — | | — |

结论：
- 准确率提升：
- 证据完整度提升：
- token 节省比例：
- 平均耗时变化：
- LLM 调用次数变化：
- PDF 读取页数变化：
- 最明显收益：
- DocGraph 当前短板：
```
