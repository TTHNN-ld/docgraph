# Claude Code Baseline 与自适应 DocGraph 对照

日期：2026-07-14

## 运行条件

- Claude Code 2.1.104
- 模型：Sonnet，effort=medium
- 单次成本上限：$1.50
- 单次时间上限：480 秒
- 输入文档：`PCIE Subsystem Spec_v3.21.pdf`、`PCIe Subsystem TRS_r2p0.pdf`
- Baseline：仅允许 Read/Bash 读取 PDF，最多 15 页
- Current：限定相同两个文档，默认从 `docgraph_context(mode="auto")` 进入
- 两组使用同一 case prompt；历史答案和评分说明均禁止读取

历史报告使用的模型和当前运行不同，本文只比较本轮成对运行，不把旧成本直接混入统计。

## 结果

| Case | 模式 | 状态 | Turns | Tool calls | 时间 | 成本 |
|---|---|---|---:|---:|---:|---:|
| case1 跨文档地址转换 | Baseline | 完成 | 17 | 14 | 188.5s | $0.510 |
| case1 跨文档地址转换 | Current | 完成 | 9 | 8 | 196.7s | $0.719 |
| case8b 寄存器 RAL | Baseline | 完成 | 25 | 24 | 349.0s | $0.904 |
| case8b 寄存器 RAL | Current | 完成 | 13 | 12 | 120.2s | $0.646 |
| case11 Clock/Reset | Baseline | 480s 超时 | — | — | >480s | — |
| case11 Clock/Reset | Current | 完成 | 26 | 25 | 243.6s | $1.173 |

只统计两边都完成的 case1 和 case8b：

| 指标 | Baseline | Current | 变化 |
|---|---:|---:|---:|
| Turns | 42 | 22 | -47.6% |
| Tool calls | 38 | 20 | -47.4% |
| 时间 | 537.5s | 316.9s | -41.0% |
| 成本 | $1.414 | $1.365 | -3.4% |

## 输出质量

### case8b

两边都正确给出了 `per_vector_misc`、`axi_awaddr`、`cfg_dbg_sel_*`、`freeze_reg` 和 `ltssm_state_reg` 的字段、bit range、access 和 reset，并明确指出 irq_src 表不是寄存器表。

Current 额外识别了 TRS p37 的 `AMBA_ORDERING_CTRL_OFF` 字段表，并明确说明所有表都缺少可直接用于 RAL 的绝对 offset。主要字段与证据页均正确。该题中 Current 在完整性不下降的情况下，将 turns、工具调用、时间和成本全部降低。

### case1

两边都覆盖了 System Address Map、BAR、Inbound、Outbound、iATU/eATU、ATS/IOMMU，并同时引用两个文档。Current 给出的证据表和规格冲突清单更完整，包括 BAR 数量、outbound region 数量、iATU 最小粒度以及 oATU/eATU 命名关系。

Baseline 直接读取了 19 页，超过 15 页限制。Current 没有突破 PDF 页数限制，并通过 2 次 `docgraph_fetch_many` 批量核对 L0 证据，避免了逐条 `docgraph_fetch`。复跑后该题 turns 从 17 降到 9，工具调用从 16 降到 8，成本从 $0.983 降到 $0.719；相对 baseline，成本仍高 41.0%，但时间只高 4.4%，且证据覆盖更系统。

### case11

Current 成功形成 clock/reset verification plan，覆盖 clock source、PLL/CRG/GFM/DIV/MUX、reset 场景、监控信号、assertion 和 coverage，并在 uncertainty 中明确指出 Figure 4-1 的完整拓扑仍需图像证据。Baseline 在相同 480 秒限制内没有完成，因此本题只能确认 Current 的完成能力，不能计算成对成本差。

## 评测中发现并修复的问题

1. 长自然语言 task 被原样交给 FTS，所有词按 AND 处理，case8b 首次返回 0 候选。现已改为关键词探针合并，并按 term/header/table-header overlap 重排。
2. 检索结果按 40k 字符填满时返回 51 个 chunks，Claude Code 会把结果转存临时文件，随后产生多次 Read/Bash。现已限制检索视图每页最多 20 个 chunk，其余通过游标继续读取；完整 L1 模式不受影响。
3. 搜索游标原先要求续读时重复 task。Agent 自然地只提交 cursor，导致续读失败并重新搜索。游标现已保存原 task。
4. `docgraph_search_chunks` 原先没有暴露 `doc_ids`，从 context 下钻后可能重新搜索到范围外文档。MCP schema 和 handler 已补充文档范围。
5. 返回策略已明确：context 中的 chunk 是完整 L1，只有核对表格 cells、图片、版面或来源时才调用 fetch。
6. 宽问题会对多个 L1 命中逐条回到 L0 核对，造成工具往返和重复证据。现新增 `docgraph_fetch_many`，一次返回多个 chunk 的完整 L1、去重后的 L0 blocks 和相关 L2 候选，并在 MCP 工具说明和评测 runner 中引导批量取证。

## 结论

自适应入口已经解决了三个原始痛点：寄存器等结构化任务的工具往返显著下降；clock/reset 这类 L2 覆盖不足的任务可以直接依赖 L1 完成，不再完全受实体覆盖率限制；跨文档宽问题可以通过批量 L0 取证减少逐条回钻。

两边都完成的 case1 和 case8b 合计看，Current 已经把 turns、工具调用、时间和成本全部压低。后续重点不应扩大单次 context 返回量，而应继续优化批量证据包的粒度、跨页表格合并和查询改写提示，让 Agent 更少做重复检索。
