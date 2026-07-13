# P&R/PHY Integration Checklist — PCIe Subsystem

**来源文档**
- `arm::protocol::PCIE Subsystem Spec_v3.21` (MAS, v3.21, 28 Jan 2026, 42 pages)
- `arm::doc::PCIe Subsystem TRS_r2p0` (TRS, r1p0, 01 Aug 2025, 42 pages)

**生成说明**：每项 checklist 条目均附带 docgraph block/chunk 证据引用。证据路径为 `文档 → section_id → block_id`。

---

## 1. PHY 物理层集成

### 1.1 PHY 拓扑与 Lane 配置

| # | Checklist Item | 需求/依据 | 证据 (block/chunk) | 状态 |
|---|---|---|---|---|
| 1.1.1 | 4 个 X4 PHY 拼成 X16 (PHY0/PHY1/PHY2/PHY3) | REQ_PHY_1201 | [MAS §1.12#p8#b23] | ☐ |
| 1.1.2 | 支持 lane 配置 x1/x2/x4/x8/x16 | REQ_PHY_1202 | [MAS §1.12#p8#b24] | ☐ |
| 1.1.3 | 16 对数据 lane，每 lane TX+RX 差分串行数据线 | 串行数据接口 | [MAS §3.1#p15#b18-b19] | ☐ |
| 1.1.4 | 支持 Lane Reversal 和 Polarity Inversion | REQ_PCIE_TRS_161 | [TRS §6#p29#b26] | ☐ |
| 1.1.5 | Link width negotiation x4/x8/x16 | REQ_PCIE_TRS_160 | [TRS §6#p29#b25] | ☐ |
| 1.1.6 | 仅 1 个 Die 上使能 PCIe（Die0 boot 时寄存器配置） | REQ_PCIE_TRS_004 | [TRS §5#p15#b5] | ☐ |

### 1.2 PHY 参考时钟

| # | Checklist Item | 需求/依据 | 证据 (block/chunk) | 状态 |
|---|---|---|---|---|
| 1.2.1 | 模式1：PHY0 PAD 输入 100MHz 差分 → PHY1/2/3 级联 | REQ_PHY_1203 | [MAS §1.12#p8#b25] | ☐ |
| 1.2.2 | 模式2：4 个 PHY 均从 PAD 输入同一 100MHz 差分时钟源 | REQ_PHY_1204 | [MAS §1.12#p9#b1] | ☐ |
| 1.2.3 | 模式3：PHY0 从 Local PLL 取 100MHz → PHY1/2/3 级联 | REQ_PHY_1205 | [MAS §1.12#p9#b2] | ☐ |
| 1.2.4 | PLL 参考时钟同源：正常功能从 PHY 取 100MHz | REQ_CRG_1503 | [MAS §1.15#p9#b22] | ☐ |
| 1.2.5 | PLL 参考时钟测试：ref_clk 取 20MHz | REQ_CRG_1502 | [MAS §1.15#p9#b21] | ☐ |

### 1.3 PHY Boot 模式

| # | Checklist Item | 需求/依据 | 证据 (block/chunk) | 状态 |
|---|---|---|---|---|
| 1.3.1 | Boot 模式1：ROM 直接 Boot | REQ_PHY_1207 | [MAS §1.12#p9#b4] | ☐ |
| 1.3.2 | Boot 模式2：ROM → SRAM，SRAM Boot | REQ_PHY_1208 | [MAS §1.12#p9#b5] | ☐ |
| 1.3.3 | Boot 模式3：Bypass ROM，配置总线 load FW → SRAM | REQ_PHY_1209 | [MAS §1.12#p9#b6] | ☐ |
| 1.3.4 | PHY 支持 Normal Mode | REQ_PHY_1214 | [MAS §1.12#p9#b11] | ☐ |
| 1.3.5 | PHY 支持 Fast Mode | REQ_PHY_1206 | [MAS §1.12#p9#b3] | ☐ |

### 1.4 PHY 低功耗与时钟模式

| # | Checklist Item | 需求/依据 | 证据 (block/chunk) | 状态 |
|---|---|---|---|---|
| 1.4.1 | PHY 支持 CC (Common Clock) 模式 | §4.15.5 | [MAS §4.15.5#p37#b18] | ☐ |
| 1.4.2 | PHY 支持 SRIS/SRNS (Separate Clock) 模式 | §4.15.5 | [MAS §4.15.5#p37#b19] | ☐ |
| 1.4.3 | SRIS 下不能使用 L0s（通过 Link Capabilities reg 关闭） | §4.12.1 | [MAS §4.12.1#p32#b0-b1] | ☐ |
| 1.4.4 | L1 Clock PM：L1 期间可关闭 PLL 和 reference clock | §4.12.3.4 | [MAS §4.12.3.4#p33#b4] | ☐ |
| 1.4.5 | PHY 完成低功耗状态管理 | 模块定义 | [MAS §3.1#p16#b13] | ☐ |

### 1.5 PHY SRAM ECC 与寄存器访问

| # | Checklist Item | 需求/依据 | 证据 (block/chunk) | 状态 |
|---|---|---|---|---|
| 1.5.1 | PHY SRAM 支持 ECC，上报 single/double bit error 中断 | REQ_SRAM_ECC_1213 | [MAS §1.12#p9#b10] | ☐ |
| 1.5.2 | CR bus 可访问 PHY 寄存器空间和 SRAM 空间 | REQ_PHY_1210 | [MAS §1.12#p9#b7] | ☐ |
| 1.5.3 | cr_para_sel[1:0] 默认 2'b01 → CR BUS；测试模式选 JTAG/IJTAG | REQ_PHY_1211 | [MAS §1.12#p9#b8] | ☐ |

### 1.6 PHY BERT 测试

| # | Checklist Item | 需求/依据 | 证据 (block/chunk) | 状态 |
|---|---|---|---|---|
| 1.6.1 | PHY0-3 全部支持 BERT Test | REQ_PHY_1215 | [MAS §1.12#p9#b12] | ☐ |
| 1.6.2 | BERT 支持 Error Inject 功能 | REQ_PHY_1215 | [MAS §1.12#p9#b12] | ☐ |

---

## 2. PIPE 接口集成

### 2.1 PIPE 架构选择

| # | Checklist Item | 需求/依据 | 证据 (block/chunk) | 状态 |
|---|---|---|---|---|
| 2.1.1 | PIPE 架构：SerDes PIPE（非 Original PIPE） | §4.9, §4.15.2 | [MAS §4.9#p29#b3-b4], [MAS §4.15.2#p37#b6] | ☐ |
| 2.1.2 | PIPE 版本：PIPE 5.1.1 | §3.1, §4.9 | [MAS §3.1#p15#b15-b16], [MAS §4.9#p29#b3] | ☐ |
| 2.1.3 | PCLK 模式：PCLK as PHY input, PCIe output fixed MAXPCLK | §4.9 | [MAS §4.9#p29#b4] | ☐ |

### 2.2 PIPE 数据接口

| # | Checklist Item | 需求/依据 | 证据 (block/chunk) | 状态 |
|---|---|---|---|---|
| 2.2.1 | SerDes PIPE 和 PHY parallel 数据接口：单个 40bit 数据结构 | §4.15.2 | [MAS §4.15.2#p37#b7] | ☐ |
| 2.2.2 | PHY 通过 PIPE 接口与 PCIe Controller 连接 | 模块定义 | [MAS §3.1#p16#b14] | ☐ |
| 2.2.3 | RX 方向：串行 SerDes → 并行 → PIPE → Controller | 模块定义 | [MAS §3.1#p16#b11] | ☐ |
| 2.2.4 | TX 方向：Controller 并行 → PIPE → PHY → SerDes 串行 | 模块定义 | [MAS §3.1#p16#b12] | ☐ |

### 2.3 PIPE 时钟

| # | Checklist Item | 需求/依据 | 证据 (block/chunk) | 状态 |
|---|---|---|---|---|
| 2.3.1 | 16 lane 每 lane 输出 1GHz max_pclk | §4.15.4 | [MAS §4.15.4#p37#b14] | ☐ |
| 2.3.2 | max_pclk 生成 core_clk (Controller 主要逻辑时钟) | §4.15.4 | [MAS §4.15.4#p37#b14] | ☐ |
| 2.3.3 | max_pclk 生成 pipe_pclk (PIPE TX 接口时钟) | §4.15.4 | [MAS §4.15.4#p37#b14] | ☐ |
| 2.3.4 | RX 数据流恢复 pipe_rx_clk → Controller PIPE RX 逻辑时钟 | §4.15.4 | [MAS §4.15.4#p37#b15] | ☐ |
| 2.3.5 | Controller 用 lane0 固定 1GHz max_pclk → 分频得 core_clk/pclk | REQ_CRG_1506 | [MAS §1.15#p10#b3] | ☐ |
| 2.3.6 | PIPE 接口时钟由 CRG 管理 | 模块定义 | [MAS §3.1#p16#b22] | ☐ |

---

## 3. JTAG/IJTAG/DFT 集成

### 3.1 JTAG 拓扑

| # | Checklist Item | 需求/依据 | 证据 (block/chunk) | 状态 |
|---|---|---|---|---|
| 3.1.1 | 5 个 JTAG：PHY0/1/2/3 + BD，MUX 共用 TSV JTAG PAD | §4.16 | [MAS §4.16#p38#b15-b16] | ☐ |
| 3.1.2 | JTAGMUX 选择信号 TM[2:0] 由 TDR 寄存器静态配置 | REQ_JTAG_1302 | [MAS §1.13#p9#b15] | ☐ |
| 3.1.3 | DFT 通过 TDR 寄存器选择任一 JTAG 接口 | §4.16 | [MAS §4.16#p38#b16] | ☐ |

### 3.2 JTAG 功能

| # | Checklist Item | 需求/依据 | 证据 (block/chunk) | 状态 |
|---|---|---|---|---|
| 3.2.1 | JTAG 可访问 PHY 内部寄存器 | REQ_JTAG_1303 | [MAS §1.13#p9#b16] | ☐ |
| 3.2.2 | PHY0-3 全部支持 JTAG 访问内部寄存器 | REQ_PHY_1212 | [MAS §1.12#p9#b9] | ☐ |
| 3.2.3 | JTAG 主要功能：PHY 内部寄存器访问 → BERT 测试 | §4.16 | [MAS §4.16#p38#b18] | ☐ |
| 3.2.4 | DB JTAG 穿过 PCIe → TSV → BD，DFT 用此组 JTAG 测 BD | §4.16 | [MAS §4.16#p38#b18] | ☐ |

### 3.3 IJTAG

| # | Checklist Item | 需求/依据 | 证据 (block/chunk) | 状态 |
|---|---|---|---|---|
| 3.3.1 | IJTAG 接 SoC DFT 控制器 | REQ_JTAG_1401 | [MAS §1.14#p9#b17-b18] | ☐ |
| 3.3.2 | DFT 控制器将外部 JTAG → IJTAG 访问 PHY 内部寄存器 | REQ_JTAG_1401 | [MAS §1.14#p9#b18] | ☐ |
| 3.3.3 | DFT 用 IJTAG 需配置 TDR cr_para_sel = 2'b10 | REQ_JTAG_1401 | [MAS §1.14#p9#b18] | ☐ |
| 3.3.4 | cr_para_sel[1:0] 默认 2'b01 (CR BUS 正常访问) | REQ_PHY_1211 | [MAS §1.12#p9#b8] | ☐ |

### 3.4 DFT 测试

| # | Checklist Item | 需求/依据 | 证据 (block/chunk) | 状态 |
|---|---|---|---|---|
| 3.4.1 | TDR 控制 cr_para_sel 选择 JTAG/IJTAG 测试模式 | REQ_PHY_1211 | [MAS §1.12#p9#b8] | ☐ |
| 3.4.2 | LTSSM debug shifter：挂死后可读最近 128 个 LTSSM 状态 | REQ_DEBUG_2301 | [MAS §1.23#p11#b19] | ☐ |
| 3.4.3 | PLL 时钟/状态可拉到 debug IO | REQ_DEBUG_2302 | [MAS §1.23#p11#b20] | ☐ |
| 3.4.4 | PHY 主要时钟和状态可拉到 debug IO | REQ_DEBUG_2303 | [MAS §1.23#p11#b21] | ☐ |
| 3.4.5 | Debug IO: APB 配寄存器选 diag_state_bus + debug 信号 → PAD | §4.13.1 | [MAS §4.13.1#p34#b4-b5] | ☐ |

---

## 4. CRG (Clock/Reset Generator) 集成

### 4.1 PLL 特性

| # | Checklist Item | 需求/依据 | 证据 (block/chunk) | 状态 |
|---|---|---|---|---|
| 4.1.1 | 功能模式 PLL 输出最高 1GHz | REQ_CRG_1501 | [MAS §1.15#p9#b20] | ☐ |
| 4.1.2 | 测试模式 PLL 输出最高 2GHz | REQ_CRG_1501 | [MAS §1.15#p9#b20] | ☐ |
| 4.1.3 | PCIE CRG 内置 PLL，产生 AMBA 总线时钟 root 时钟 | 模块定义 | [MAS §3.1#p16#b21] | ☐ |
| 4.1.4 | PLL 参考时钟从 PHY 获取同源 100MHz（正常功能） | REQ_CRG_1503 | [MAS §1.15#p9#b22] | ☐ |
| 4.1.5 | PLL 参考时钟可从 ref_clk 取 20MHz（测试/debug） | REQ_CRG_1502 | [MAS §1.15#p9#b21] | ☐ |

### 4.2 功能时钟分配

| # | Checklist Item | 需求/依据 | 证据 (block/chunk) | 状态 |
|---|---|---|---|---|
| 4.2.1 | 功能模式时钟：axi_clk 1GHz, cr_para_clk 125MHz, local_phy_ref_clk 100MHz | REQ_CRG_1504 | [MAS §1.15#p10#b1] | ☐ |
| 4.2.2 | 测试模式时钟：clk_test0 2GHz, clk_test1 1GHz, clk_test2 125MHz, clk_test3 100MHz | REQ_CRG_1505 | [MAS §1.15#p10#b2] | ☐ |
| 4.2.3 | CRG 管理 core_clk, AMBA 总线时钟, PIPE 接口时钟, 低功耗时钟 | 模块定义 | [MAS §3.1#p16#b22] | ☐ |
| 4.2.4 | lock_phy_ref_clk 给 PHY 作为参考时钟（调试备用） | §4.15.3 | [MAS §4.15.3#p37#b12] | ☐ |

### 4.3 Reset 管理

| # | Checklist Item | 需求/依据 | 证据 (block/chunk) | 状态 |
|---|---|---|---|---|
| 4.3.1 | CRG 管理 PCIe 子系统 reset | 模块定义 | [MAS §3.1#p16#b23] | ☐ |
| 4.3.2 | 负责 Power On Reset | 模块定义 | [MAS §3.1#p16#b23] | ☐ |
| 4.3.3 | 负责暖复位 (Warm Reset) | 模块定义 | [MAS §3.1#p16#b23] | ☐ |
| 4.3.4 | 负责低功耗时部分复位 | 模块定义 | [MAS §3.1#p16#b23] | ☐ |
| 4.3.5 | PCIe 有独立 CRG，CRG 不应被 FLR 复位 | REQ_PCIE_TRS_163 | [TRS §6#p29#b16] | ☐ |

---

## 5. Reset 方案集成

### 5.1 Reset 类型与行为

| # | Checklist Item | 需求/依据 | 证据 (block/chunk) | 状态 |
|---|---|---|---|---|
| 5.1.1 | Power On Reset：PCIe 子系统支持 | §3.1 | [MAS §3.1#p16#b4] | ☐ |
| 5.1.2 | Warm Reset：全芯片 Reset | REQ_WarmReset_1605 | [MAS §1.16#p10#b9] | ☐ |
| 5.1.3 | Hot Reset：收到连续 2 个 Hot Reset=1 的 TS1，触发 flush | §4.5.3 | [MAS §4.5.3#p23#b7-b9] | ☐ |
| 5.1.4 | Hot Reset 对 SoC Reset，PCIe 子系统不 Reset（HotResetBlock/TBU/ReMap 除外） | REQ_HotReset_1603 | [MAS §1.16#p10#b7] | ☐ |
| 5.1.5 | Hot Reset 应触发 SoC 除 PCIe Controller 外所有逻辑 Reset | REQ_PCIE_TRS_900 | [TRS §6#p29#b13] | ☐ |
| 5.1.6 | Link Disable 等同于 Hot Reset 处理 | REQ_Disable_1604 | [MAS §1.16#p10#b8] | ☐ |
| 5.1.7 | 不支持 FLR (Function Level Reset) | REQ_FLR_1606 | [MAS §1.16#p10#b10], [TRS §6#p29#b12] | ☐ |

### 5.2 Hot Reset 时序控制

| # | Checklist Item | 需求/依据 | 证据 (block/chunk) | 状态 |
|---|---|---|---|---|
| 5.2.1 | flush timer 默认 timeout 2us，软件可配置 | REQ_HotReset_1601 | [MAS §1.16#p10#b5] | ☐ |
| 5.2.2 | 拉低 app_ltssm_enable 可延长 flush 时间 | REQ_HotReset_1601 | [MAS §1.16#p10#b5] | ☐ |
| 5.2.3 | Timer 支持软件 flush 提前结束 | REQ_HotReset_1601 | [MAS §1.16#p10#b5] | ☐ |
| 5.2.4 | flush_time_ctrl 寄存器可配 timeout 值 | §4.20.1 | [MAS §4.20.1#p40#b2] | ☐ |
| 5.2.5 | flush 完成后 PCIe Controller assert core_rst_n, app_ltssm_enable → 0 | §4.20.1 | [MAS §4.20.1#p41#b1] | ☐ |
| 5.2.6 | SoC 需重新配 app_ltssm_enable 进行建连 | REQ_HotReset_1602 | [MAS §1.16#p10#b6] | ☐ |

### 5.3 PERST# Warm Reset

| # | Checklist Item | 需求/依据 | 证据 (block/chunk) | 状态 |
|---|---|---|---|---|
| 5.3.1 | PERST# 输入 PAD 用于 controller warm Reset | §4.17 | [MAS §4.17#p39#b2] | ☐ |
| 5.3.2 | PERST# 控制 perst_n → 复位 controller 和 PHY 相关逻辑 | §3.1 | [MAS §3.1#p16#b3] | ☐ |
| 5.3.3 | RC 可随时拉低 PERST# 复位 EP | §4.20.2 | [MAS §4.20.1#p40#b2] | ☐ |

### 5.4 PAD 信号

| # | Checklist Item | 需求/依据 | 证据 (block/chunk) | 状态 |
|---|---|---|---|---|
| 5.4.1 | CLKREQ#：输入 PAD，L1 SubState 控制参考时钟开关 | §4.17 | [MAS §4.17#p39#b0] | ☐ |
| 5.4.2 | WAKE#：输出 PAD，L2/L3 唤醒控制 | §4.17 | [MAS §4.17#p39#b1] | ☐ |
| 5.4.3 | PERESET#：输入 PAD，warm Reset controller | §4.17 | [MAS §4.17#p39#b2] | ☐ |

---

## 6. 初始化序列 (Initialization Sequence)

### 6.1 上电初始化序列 (§4.20.1)

| # | Checklist Item | 证据 (block/chunk) | 状态 |
|---|---|---|---|
| 6.1.1 | Step 6: 写 phy_ss_reg.phyX_sram_ctrl.sram_ext_ld_done=1 通知 PHY FW 更新完成（仅 enable PHY） | [MAS §4.20.1#p40#b1] | ☐ |
| 6.1.2 | Step 7: 配置 PCIE TOP, PCIe Controller, PCIE CRG 寄存器 | [MAS §4.20.1#p40#b1] | ☐ |
| 6.1.3 | Step 8: 配置中断相关寄存器（可选） | [MAS §4.20.1#p40#b1] | ☐ |
| 6.1.4 | Step 9: 写 app_hold_phy_rst = 0 | [MAS §4.20.1#p40#b1] | ☐ |
| 6.1.5 | Step 10: 配 app_ltssm_enable = 1 | [MAS §4.20.1#p40#b1] | ☐ |
| 6.1.6 | Step 11: 等待 rdlh_link_up = 1（Link Training 完成标志） | [MAS §4.20.1#p40#b1] | ☐ |
| 6.1.7 | Step 12-13: RC 枚举 EP → 开始传数据 | [MAS §4.20.1#p40#b1] | ☐ |

### 6.2 PERST# Sequence (§4.20.2)

| # | Checklist Item | 证据 (block/chunk) | 状态 |
|---|---|---|---|
| 6.2.1 | Step 14: EP CPU 收到 link_down 中断 | [MAS §4.20.1#p40#b2] | ☐ |
| 6.2.2 | Step 15: CPU 收到 core_rst_int → 配置 PCIe 寄存器 | [MAS §4.20.1#p40#b2] | ☐ |
| 6.2.3 | Step 16: 配 app_ltssm_enable = 1 | [MAS §4.20.1#p40#b2] | ☐ |
| 6.2.4 | Step 17-19: 等 link_up → RC 枚举 → 传数据 | [MAS §4.20.1#p40#b2] | ☐ |

### 6.3 Hot Reset Sequence (§4.20.3)

| # | Checklist Item | 证据 (block/chunk) | 状态 |
|---|---|---|---|
| 6.3.1 | Step 14: 收 hot_reset_int 中断 → 处理 | [MAS §4.20.1#p40#b2] | ☐ |
| 6.3.2 | Step 15: CPU 收 link_down 中断 | [MAS §4.20.1#p40#b2] | ☐ |
| 6.3.3 | Step 16: HotResetBlock 屏蔽 link_down 后新传输 → Controller flush | [MAS §4.20.1#p40#b2] | ☐ |
| 6.3.4 | Step 17: flush 完成后 assert core_rst_n, app_ltssm_enable → 0 | [MAS §4.20.1#p41#b1] | ☐ |
| 6.3.5 | Step 18: CPU 收 core_rst_int → SoC watchdog → SoC Reset | [MAS §4.20.1#p41#b1] | ☐ |
| 6.3.6 | Step 19-24: 重新 boot SoC（PCIe 不 reboot）→ 可选重配 → link_up → 枚举 → 传数据 | [MAS §4.20.1#p41#b1] | ☐ |

---

## 7. 中断集成

| # | Checklist Item | 需求/依据 | 证据 (block/chunk) | 状态 |
|---|---|---|---|---|
| 7.1 | PCIe 输出到 SoC 中断均为 level 中断 | REQ_IRQ_1701 | [MAS §1.17#p10#b12] | ☐ |
| 7.2 | 重要中断保证上报：DMA, link down, hot reset, remap error, TBU error, flush done, PLL lock/unlock, PHY ref clk on/off, PCIe Controller 重要 Error | REQ_IRQ_1706 | [MAS §1.17#p10#b17] | ☐ |
| 7.3 | DMA 32 通道中断独立输出 | REQ_IRQ_1702 | [MAS §1.17#p10#b13] | ☐ |
| 7.4 | TBU 2 个中断独立输出 | REQ_IRQ_1703 | [MAS §1.17#p10#b14] | ☐ |

---

## 8. 寄存器空间

| # | Checklist Item | 证据 (block/chunk) | 状态 |
|---|---|---|---|
| 8.1 | Part1: 子系统 CSR（PCIe 控制器适配 + PHY 适配 + CRG 控制 + 顶层逻辑控制） | [MAS §4.21#p41#b2-b4] | ☐ |
| 8.2 | Part2: PHY 内部控制寄存器（APB slave → CR → 4 个 PHY CR 接口）—— 参考 dwc_pcie5phy_ss8lpp_x2ns_databook.pdf | [MAS §4.21#p41#b5-b6] | ☐ |
| 8.3 | Part3: PCIe Controller 内部寄存器 —— 参考 Controller databook | [MAS §4.21#p41#b7] | ☐ |

---

## 9. 物理层待确认项（TRS 遗留）

| # | Checklist Item | 证据 (block/chunk) | 状态 |
|---|---|---|---|
| 9.1 | 需进一步确认物理层特性（EQ 等） | [TRS §6.14#p39#b9-b10] | ☐ |
| 9.2 | Retimer 数量确认（影响 Retry Buffer 等硬件资源） | [TRS §6#p29#b21-b22] | ☐ |

---

## 10. 集成总览（Block Diagram 模块清单）

| 模块 | 功能摘要 | 证据 |
|---|---|---|
| **PHY** (×4) | SerDes 串并转换、PCIe5.0 物理层协议、低功耗管理、PIPE 连接 Controller | [MAS §3.1#p16#b10-b14] |
| **PCIE_CRG** | 内置 PLL、时钟生成 (core/AMBA/PIPE/低功耗)、Reset 管理 (POR/Warm/低功耗) | [MAS §3.1#p16#b20-b23] |
| **CFG** | 顶层逻辑控制寄存器、Controller/PHY 外围接口寄存器、APB→CR 协议转换 | [MAS §3.1#p16#b34-p17#b2] |
| **TBU** | DTI 接口接 TCU，共同完成 SMMU 功能 | [MAS §3.1#p17#b3-b5] |
| **HotResetBlock** | Hot Reset 时管理 flush 和屏蔽新传输 | [MAS §3.1#p17#b6-b8] |
| **JTAG MUX** | 5 路 JTAG (PHY0-3 + BD) MUX → TSV JTAG PAD | [MAS §4.16#p38#b15-b16] |

---

**文档版本**: 基于 MAS v3.21 (2026-01-28) + TRS r1p0 (2025-08-01)
**生成日期**: 2026-07-10
**工具**: docgraph MCP (search_chunks + fetch + search entities)
