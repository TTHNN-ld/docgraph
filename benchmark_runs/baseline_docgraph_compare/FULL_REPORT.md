# DocGraph vs Baseline 完整对照报告

**日期**: 2026-07-10 | **KG**: 1123 nodes, 705 edges | **MCP**: 7 工具

## 1. 全部 17 Case 对照表

| Case | 预期收益 | Baseline | | | DocGraph | | | 差异 |
|------|---------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| | | Turns | Cost | Time(s) | Turns | Cost | Time(s) | ΔCost |
| case1 跨文档地址转换 | HIGH | 32 | $1.20 | 165.6s | 30 | $1.57 | 182.0s | **+31%** |
| case10 LTSSM debug 流程 | MEDIUM | 12 | $0.69 | 161.8s | 27 | $0.91 | 129.5s | **+33%** |
| case11 Clock/Reset 验证计划 | LOW | 10 | $0.81 | 164.2s | 40 | $2.59 | 256.1s | **+219%** |
| case12 Tape-in 评审清单 | HIGH | 19 | $0.88 | 204.7s | 31 | $1.11 | 130.4s | **+26%** |
| case13 STA/SDC 约束 | LOW | 7 | $0.66 | 136.4s | 24 | $1.30 | 247.7s | **+96%** |
| case14 CDC/RDC sign-off | LOW | 5 | $0.62 | 139.5s | 22 | $1.92 | 216.2s | **+211%** |
| case15 P&R/PHY 集成 | MEDIUM | 4 | $0.54 | 93.0s | 32 | $1.18 | 121.6s | **+118%** |
| case16 DFT/JTAG bring-up | MEDIUM | 8 | $0.71 | 115.8s | 24 | $0.97 | 96.0s | **+36%** |
| case17 KG 缺失发现 | HIGH | 12 | $0.67 | 85.6s | 2 | $1.47 | 200.0s | **+118%** |
| case2 模块边界与接口 | MEDIUM | 13 | $0.76 | 121.5s | 31 | $1.24 | 162.3s | **+63%** |
| case3 RTL 端口表 | HIGH | 13 | $0.71 | 116.2s | 20 | $1.26 | 135.4s | **+77%** |
| case4 地址/BAR test plan | HIGH | 6 | $0.72 | 142.6s | 26 | $1.01 | 97.0s | **+39%** |
| case5 数据路径 RTL/DV 方案 | MEDIUM | 11 | $0.62 | 74.7s | 30 | $0.93 | 96.0s | **+49%** |
| case6 IOMMU/ATS 验证矩阵 | MEDIUM | 9 | $0.51 | 74.2s | 27 | $0.89 | 93.4s | **+74%** |
| case7 MSI/MSI-X test plan | HIGH | 13 | $0.87 | 179.1s | 31 | $1.68 | 363.8s | **+94%** |
| case8b 寄存器 RAL 输入 | HIGH | 22 | $0.85 | 119.3s | 17 | $0.60 | 97.7s | **-29%** |
| case9 MSI-X UVM sequence | HIGH | 12 | $0.58 | 70.9s | 9 | $0.60 | 51.0s | +3% |
| **合计** | | **208** | **$12.42** | **2165s** | **423** | **$21.22** | **2676s** | **+71%** |

## 2. 按预期收益分组统计

### HIGH 收益 (9 cases — 表格式信息)

| Case | Baseline | DocGraph | Δ |
|---|---:|---:|---:|
| case1 跨文档地址转换 | $1.20 | $1.57 | +31% |
| case12 Tape-in 评审清单 | $0.88 | $1.11 | +26% |
| case17 KG 缺失发现 | $0.67 | $1.47 | +118% |
| case3 RTL 端口表 | $0.71 | $1.26 | +77% |
| case4 地址/BAR test plan | $0.72 | $1.01 | +39% |
| case7 MSI/MSI-X test plan | $0.87 | $1.68 | +94% |
| case8b 寄存器 RAL 输入 | $0.85 | $0.60 | -29% |
| case9 MSI-X UVM sequence | $0.58 | $0.60 | +3% |
| **小计** | **$6.50** | **$9.30** | **+43%** |
| DocGraph 胜出: 1/8 cases | | | |

### MEDIUM 收益 (6 cases — VLM/正文依赖)

| Case | Baseline | DocGraph | Δ |
|---|---:|---:|---:|
| case10 LTSSM debug 流程 | $0.69 | $0.91 | +33% |
| case15 P&R/PHY 集成 | $0.54 | $1.18 | +118% |
| case16 DFT/JTAG bring-up | $0.71 | $0.97 | +36% |
| case2 模块边界与接口 | $0.76 | $1.24 | +63% |
| case5 数据路径 RTL/DV 方案 | $0.62 | $0.93 | +49% |
| case6 IOMMU/ATS 验证矩阵 | $0.51 | $0.89 | +74% |
| **小计** | **$3.83** | **$6.11** | **+59%** |

### LOW 收益 (3 cases — clock/reset 短板)

| Case | Baseline | DocGraph | Δ |
|---|---:|---:|---:|
| case11 Clock/Reset 验证计划 | $0.81 | $2.59 | +219% |
| case13 STA/SDC 约束 | $0.66 | $1.30 | +96% |
| case14 CDC/RDC sign-off | $0.62 | $1.92 | +211% |
| **小计** | **$2.09** | **$5.81** | **+178%** |

## 3. 关键结论

- **DocGraph 最优 case**: case8b (寄存器 RAL 输入) — ΔCost = -29%
- **DocGraph 最差 case**: case11 (Clock/Reset 验证计划) — ΔCost = +219%
- **HIGH 收益组**: Baseline $6.50 → DocGraph $9.30 (+43%)
- **LOW 收益组**: Baseline $2.09 → DocGraph $5.81 (+178%)
- **加权平均成本**: Baseline $0.73 → DocGraph $1.23
- **总成本**: Baseline $12.42 → DocGraph $21.22 (+71%)
- **总 turns**: Baseline 208 → DocGraph 423
- **总耗时**: Baseline 2165s → DocGraph 2676s

### 实事求事

1. **DocGraph 在小文档集 (2×42pp, 105KB) 上总体成本高于 Baseline (+41%)**。全文在上下文窗口内时，结构化 MCP 返回的元数据开销超过其带来的检索效率提升。
2. **DocGraph 在 register/bitfield 确定性抽取场景有明确价值** (Case 8B -29%)。L2 实体直接提供精确 bit range/access/reset，agent 无需人工对齐原表。
3. **Clock/reset 是明确短板** (3 个 LOW case 平均 +180%)。覆盖率 ~15%，entity 几乎全来自 VLM (needs_source_check=true)，agent 被迫大量回退到 L1/L0。
4. **架构契约成立** (Case 17)。L2 缺失时 L1/L0 永远可用。agent 能从原文获取完整信息，只是需要更多工具调用。
5. **新 MCP 工具链有效**。7 工具比旧 20 工具更清晰，agent 遵循 search_chunks→fetch→search 路径，不再绕圈。

### 改进优先级

| P | 改进项 | 影响范围 |
|---|---|---|
| P0 | 提升 clock/reset 实体覆盖率 (从接口表确定性抽取) | Case 11/13/14 |
| P1 | 修复 clock 实体可检索性 (声称 21 但仅 7 可搜索) | 所有 clock 相关 case |
| P2 | 填充 register 实体 address/offset 属性 | Case 8B/9 |
| P3 | 更大文档集评测 (10+ docs, 1000+ pp) 验证规模优势 | 全局 |