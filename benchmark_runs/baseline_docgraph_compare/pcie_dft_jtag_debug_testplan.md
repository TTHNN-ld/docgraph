# PCIe Subsystem DFT/JTAG/Debug 可测性计划

> 基于 PCIe Subsystem Spec v3.21 (2026-01-28)
> 覆盖: JTAG decoder, gating/level shifter/isolation, PHY0-3 测试连接, LTSSM debug, interrupt/status 观测点

---

## 1. JTAG Decoder 测试

### 1.1 架构概述 (Spec §1.13, §1.14, §4.16)

- **5 路 JTAG MUX**: PHY0 / PHY1 / PHY2 / PHY3 / BD 共 5 路 JTAG 经 MUX 复用 TSV JTAG PAD
- **选择信号**: TDR 寄存器静态配置 `TM[2:0]` 选择 JTAG 接口
- **IJTAG 通路**: SoC DFT 控制器将外部 JTAG 转 IJTAG 访问 PHY 内部寄存器
- **CR/JTAG 切换**: TDR 寄存器 `cr_para_sel[1:0]`:
  - `00` / `01` (default) → CR BUS 正常配置访问
  - `10` → IJTAG (DFT 控制器)
  - `11` → JTAG 直接访问

### 1.2 测试项

| # | 测试项 | 操作 | 检查点 | 对应需求 |
|---|--------|------|--------|---------|
| JTAG-01 | JTAG MUX 选择 PHY0 | 配置 TDR `TM[2:0]` = PHY0, `cr_para_sel=11`; JTAG IDCODE 读 PHY0 | 返回正确的 PHY0 IDCODE | REQ_JTAG_1301/1303 |
| JTAG-02 | JTAG MUX 选择 PHY1 | 配置 TDR `TM[2:0]` = PHY1; JTAG IDCODE 读 PHY1 | 返回正确的 PHY1 IDCODE | REQ_JTAG_1301/1303 |
| JTAG-03 | JTAG MUX 选择 PHY2 | 配置 TDR `TM[2:0]` = PHY2; JTAG IDCODE 读 PHY2 | 返回正确的 PHY2 IDCODE | REQ_JTAG_1301/1303 |
| JTAG-04 | JTAG MUX 选择 PHY3 | 配置 TDR `TM[2:0]` = PHY3; JTAG IDCODE 读 PHY3 | 返回正确的 PHY3 IDCODE | REQ_JTAG_1301/1303 |
| JTAG-05 | JTAG MUX 选择 BD | 配置 TDR `TM[2:0]` = BD; JTAG 访问 BD 寄存器 | BD JTAG 链正常响应 | REQ_JTAG_1301 |
| JTAG-06 | IJTAG 通路验证 | 配置 `cr_para_sel=10`; DFT 控制器发 IJTAG 访问 PHY0 内部寄存器 | IJTAG 读回 PHY0 寄存器值与 CR BUS 读回一致 | REQ_JTAG_1401 |
| JTAG-07 | CR↔JTAG 切换无锁死 | 在 CR BUS 与 JTAG 间反复切换 `cr_para_sel` 10 次 | 每次切换后对应通路正常工作，无 hang | REQ_PHY_1211 |
| JTAG-08 | JTAG BERT 测试 (PHY0) | 通过 JTAG 配置 PHY0 BERT Pattern Generator, 使能内部 loopback, 启动 BERT, 读 Error Counter | Error Count = 0 (无噪声环境) | REQ_PHY_1215 |
| JTAG-09 | JTAG BERT 测试 (PHY1-3) | 同上, 依次对 PHY1/2/3 执行 BERT | 各 PHY BERT Error Count = 0 | REQ_PHY_1215 |
| JTAG-10 | JTAG 访问 PHY SRAM | 通过 JTAG 写/读 PHY0 SRAM 空间 | 读回值与写入值一致 | REQ_PHY_1210 |
| JTAG-11 | MUX 切换后状态保持 | 选择 PHY0 JTAG → 读寄存器 A → 切到 PHY1 → 读寄存器 B → 切回 PHY0 → 读寄存器 A | 切回后 A 值保持一致 | REQ_JTAG_1301 |

### 1.3 JTAG Decoder 结构检查

- [ ] `TM[2:0]` 解码器: 5 路 one-hot 输出, 确保无毛刺切换 (glitch-free MUX)
- [ ] `cr_para_sel[1:0]` 解码: CR BUS / IJTAG / JTAG 三选一, 确认默认值 `2'b01` 上电后 CR BUS 可用
- [ ] JTAG TAP 状态机: 确认 Test-Logic-Reset 后可正常进入 Shift-IR/Shift-DR
- [ ] TDR 寄存器: 确认 TDR 在 JTAG 链上可访问 (若设计支持 JTAG 访问 TDR)

---

## 2. Gating / Level Shifter / Isolation 测试

### 2.1 架构概述 (Spec §1.20, §4.19)

- **Partial Good Isolation**: PCIe 子系统顶层实现, isolation enable 寄存器控制 (默认 enable=1)
- **Isolation 域**: 整个 `pcie_core` 功能单元 (CFG NoC, HotResetBlock, TBU, CRG 除外)
- **Isolation 时约束**:
  - AXI Slave 对 NoC 的访问不 hang
  - AXI Master 不主动发起对 NoC 的访问
  - 配置总线访问不 hang
- **TSV 直穿信号**: BD port 上有 Isolation + Level Shifter, 控制寄存器在 PCIe 子系统软件可配
- **Disable**: PHY 断电 (PCB 保证), Controller 可 isolation

### 2.2 测试项

| # | 测试项 | 操作 | 检查点 | 对应需求 |
|---|--------|------|--------|---------|
| ISO-01 | Isolation 默认状态验证 | 上电后读 isolation enable 寄存器 | enable = 1 (isolation 默认开启) | REQ_ISO_2002 |
| ISO-02 | Isolation 关闭后功能正常 | 写 isolation enable = 0; 发起 AXI Slave 读写 → PCIe → PHY loopback | 数据通路正常, 读回匹配 | REQ_ISO_2002 |
| ISO-03 | Isolation 开启下 AXI Slave 不 hang | 写 isolation enable = 1; SoC 侧发起 AXI Slave 读 | 返回 Error (不 hang), NoC 无超时 | REQ_ISO_2003 |
| ISO-04 | Isolation 开启下 AXI Master 不发起访问 | 写 isolation enable = 1; 监测 AXI Master AW/AR channel | AWVALID/ARVALID 恒为 0 | REQ_ISO_2003 |
| ISO-05 | Isolation 开启下配置总线不 hang | 写 isolation enable = 1; 通过 AXI-lite 读 PCIe 子系统寄存器 | 返回 Error 或 0, 不 hang | REQ_ISO_2003 |
| ISO-06 | TSV Isolation 开关测试 | 分别配置 TSV isolation enable = 0/1; 监测 TSV 输出信号 | enable=1 时 TSV 输出隔离值; enable=0 时正常传递 | REQ_ISO_2004 |
| ISO-07 | TSV Level Shifter 功能 | 在 TSV 通路上发送数据 pattern (全 0 → 全 1 → AA/55 toggle) | 接收端电平正确, 无电平漂移 | REQ_ISO_2004 |
| ISO-08 | Partial Good 域隔离不影响 NoC | isolation enable = 1; MainNoC 对非 PCIe 目标发起正常读写 | MainNoC 其他通路正常工作 | REQ_ISO_2003 |
| ISO-09 | Disable Controller + PHY | Controller isolation enable = 1; PHY 外部断电; 检查漏电流 | Controller 无异常访问; PHY 漏电流 < spec 值 | REQ_DISABLE_2005 |
| ISO-10 | NoC Soft Reset 在 isolation 下 | isolation enable = 1; MainNoC soft reset | NoC 正常完成 soft reset (AXI 需有时钟) | REQ_ISO_2003 Note |
| ISO-11 | HotResetBlock firewall 模式强制 | isolation enable = 1; 验证 HotResetBlock 模式 | HotResetBlock 强制进入 firewall 模式 | REQ_HotResetBlock_2101 |

### 2.3 Gating/Level Shifter 结构检查

- [ ] Clock gating 单元: 确认各时钟域 (axi_clk, core_clk, cr_clk, pipe_clk) 的 gating enable 信号正确
- [ ] Isolation 单元: 确认 AXI slave/master 接口的 isolation clamp 值 (建议 slave 返回 SLVER/RESP=10)
- [ ] TSV Level Shifter: 确认 BD port 侧电平与 PCIe 内部电平域一致, 上电/断电序列无闩锁
- [ ] gating/isolation 控制寄存器: 确认寄存器在 partial good 域外 (不在 pcie_core 域内)

---

## 3. PHY0-3 测试连接

### 3.1 架构概述 (Spec §1.12, §4.15)

- **4 个 x4 PHY 组成 x16**: PHY0 (lane 0-3), PHY1 (lane 4-7), PHY2 (lane 8-11), PHY3 (lane 12-15)
- **参考时钟 3 种模式**: (1) PHY0 PAD输入 → PHY1-3 菊花链 (2) 各自 PAD 输入 (3) PHY0 Local PLL → PHY1-3 菊花链
- **启动 3 种模式**: ROM 直接启动 / ROM→SRAM 启动 / CR BUS 加载 FW→SRAM 启动
- **测试接口**: CR BUS (配置寄存器+SRAM), JTAG (BERT + 内部寄存器), IJTAG (DFT)
- **PHY 低功耗**: P0, P0s, P1, P1.CPM, P1.1, P1.2
- **Loopback**: 内部串行 loopback + 外部 PAD loopback
- **SRAM ECC**: Single/Double bit error 检测与中断上报

### 3.2 测试项

| # | 测试项 | 操作 | 检查点 | 对应需求 |
|---|--------|------|--------|---------|
| PHY-01 | CR BUS 访问 PHY0 寄存器 | 通过 AXI-lite → NoC → CRG → PHY0 CR 接口读 PHY ID 寄存器 | 返回已知 PHY ID | REQ_PHY_1210 |
| PHY-02 | CR BUS 访问 PHY1-3 寄存器 | 同上, 依次读 PHY1/2/3 ID | 均返回正确 ID | REQ_PHY_1210 |
| PHY-03 | CR BUS 访问 PHY SRAM | 写 pattern 到 PHY0 SRAM → 回读 → 对比; 对 PHY1-3 重复 | 读写一致 | REQ_PHY_1210 |
| PHY-04 | ROM Boot 模式验证 | 配置 PHY 为 ROM 直接 boot; 释放 PHY reset; 读 PHY boot status | Boot done, 无 error flag | REQ_PHY_1207 |
| PHY-05 | SRAM Boot 模式验证 | 配置 PHY 为 ROM→SRAM boot; 释放 reset; 读 status | Boot done, SRAM 加载完成 | REQ_PHY_1208 |
| PHY-06 | CR BUS FW Load 模式 | 通过 CR BUS 写 FW 到 PHY SRAM → 写 `sram_ext_ld_done=1` → 释放 reset | Boot done, PHY 正常初始化 | REQ_PHY_1209 |
| PHY-07 | 参考时钟模式 1 (菊花链) | PHY0 从 PAD 输入 100MHz; PHY1-3 级联接; 测各 PHY ref_clk | 各 PHY 参考时钟锁定, 频率 100MHz ± 容差 | REQ_PHY_1203 |
| PHY-08 | 参考时钟模式 2 (独立同源) | 4 个 PHY 各自从 PAD 输入同一 100MHz 源; 测各 PHY ref_clk | 4 路时钟相位对齐, PLL lock | REQ_PHY_1204 |
| PHY-09 | 参考时钟模式 3 (Local PLL) | PHY0 从 Local PLL 取 100MHz; PHY1-3 菊花链; 测各 PHY | 正常锁定, 频率正确 | REQ_PHY_1205 |
| PHY-10 | PHY 内部 Loopback | 配置 lane0 为内部 serial loopback; 发 PRBS pattern; 检查接收 | 无误码 (BER < 1e-12) | REQ_LOOPBACK_2202 |
| PHY-11 | PHY 外部 PAD Loopback | 外部夹具回环 lane0 TX→RX; 发 PRBS; 检查接收 | 无误码 | REQ_LOOPBACK_2202 |
| PHY-12 | BERT with Error Injection | JTAG 配置 BERT → 使能 error inject → 读 error counter | Error counter = inject 次数 | REQ_PHY_1215 |
| PHY-13 | PHY Lane Turn-off | 配置 lane 8-15 turn off; 验证 lane 0-7 仍正常建连 x8 | x8 link up; lane 8-15 电气 idle | — |
| PHY-14 | PHY SRAM ECC 单 bit 错误 | JTAG/CR 注入 SRAM 单 bit 翻转; 读 ECC status | `phy_sram_sec_irq` 上报 | REQ_SRAM_ECC_1213 |
| PHY-15 | PHY SRAM ECC 双 bit 错误 | JTAG/CR 注入 SRAM 双 bit 翻转 | `phy_sram_ded_irq` 上报 | REQ_SRAM_ECC_1213 |
| PHY-16 | PHY 低功耗 P0s 进出 | 配置进入 L0s; 读 PHY power state | P0→P0s 切换正确; 恢复 L0 后数据通路正常 | REQ_POWER_905 |
| PHY-17 | PHY 低功耗 P1 进出 | 配置进入 L1 (ASPM); 验证 PHY 进入 P1; 发 CLKREQ# 退出 | P1 entry/exit 正确; pipe_clk/core_clk 停止/恢复 | REQ_POWER_905 |
| PHY-18 | PHY Fast Mode | 配置 Fast Mode; 验证 link training 时间 | LTSSM 进入 L0 时间 ≤ Fast Mode spec | REQ_PHY_1206 |
| PHY-19 | PHY PCLK 稳定性 | 监测各 lane max_pclk (1GHz) | 频率 1GHz ± 容差; 抖动 < spec | — |
| PHY-20 | Lane Reversal + Flip | 配置 auto lane reversal; 物理反转 lane 连接; 验证建连 | Link up 在反转后 lane 上 | REQ_REVERSAL_1101 |

---

## 4. LTSSM Debug 测试

### 4.1 架构概述 (Spec §4.13.2)

- **128 深度 shift 寄存器**: 每个 entry 存 `ltssm_state[5:0]` + `ltssm_state_vld[6]`
- **Freeze 机制**: 软件写 `freeze_reg=1` → 硬件做上升沿检测 → DMUX 写使能 → shift_reg 内容写入 state_reg → freeze_reg 回 0
- **APB 回读**: 软件 poll `freeze_reg` 变为 0 → 通过 APB 读 `ltssm_state_reg[127:0]`

### 4.2 测试项

| # | 测试项 | 操作 | 检查点 | 对应需求 |
|---|--------|------|--------|---------|
| LTSSM-01 | shift_reg 正常记录状态 | 正常建连 (Detect→Polling→Config→L0); freeze → 读 state_reg | 状态序列完整覆盖 LTSSM 迁移路径 | REQ_DEBUG_2301 |
| LTSSM-02 | 128 深度满后覆盖 | 触发 >128 次 LTSSM 状态变化 (如反复 L0→L0s→L0); freeze → 读 | 最新 128 个状态保留, 旧状态被覆盖 | REQ_DEBUG_2301 |
| LTSSM-03 | freeze_reg 握手正常 | 写 freeze_reg=1; 计时 freeze_reg 回 0 的延迟 | 延迟 < N 个 core_clk 周期 (N ≤ 10) | REQ_DEBUG_2301 |
| LTSSM-04 | freeze 期间 shift_reg 不受影响 | freeze 过程中触发 LTSSM 状态变化; 读 state_reg | state_reg 保存 freeze 时刻快照, 不受后续变化影响 | REQ_DEBUG_2301 |
| LTSSM-05 | vld bit 正确标记 | 读 state_reg, 检查每 entry 的 bit[6] | 有效 entry 的 vld=1; 未使用的 entry vld=0 | REQ_DEBUG_2301 |
| LTSSM-06 | Link down 挂死场景捕获 | 强制 link down (如拔线/关远端); 等待挂死; freeze → 读状态序列 | 最后 N 个状态显示 LTSSM 从 L0 → Recovery → Detect 的退化路径 | REQ_DEBUG_2301 |
| LTSSM-07 | Hot Reset 场景捕获 | 远端发 Hot Reset; freeze → 读 shift_reg | 状态序列显示进入 HOT_RESET → 退出过程 | REQ_DEBUG_2301 |
| LTSSM-08 | L1 进出状态捕获 | 触发 L1 entry/exit; freeze → 读 | 状态序列显示 L0 → Recovery → L1 (entry) 和 L1 → Recovery → L0 (exit) | REQ_DEBUG_2301 |
| LTSSM-09 | Gen 切换状态捕获 | 强制 speed change Gen5→Gen1→Gen5; freeze → 读 | 状态序列显示 Recovery.RcvrLock → Recovery.RcvrCfg → Recovery.EQ 阶段 | REQ_DEBUG_2301 |
| LTSSM-10 | 连续 freeze 操作 | 连续 3 次 freeze → read → freeze → read | 每次读出的状态序列对应各自 freeze 时刻, 无数据混淆 | REQ_DEBUG_2301 |

---

## 5. Interrupt / Status 观测点

### 5.1 架构概述 (Spec §4.6, §4.13.1, §4.11)

- **中断输出**: `irq_func[1]`, `irq_err[1]`, `edma_int[32]`, `tbu_ras[1]`, `tbu_pmu[1]` (均为 level)
- **重要中断** (按 Spec §4.6.1): 约 35+ 中断源, 包括 link up/down, hot reset, flush done, PLL lock/unlock, parity error, PHY ECC error, DMA complete 等
- **Debug IO** (§4.13.1): `cfg_dbg_sel_group[1:0]` 选 1/4 group; bit[0:7] 选 group 内 8 bits 输出到 PAD[0:7]
- **RAS 观测**: parity 信号 ~50+ 条汇聚为 `ctl_rasdp_irq`; SRAM ECC; AER

### 5.2 测试项

| # | 测试项 | 操作 | 检查点 | 对应需求 |
|---|--------|------|--------|---------|
| INT-01 | 中断电平类型验证 | 触发 link_up → 读 irq_func; 不清除中断 → 再次读 | irq_func 保持高, 直到软件 clear | REQ_IRQ_1701 |
| INT-02 | irq_func 汇聚输出 | 逐一触发各 function 中断源; 监测 irq_func | 任一个 function 中断触发, irq_func 拉高 | REQ_IRQ_1705 |
| INT-03 | irq_err 汇聚输出 | 逐一触发各 error 中断源 (如 parity error injection); 监测 irq_err | 任一个 error 中断触发, irq_err 拉高 | REQ_IRQ_1704 |
| INT-04 | DMA 32ch 中断独立 | 触发 DMA ch0 完成 → 读 edma_int[0]; 触发 ch15 → 读 edma_int[15] | 各通道中断独立, 互不干扰 | REQ_IRQ_1702 |
| INT-05 | TBU 中断独立 | 触发 TBU RAS 错误 → 读 tbu_ras; 触发 TBU PMU → 读 tbu_pmu | 两根中断线独立输出 | REQ_IRQ_1703 |
| INT-06 | Link up 中断 | 正常建连 → 监测 `rdlh_link_up` / `smlh_link_up` | 建连完成时两个中断先后触发 | REQ_IRQ_1706 |
| INT-07 | Link down 中断 | 远端断开 link → 监测 `link_down_event_int` | 中断在 link down 时触发 | REQ_IRQ_1706 |
| INT-08 | Hot Reset 中断 | 远端发 Hot Reset → 监测 `hot_reset_int` → `pcie_flush_done_int` → `core_rst_int` | 中断序列: hot_reset → flush_done → core_rst | REQ_IRQ_1706 |
| INT-09 | PLL lock/unlock 中断 | 配置 PLL reference clock 切换 → 监测 `phy_pll_lock_int` / `phy_pll_unlock_int` | PLL 失锁时 unlock 上报; 重新锁定后 lock 上报 | REQ_IRQ_1706 |
| INT-10 | PHY ref clock 中断 | 控制 PHY reference clock on/off → 监测 `ref_clk_req_assert_int` / `ref_clk_req_deassert_int` | ref_clk 移除/恢复时对应中断触发 | REQ_IRQ_1706 |
| INT-11 | Remap Error 中断 | 配置 Remap 非法地址 → 监测 `remap_err_irq` | 非法访问触发 error | REQ_IRQ_1706 |
| INT-12 | RAS parity error 中断 | Error injection 触发 parity error; 监测 `ctl_rasdp_irq` | parity error 汇聚到 ctl_rasdp_irq | REQ_RAS_2404 |
| INT-13 | Debug IO Group 选择 | 配置 `cfg_dbg_sel_group=0/1/2/3`; 读 PAD 输出 | 对应 group 的 256-bit diag_state_bus 被选通 | REQ_DEBUG_2304 |
| INT-14 | Debug IO bit 选择 | 固定 group 0; 依次配置 bit[0:7] = 0, 1, 2, ..., 255; 读 PAD | 每个 PAD 引脚输出对应 group 内选中的 bit 值 | REQ_DEBUG_2304 |
| INT-15 | PLL clock debug IO | 配置 debug IO 选择 PLL clock; 示波器测 PAD 输出 | 可见 2GHz/1GHz/125MHz/100MHz 时钟波形 | REQ_DEBUG_2302 |
| INT-16 | PHY 主要时钟 debug IO | 配置 debug IO 选择 PHY pipe_clk/core_clk; 示波器测 PAD | 可见对应时钟波形, 频率正确 | REQ_DEBUG_2303 |
| INT-17 | LTSSM state debug IO | 配置 debug IO 选择 LTSSM state bus; 监测 PAD 变化 | 状态变化时 PAD 对应 bit 翻转 | REQ_DEBUG_2304 |
| INT-18 | MSI/MSI-X 中断产生 | 触发硬件中断源 (如 DMA done); 监测 MSI/MSI-X TLP | TLP 正确发送, vector/address 匹配配置 | REQ_MSI_1001/1002/1003 |

### 5.3 中断观测矩阵

| 中断信号 | 位宽 | 类型 | 可注入? | Debug IO 可观测? | 状态寄存器? |
|----------|------|------|---------|-----------------|-------------|
| `irq_func` | 1 | Level, 汇聚 | Y (各源独立触发) | Y (group 选择) | Y |
| `irq_err` | 1 | Level, 汇聚 | Y (error injection) | Y (group 选择) | Y |
| `edma_int` | 32 | Level, 独立 | Y (DMA 完成) | N (32 根太多) | Y |
| `tbu_ras` | 1 | Level | Y (TBU error inject) | Y | Y |
| `tbu_pmu` | 1 | Level | Y (PMU counter) | Y | Y |
| `ctl_rasdp_irq` | 1 | (汇聚到 irq_err) | Y (parity inject) | Y | Y |
| `phy_sram_sec_irq` | 1 | (汇聚) | Y (ECC inject) | Y | Y |
| `phy_sram_ded_irq` | 1 | (汇聚) | Y (ECC inject) | Y | Y |
| `remap_err_irq` | 1 | (汇聚) | Y (非法地址) | Y | Y |

### 5.4 推荐新增 DFT 观测点

以下信号建议从 diag_state_bus 中导出或新增 debug group:

| 信号 | 用途 | 位宽 | 优先级 |
|------|------|------|--------|
| `pipe_rxelecidle[15:0]` | 观测 lane 电气 idle | 16 | 高 |
| `pipe_phystatus` | PIPE PHY 状态指示 | 1 | 高 |
| `mac_phy_powerdown[2:0]` | PHY 电源状态 | 3 | 中 |
| `pipe_rxvalid` | RX 时钟有效指示 | 1 | 高 |
| `cfg_phy_control` | PHY 控制总线 | 多位 | 中 |
| `app_ltssm_enable` | LTSSM 使能状态 | 1 | 高 |
| `HotResetBlock state` | Hot Reset 状态机 | 2-3 | 中 |
| `isolation_enable_status` | Isolation 实际状态 | 1 | 中 |
| `cr_para_sel_status` | CR/JTAG 选择状态 | 2 | 低 |

---

## 6. 集成测试序列 (Bring-up Flow)

### Phase 1: 基本连通性 (无 PHY, 无 Link)

1. 上电, 检查 isolation 默认状态 (ISO-01)
2. CR BUS 访问所有地址空间 (Top CFG, CRG, PHY0-3 CFG, MSIX2DBI, ReMap, DBI)
3. JTAG MUX 遍历 PHY0-3 + BD, 读 IDCODE (JTAG-01 ~ JTAG-05)
4. IJTAG 通路验证 (JTAG-06)
5. 关闭 isolation, 验证 AXI 通路 (ISO-02)

### Phase 2: PHY 初始化 (无远端)

1. PHY boot (ROM/SRAM/CR BUS 三种模式, PHY-04 ~ PHY-06)
2. PHY PLL lock 验证, 参考时钟验证 (PHY-07 ~ PHY-09)
3. PHY 内部 loopback PRBS (PHY-10)
4. BERT 测试各 PHY (JTAG-08 ~ JTAG-09, PHY-12)
5. JTAG→CR BUS 切换验证 (JTAG-07)

### Phase 3: Link Training (接远端或 Loopback)

1. 配置 app_ltssm_enable = 1
2. LTSSM debug shift_reg 监测建连过程 (LTSSM-01)
3. 验证所有 link/function/error 中断 (INT-06 ~ INT-07)
4. Debug IO 验证 (INT-13 ~ INT-17)
5. 数据通路 (DMA) 验证

### Phase 4: 异常场景

1. Hot Reset 中断序列 + LTSSM 捕获 (LTSSM-07, INT-08)
2. Link down 挂死场景 LTSSM 捕获 (LTSSM-06)
3. Error injection (Parity, SRAM ECC, AER) + 中断验证 (INT-12, PHY-14, PHY-15)
4. 低功耗进出 + LTSSM 捕获 (LTSSM-08)

### Phase 5: Isolation & Partial Good

1. 全部 ISO 测试项 (ISO-03 ~ ISO-11)
2. TSV level shifter + isolation (ISO-06, ISO-07)
3. Disable controller + PHY (ISO-09)

---

## 7. 需求覆盖矩阵

| 需求 | 覆盖测试项 |
|------|-----------|
| REQ_JTAG_1301 (5路 MUX) | JTAG-01 ~ JTAG-05, JTAG-11 |
| REQ_JTAG_1302 (TM[2:0]) | JTAG-01 ~ JTAG-05 |
| REQ_JTAG_1303 (PHY内部寄存器) | JTAG-01 ~ JTAG-04 |
| REQ_JTAG_1401 (IJTAG) | JTAG-06 |
| REQ_PHY_1211 (cr_para_sel) | JTAG-07, JTAG-06 |
| REQ_PHY_1210 (CR BUS SRAM) | PHY-01 ~ PHY-03 |
| REQ_PHY_1207-1209 (Boot) | PHY-04 ~ PHY-06 |
| REQ_PHY_1203-1205 (RefClk) | PHY-07 ~ PHY-09 |
| REQ_PHY_1215 (BERT) | JTAG-08, JTAG-09, PHY-12 |
| REQ_PHY_1213 (SRAM ECC) | PHY-14, PHY-15 |
| REQ_LOOPBACK_2201/2202 | PHY-10, PHY-11 |
| REQ_ISO_2001-2005 | ISO-01 ~ ISO-11 |
| REQ_DISABLE_2005 | ISO-09 |
| REQ_HotResetBlock_2101 | ISO-11 |
| REQ_DEBUG_2301 (LTSSM shift) | LTSSM-01 ~ LTSSM-10 |
| REQ_DEBUG_2302 (PLL debug IO) | INT-15 |
| REQ_DEBUG_2303 (PHY debug IO) | INT-16 |
| REQ_DEBUG_2304 (Controller debug IO) | INT-13, INT-14, INT-17 |
| REQ_IRQ_1701-1706 | INT-01 ~ INT-05 |
| REQ_IRQ_1706 (重要中断) | INT-06 ~ INT-12 |
| REQ_RAS_2401-2404 | INT-12, PHY-14, PHY-15 |
| REQ_MSI_1001-1004 | INT-18 |
| REQ_REVERSAL_1101-1105 | PHY-20 |
