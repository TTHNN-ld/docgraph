# PCIe Subsystem P&R / Floorplan Integration Checklist

> 来源: PCIe Subsystem Spec v3.21 (2026-01-28), 42 pages
> 目标: physical integration owner 签核用, 覆盖 PCIe core / UPCS PIPE / PHY0-3 / CRG / NoC/AXI / JTAG / TX-RX lane 物理边界与风险

---

## 1. PCIe Core (Controller) 物理边界

### 1.1 接口信号物理约束

| # | 检查项 | 规格 | 风险等级 |
|---|--------|------|---------|
| 1.1.1 | PIPE 5.1.1 → UPCS: 40bit/lane × 16 lanes, 共 640bit TXD + 640bit RXD, lane 位宽 40bit (§1.1-103) | 需确认 die 内走线长度匹配, 同层 metal | **高** |
| 1.1.2 | AXI4 Master (IB) 512b data + 48b addr → Main NoC (§1.5-508) | 512b 位宽在 1GHz 下需 timing closure | **高** |
| 1.1.3 | AXI4 Slave (OB) 512b data + 48b addr ← Main NoC (§1.5-501) | 同上, 且 OB 支持 WRAP burst (§1.5-505) | **高** |
| 1.1.4 | AXI-lite 32b CFG ← SoC NoC, 内部转 9 路配置总线 (§3.1) | 确认 CFG_NOC 的 floorplan 位置 | 中 |
| 1.1.5 | 中断输出: edma_int[31:0] + irq_func + irq_err + tbu_ras + tbu_pmu → SoC (§4.6.1) | 36 根 level 中断线, 避免与 NoC 数据线交叉串扰 | 中 |
| 1.1.6 | DTI 接口 → TCU (TBU 内置, SMMU 功能) (§3.1) | 确认 TCU 物理位置, DTI 走线延迟 | 中 |

### 1.2 时钟域边界

| # | 检查项 | 规格 | 风险等级 |
|---|--------|------|---------|
| 1.2.1 | core_clk ← lane0 max_pclk 1GHz (§4.4.1) | 固定 1GHz, 控制器内部按 link rate 分频 | **高** |
| 1.2.2 | pipe_rx_clk (恢复时钟) ← UPCS (§4.9) | 与 core_clk **异步**, CDC 在 controller 内 | **高** |
| 1.2.3 | AMBA 时钟 (axi_clk 1GHz) 与 core_clk 异步 (§4.4.1) | CDC crossing 需确认同步器位置 | **高** |
| 1.2.4 | pcie_cfg_clk 250MHz ← SoC (§4.4.1) | 独立时钟域, NoC + 系统寄存器 | 中 |
| 1.2.5 | aux_clk ← clk_ref_clk 20MHz (§4.4.1), 用于 L2 低功耗 | core_clk ↔ aux_clk 通过 GFM 动态切换, **无 CDC 处理** (§4.4.1) | **高** |

### 1.3 复位边界

| # | 检查项 | 规格 | 风险等级 |
|---|--------|------|---------|
| 1.3.1 | PERST# 暖复位: 接 power_on_rst_n 对全芯片复位 (§4.5.2) | 确认 PERST# PAD 到 controller 的复位树延迟 | **高** |
| 1.3.2 | Hot Reset: controller flush 后 assert core_rst_n, SoC watchdog 触发 reset (§4.5.3.4) | HotResetBlock 在 Hot Reset 期间需单独复位以退出状态 | 中 |
| 1.3.3 | phy_laneX_reset_n: 前级 OR 改为 AND (§V3.02 doc history) | 确认复位门逻辑在 P&R 后的物理位置 | 中 |

---

## 2. UPCS PIPE 物理边界

| # | 检查项 | 规格 | 风险等级 |
|---|--------|------|---------|
| 2.1 | SerDes PIPE → Controller: 16 lanes × 40bit = 640bit 并行数据, 1GHz | 与 controller PIPE 接口对齐, 同 die 边 | **高** |
| 2.2 | SerDes PIPE ← PHY0-3: 4×PHY, 每 PHY 4 lanes × 40bit = 160bit | UPCS 到每 PHY 的距离差异影响 skew | **高** |
| 2.3 | max_pclk 输出: 16 lane 各 1GHz → controller core_clk 生成 | 确认 lane0 固定 1GHz max pclk (§1.15-1506) | **高** |
| 2.4 | pipe_rx_clk: 从 RX 数据流恢复 → controller PIPE RX | 恢复时钟质量依赖 SI | **高** |
| 2.5 | RX 方向异步 CDC: UPCS 内部处理 SerDes → PIPE 位宽转换 + 异步处理 (§3.1 UPCS) | CDC FIFO 深度需满足 latency 要求 | 中 |

---

## 3. PHY0-3 物理边界

### 3.1 总体布局

| # | 检查项 | 规格 | 风险等级 |
|---|--------|------|---------|
| 3.1.1 | 4 × X4 PHY = X16 total (§1.12-1201) | PHY0-3 沿 die 边缘一字排列, lane 序列对齐 | **高** |
| 3.1.2 | lane 可配 x1/x2/x4/x8/x16 (§1.12-1202) | lane reversal/flip 逻辑需验证物理 lane 顺序 | 中 |

### 3.2 参考时钟路径

| # | 检查项 | 规格 | 风险等级 |
|---|--------|------|---------|
| 3.2.1 | 模式1: PHY0 PAD 100MHz 差分输入 → PHY1/2/3 菊花链 (§1.12-1203) | daisy-chain 路径每级时钟抖动累加, 确认 jitter budget | **高** |
| 3.2.2 | 模式2: 4 PHY 各从 PAD 独立 100MHz 差分, 同源 (§1.12-1204) | 确保 4 路差分对的 skew < 50ps | **高** |
| 3.2.3 | 模式3: PHY0 ← Local PLL, PHY1-3 菊花链 (§1.12-1205) | Local PLL → PHY0 走线需隔离数字噪声 | 中 |
| 3.2.4 | PHY0-3 各 PAD_REF_CLK_N/P IO 对 (§2 Table 2-1) | 差分对 pad 位置靠近各自 PHY, 避免穿越数字区 | **高** |

### 3.3 串行数据 PHY 边界

| # | 检查项 | 规格 | 风险等级 |
|---|--------|------|---------|
| 3.3.1 | 16 TXx_P/N 差分输出 IO (§2 Table 2-1) | pad 间距满足串扰指标, 避免邻近 aggressor | **高** |
| 3.3.2 | 16 RXx_P/N 差分输入 IO (§2 Table 2-1) | RX 敏感, 远离 TX 和数字噪声源 | **高** |
| 3.3.3 | PHY0/1/2/3 各 RESREF IO (§2 Table 2-1) | 200Ω 精度电阻靠近各自 PHY, 避免与数字地耦合 | 中 |

### 3.4 PHY 供电

| # | 检查项 | 规格 | 风险等级 |
|---|--------|------|---------|
| 3.4.1 | 每 PHY 独立供电域 | PHY 不工作时可断电 (§1.20-2006) | 中 |
| 3.4.2 | PHY power state: P0, P0s, P1 (§1.9-905) | P1 时停参考时钟, 电源轨切换无 glitch | 中 |

### 3.5 PHY SRAM

| # | 检查项 | 规格 | 风险等级 |
|---|--------|------|---------|
| 3.5.1 | SRAM ECC: single/double bit error 上报中断 (§1.12-1213) | SRAM 物理位置需在 PHY 内部, 避免长走线 | 中 |

---

## 4. CRG (Clock & Reset Generator) 物理边界

| # | 检查项 | 规格 | 风险等级 |
|---|--------|------|---------|
| 4.1 | PLL 功能模式 1GHz, 测试模式 2GHz (§1.15-1501) | PLL 物理隔离 (guard ring, deep N-well) | **高** |
| 4.2 | PLL 参考时钟源选择: (a) PHY mplla 100MHz, (b) PHY mpllb 100MHz, (c) clk_ref_clk 20MHz (§4.4.1) | MUX 前需要 glitch-free 切换逻辑 | 中 |
| 4.3 | pll_rst_n 外部复位输入 (§2 Table 2-1) | 复位 de-assert 需等 PLL lock 稳定 | 中 |
| 4.4 | 输出时钟: axi_clk 1GHz, cr_para_clk 125MHz, local_phy_ref_clk 100MHz (§1.15-1504) | 三路时钟 fanout 的 CTS (clock tree synthesis) 平衡 | **高** |
| 4.5 | 测试输出: clk_test0 2GHz, clk_test1 1GHz, clk_test2 125MHz, clk_test3 100MHz (§1.15-1505) | 测试时钟拉到 debug IO, 走线屏蔽 | 低 |
| 4.6 | CRG 不在 partial good 域内 (§4.19) | CRG 必须保持常电, isolation 时不能断时钟 | 中 |

---

## 5. NoC / AXI 物理边界

### 5.1 Main NoC 接口

| # | 检查项 | 规格 | 风险等级 |
|---|--------|------|---------|
| 5.1.1 | AXI Master (IB): 512b × 48b addr, INCR only, 256 IDs, MTU 256 (§1.5) | 512b @ 1GHz → 512 Gbps 带宽, 需时序收敛 | **高** |
| 5.1.2 | AXI Slave (OB): 512b × 48b addr, INCR+WRAP, 256 tags, MTU 256 (§1.5) | WSTRB 零字节支持 / NCBE 支持增加 datapath 复杂度 | **高** |
| 5.1.3 | Master 侧 W channel 早于 AW channel (§1.5-512) | 不影响物理, 但验证需覆盖此 ordering | 低 |
| 5.1.4 | QoS/User 信号静态配置 (§1.5-515) | NoC 侧 QoS 域段匹配 | 低 |

### 5.2 CFG NoC 内部

| # | 检查项 | 规格 | 风险等级 |
|---|--------|------|---------|
| 5.2.1 | AXI-lite → 9 子模块配置总线 (§3.1) | 地址映射: Top CFG / CRG / PHY0-3 / MSIX2DBI / ReMap / DBI (§4.2 Table 4-2) | 中 |
| 5.2.2 | Reserved 地址访问不能挂死 (§1.5-517) | 确认 NoC default slave 的 error response | 中 |
| 5.2.3 | CFG NoC 不在 partial good 域 (§4.19) | 隔离时 CFG NoC 仍需响应访问 | 中 |

### 5.3 Isolation 物理约束

| # | 检查项 | 规格 | 风险等级 |
|---|--------|------|---------|
| 5.3.1 | isolation enable 时 AXI Slave 不 hang 死 MainNoC (§1.20-2003) | 需 isolation cell 在 AXI 接口上的正确插入 | **高** |
| 5.3.2 | AXI Master isolation 时不主动发起访问 (§1.20-2003) | 输出 isolation clamp 值 (0 或 1) 确认 | 中 |
| 5.3.3 | TSV 直穿 port 上有 isolation + level shifter (§1.20-2004) | 默认 isolation enable, 寄存器可配 | 中 |
| 5.3.4 | MainNoC 无时钟时 AXI 接口行为 (§1.20-2003 Note) | 20MHz 时钟存在时数据通路正常, 可 soft reset | 中 |

---

## 6. JTAG / IJTAG 物理边界

| # | 检查项 | 规格 | 风险等级 |
|---|--------|------|---------|
| 6.1 | 5 路 JTAG (PHY0-3 + BD) MUX → TSV JTAG PAD (§1.13-1301) | MUX 位置靠近 TSV port, 选择信号 TM[2:0] 走线 | 中 |
| 6.2 | cr_para_sel[1:0] TDR 寄存器控制 DFT 选择 (§1.12-1211) | 默认 2'b01 = CR BUS; 2'b10 = IJTAG DFT | 中 |
| 6.3 | IJTAG → SoC DFT 控制器 (§1.14-1401) | DFT 控制器物理位置, IJTAG 链走线 | 低 |
| 6.4 | JTAG decoder 框图已添加 (v3.20) | JTAG decoder 位置在 BD 通路还是 PCIe 子系统内 | 低 |

---

## 7. TX/RX Lane 物理边界汇总

### 7.1 串行信号完整性

| # | 检查项 | 规格 | 风险等级 |
|---|--------|------|---------|
| 7.1.1 | 16 TX 差分对: Gen5 32GT/s, 阻抗 85Ω/100Ω | pad → bump → package → PCB 全程阻抗控制 | **高** |
| 7.1.2 | 16 RX 差分对: 接收均衡 + CDR | RX 输入 eye diagram 满足 Gen5 margin | **高** |
| 7.1.3 | TX ↔ RX 串扰: 同 lane 的 TX/RX 在 die 内物理分离 | 避免近端串扰 (NEXT) 恶化 RX 灵敏度 | **高** |
| 7.1.4 | 相邻 lane 间 TX-to-RX 串扰 | lane pitch 满足 Gen5 串扰隔离要求 | **高** |

### 7.2 Lane 排列与方向

| # | 检查项 | 规格 | 风险等级 |
|---|--------|------|---------|
| 7.2.1 | Auto Lane Reversal (§1.11-1101) | 物理 lane 顺序与逻辑 lane 号的映射表 | 中 |
| 7.2.2 | Full Reversal + Flip (§1.11-1102) | 验证所有 reversal/flip 组合下 lane 连接正确性 | 中 |
| 7.2.3 | Partial Reversal + Flip (§1.11-1105, v3.21 新增) | 仅部分 lane 的 reversal/flip 配置 | 中 |
| 7.2.4 | Lane broken 时外部寄存器使能 flip (§1.11-1103) | 冗余 lane 的物理位置考虑 | 低 |

### 7.3 Loopback 测试

| # | 检查项 | 规格 | 风险等级 |
|---|--------|------|---------|
| 7.3.1 | Controller 内部 loopback (§1.22-2201) | 数字回路, 不涉及 PHY | 低 |
| 7.3.2 | PHY 内部 serial loopback (§1.22-2202) | 需要 PHY 内部 mux 逻辑, 芯片内可测 | 中 |
| 7.3.3 | PHY 外部 PAD loopback (§1.22-2202) | 需要 PCB 测试点或 external loopback 夹具 | 中 |
| 7.3.4 | BERT + Error Inject (§1.12-1215) | JTAG/IJTAG 控制, BERT 测试走线不引入额外 jitter | 中 |

---

## 8. 跨模块物理集成风险汇总

### 8.1 关键时序路径

| 路径 | 源 | 目标 | 频率 | 风险 |
|------|-----|------|------|------|
| PIPE TX | Controller | UPCS → PHY | 1GHz | 640bit 并行数据 + 控制, 跨模块 skew |
| PIPE RX | PHY → UPCS | Controller | 1GHz (恢复) | CDC 异步, FIFO 深度 |
| AXI Master Read | Controller | Main NoC | 1GHz | 512b datapath timing |
| AXI Slave Write | Main NoC | Controller | 1GHz | 512b datapath timing |
| max_pclk | PHY lane0 | CRG → Controller | 1GHz | 时钟树平衡 |
| REFCLK daisy | PHY0 → PHY1 → PHY2 → PHY3 |  | 100MHz | 级联 jitter 累加 |
| axi_clk | CRG PLL | AXI/NoC/HotReset/Remap | 1GHz | CTS 平衡 |

### 8.2 供电域与 isolation

| 域 | 可独立断电 | isolation 控制 | 备注 |
|----|-----------|---------------|------|
| PHY0-3 | 是 (PCB 断电) | isolation enable 默认 1 | PCIe 子系统顶层 isolation 控制寄存器 |
| PCIe core (pcie_core) | 否 (partial good) | partial good isolation | 不包括 CFG NoC / HotReset / TBU / CRG |
| CRG | 否 | 不在 partial good 域 | 常电 |
| CFG NoC | 否 | 不在 partial good 域 | 常电 |
| HotResetBlock | 否 | 不在 partial good 域 | Hot Reset 时需单独复位 |

### 8.3 PAD 物理总结

| PAD | 方向 | 数量 | 备注 |
|-----|------|------|------|
| TXx_P/N | Out | 16 对 | 差分串行输出, Gen5 32GT/s |
| RXx_P/N | In | 16 对 | 差分串行输入, Gen5 32GT/s |
| PHY[0:3]_RESREF | IO | 4 | 200Ω 精度参考电阻 |
| PHY[0:3]_PAD_REF_CLK_N/P | IO | 4 对 | 100MHz 差分参考时钟输入 |
| PERST# | In | 1 | Warm reset |
| CLKREQ# | IO | 1 | L1 sub-state 时钟控制 |
| WAKE# | IO | 1 | L2/L3 唤醒 |
| JTAG (TSV) | IO | 5 组 MUX → 1 组 PAD | 复用 TSV JTAG PAD |
| Debug IO | Out | 8 | PLL/PHY/Controller 状态输出 |

---

## 9. 签核前必查 10 条

1. [ ] **PHY0-3 沿 die 边缘一字排列**, lane 0-15 从左到右顺序, 差分对 pad 间距 ≥ spec
2. [ ] **参考时钟菊花链**: PHY0 ← PAD → PHY1 → PHY2 → PHY3, 每级 jitter 累加 < 0.1UI
3. [ ] **1GHz PIPE 640bit 数据总线**: 所有 16 lane 的 TXD/RXD 走线延迟差 < 1/4 周期 (250ps)
4. [ ] **core_clk ↔ pipe_rx_clk 异步 CDC**: controller 内同步器已实现, STA 确认无 CDC 违例
5. [ ] **AXI 512b @ 1GHz timing closure**: Master 和 Slave 方向均需在 target corner 下收敛
6. [ ] **isolation cells 正确插入**: AXI Master/Slave/TSV port 上 isolation clamp 值确认
7. [ ] **CRG PLL 物理隔离**: guard ring + 独立供电, 远离 PHY 串行 TX
8. [ ] **PERST# 复位树**: 从 PAD → controller → 全芯片 power_on_rst_n, 延迟 < 10ns
9. [ ] **JTAG MUX 选择 TM[2:0]**: 默认值确保 CR bus 可访问 PHY 寄存器
10. [ ] **TX/RX 通道间串扰**: Gen5 32GT/s 下 NEXT/FEXT < -40dB @ 16GHz
