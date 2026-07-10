# DocGraph vs Baseline 对照评测报告

**日期**: 2026-07-10  
**KG**: 1123 nodes, 705 edges, 3 文档  
**MCP**: 重构后 7 工具 (search_chunks, fetch, search, section, neighbors, status, files)

---

## 1. 评测方法

| 模式 | 条件 |
|---|---|
| **Baseline** | docling 预提取文本文件 (105KB 双文档)，无 DocGraph MCP，最多读 15-25 页 |
| **DocGraph** | 挂载 docgraph MCP (7 工具)，推荐路径 search_chunks→fetch→search |

每个 case 跑 Claude Code headless (`--print --output-format json`)。

---

## 2. 对照结果

### Case 1: 跨文档地址转换 (DocGraph HIGH)

| 指标 | Baseline | DocGraph |
|---|---|---|
| Turns | 32 | **30** |
| API 时间 | **166s** | 182s |
| 输入 token | **86K** | 203K |
| 输出 token | 13.5K | **16.5K** |
| 成本 | **$1.20** | $1.58 |
| 质量 | ✅ 双文档，12 待确认 | ✅ 双文档，显式 BAR 冲突(3 vs 4) |

> 跨文档精度略优，但文本文件方案更经济。DocGraph 的 block/chunk ID 证据在工程评审中更有说服力。

---

### Case 8A: irq_src 中断源信号建模 (DocGraph HIGH)

| 指标 | Baseline | DocGraph |
|---|---|---|
| Turns | **7** | 11 |
| API 时间 | 55s | **52s** |
| 输入 token | **46K** | 114K |
| 成本 | **$0.445** | $0.779 |
| 质量 | ✅ 48 信号，正确判为非 register | ✅ 48 信号，正确判为非 register |

> 单页信号表查找，纯文本更快更便宜。但两方都正确识别了"不是 register 表"——新 Prompt 设计起效。

---

### Case 8B: 寄存器 RAL 输入 (DocGraph HIGH)

| 指标 | Baseline | DocGraph |
|---|---|---|
| Turns | 22 | **17** |
| API 时间 | 119s | **98s** |
| 成本 | $0.855 | **$0.603** |
| 输出 | 9.1K | 7.2K |
| 质量 | ✅ 区分 3 张寄存器表 vs 7 张信号表 | ✅ 区分 + L2 entity quality 审计 |

> **DocGraph 首次在成本上胜出**。L2 register/bitfield 实体直接提供结构化字段，agent 不需要从原文中人工对齐 bit range。

---

### Case 9: MSI-X UVM Sequence (DocGraph HIGH)

| 指标 | Baseline | DocGraph |
|---|---|---|
| Turns | 12 | **9** |
| API 时间 | 71s | **51s** |
| 成本 | $0.584 | $0.601 |
| 质量 | ✅ 6 field 精确，7 错误注入 | ✅ 6 field 精确 + SystemVerilog code |

> 成本接近，但 DocGraph 提供更结构化的证据（block/chunk ID）和直接可用的 UVM 代码。

---

### Case 11: Clock/Reset 验证计划 (DocGraph LOW)

| 指标 | Baseline | DocGraph |
|---|---|---|
| Turns | **10** | 40 |
| API 时间 | **164s** | 256s |
| 成本 | **$0.811** | $2.59 |
| 质量 | ✅ 13 SVA + 3 covergroup + 10 checker | ✅ 类似覆盖 + KG 覆盖评估 |

> **DocGraph 最弱的场景。** 3x 成本，4x turns。clock 实体仅 7 个可检索（都是 VLM 来源 needs_source_check=true）。agent 被迫频繁回退到 search_chunks→fetch 读原文。

---

### Case 17: KG 缺失审计 (DocGraph HIGH)

| 指标 | Baseline | DocGraph |
|---|---|---|
| Turns | **12** | **2** |
| API 时间 | **86s** | 200s |
| 成本 | **$0.673** | $1.47 |
| 关键发现 | 42 clock 信号, 7 个寄存器名 | 21 clock声称但仅 7 可检索, register 属性缺失 |

> Baseline 从原文找到了 42 个 clock 信号（更完整），DocGraph 揭示了 L2 的 clock 计数 bug (21→7)。**这个 case 完美验证了架构契约：L2 不准但 L1/L0 永远可用。**

---

## 3. 汇总对比

| Case | 类型 | Baseline 成本 | DocGraph 成本 | 差异 | 质量 |
|---|---|---|---|---|---|
| Case 1 | 跨文档地址 | $1.20 | $1.58 | +32% | 均优秀 |
| Case 8A | 信号表 | $0.45 | $0.78 | +75% | 均优秀 |
| Case 8B | 寄存器 RAL | $0.86 | **$0.60** | **-29%** ✅ | 均优秀 |
| Case 9 | UVM Sequence | $0.58 | $0.60 | +3% | 均优秀 |
| Case 11 | Clock/Reset | **$0.81** | $2.59 | +220% ❌ | 均优秀 |
| Case 17 | KG 审计 | $0.67 | $1.47 | +119% | DocGraph 揭示 L2 缺陷 |

| 指标 | Baseline 合计 | DocGraph 合计 |
|---|---|---|
| 总 turns | 95 | **109** |
| 总成本 | **$4.52** | $7.62 |
| 总 API 时间 | 661s | 838s |

---

## 4. 实事求事的结论

### DocGraph 优势场景（2/6 case 胜出或持平）

1. **表格寄存器抽取 (Case 8B/9)**：L2 register/bitfield 实体是确定性抽取，agent 直接拿到精确的 bit range/access/reset，无需人工对齐。这是 DocGraph 的核心价值。
2. **结构化证据 (Case 1)**：block/chunk ID 在工程评审中比页码更具可追溯性。

### DocGraph 弱势场景（2/6 case 明显劣于 Baseline）

1. **Clock/Reset (Case 11)**：clock 实体覆盖率仅 ~15%，且全来自 VLM 图抽取 (needs_source_check=true)。agent 被迫大量回退到 L1/L0，MCP 开销远超纯文本方案。
2. **实体审计 (Case 17)**：KG 对 clock 存在计数问题（声称 21 但仅 7 可检索），agent 需要更多工具调用来确认。

### 中性场景（2/6 case 无明显差异）

- **单页表查找 (Case 8A)**：105KB 文本足够小，全文检索更直接。
- **跨文档 (Case 1)**：两方都能完成任务，DocGraph 成本稍高。

### 五个关键发现

1. **小文档集 (2×42pp, 105KB) 上全文检索优于 MCP 结构化检索**。DocGraph 的优势需要更大规模才能体现。
2. **L2 register/bitfield 是 DocGraph 最强点**。确定性表格抽取提供精确的结构化字段，agent 无需人肉对齐 bit range。
3. **L2 clock/reset 是 DocGraph 最大短板**。覆盖率 ~15%，分类错误 (signal vs clock)，依赖不可信的 VLM 抽取。
4. **架构契约成立** (Case 17 验证)。L2 不准时 L1/L0 永远可回退——agent 能从原文获取完整信息。
5. **MCP 工具减少到 7 个后 agent 不再迷失**。新工具链的 search_chunks→fetch→search 路径清晰，无之前在 20 个工具间绕圈的问题。

### 改进优先级

| 优先级 | 改进项 | 影响 |
|---|---|---|
| P0 | 提高 clock/reset 实体覆盖率（从接口表确定性抽取） | Case 11/13/14 |
| P1 | 修复 clock 实体计数/搜索问题（21 声称 vs 7 可检索） | Case 17, 所有 clock 相关 |
| P2 | 填充 register 实体的 address/offset/access/reset 属性 | Case 8B/9 |
| P3 | 更大规模文档集评测（10+ 文档, 1000+ 页） | 验证规模优势 |
