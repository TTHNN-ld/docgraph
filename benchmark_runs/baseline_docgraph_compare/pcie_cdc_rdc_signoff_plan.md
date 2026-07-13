# PCIe 子系统 CDC/RDC Sign-Off 检查计划

> 基于文档: PCIe Subsystem Spec V3.21 (28 Jan 2026)，42 页
> 目标: 覆盖所有跨时钟域/跨复位域路径，列出 synchronizer/reset bridge 需求，评估各复位类型风险

---

## 1. 时钟域列表 (Clock Domain Inventory)

本子系统共识别出 **8 个独立时钟域**（不含 PHY 内部多级 PLL 派生时钟）。

| # | 时钟域名称 | 源 | 标称频率 | 用途 | 门控/低功耗行为 |
|---|---|---|---|---|---|
| CD1 | `core_clk` | PHY max_pclk → 控制器内部分频 | 125M/250M/500M/1GHz (Gen1~5) | Controller 逻辑主时钟，TLP 处理，LTSSM，DMA | L1 时停止（§4.4.1） |
| CD2 | `pipe_rx_clk` | PHY SerDes CDR 恢复 | Gen1~5 rate dependent | PIPE RX 接口接收数据捕获 | PHY P0/P0s/P1 受控 |
| CD3 | `aux_clk` | `clk_ref_clk` (外部 20MHz) | 20MHz | L2 低功耗存活逻辑；通过 GFM 与 core_clk 动态切换 | L1 Substate 时激活，Normal 时停 |
| CD4 | `axi_clk` | 本地 CRG PLL (ref=PHY 100MHz) | 1GHz (功能模式) / 2GHz (测试) | AXI Master/Slave, TBU, ReMap, HotResetBlock | 功能模式常开 |
| CD5 | `cr_para_clk` | 本地 CRG PLL | 125MHz | PHY CR 配置总线, CRG 配置, CFG_NOC 子模块配置 | 功能模式常开 |
| CD6 | `local_phy_ref_clk` | 本地 CRG PLL | 100MHz | PHY 备用参考时钟输入 | 常开 (PHY 未用时可选关) |
| CD7 | `pcie_cfg_clk` | SoC PLL 输入 | 250MHz | AXI-lite 配置 NoC, 子系统顶层 CSR, DBI 转换 | SoC 侧提供 |
| CD8 | `cfg_clk` | SoC 输入 (AXI DBI 专用) | 未明确标称 (推断 250MHz) | 控制器 DBI 配置接口 | SoC 侧提供 |
| CD_PHY | PHY 内部时钟 | 各 PHY 内置 PLL (PAD 100MHz ref) | 多级派生 | SerDes, PIPE TX/RX, PHY 状态机 | PHY P0/P0s/P1 受控 |

### 1.1 关键时钟关系说明

- `core_clk` ↔ `pipe_rx_clk`: **异步** (spec §4.4.1 明确标注)
- `core_clk` ↔ `aux_clk`: **异步**，通过 GFM (Glitch-Free Mux) 动态切换，**spec 明确写"无 CDC 处理"**（§4.4.1）— 高风险点
- `core_clk` ↔ `axi_clk`: **异步** (spec §4.4.1 明确标注 AMBA 时钟与 core 时钟异步)
- `axi_clk` ↔ `pcie_cfg_clk`: **异步** (来源不同: CRG PLL vs SoC PLL)
- `axi_clk` ↔ `cr_para_clk`: **同源** (均来自 CRG PLL)，但有分频关系，需验证 phase 关系
- PHY 内部时钟 ↔ controller 时钟: PIPE 接口处存在异步边界（SerDes PIPE 架构下 `max_pclk` 为固定 1GHz 输入）

---

## 2. 复位域列表 (Reset Domain Inventory)

| # | 复位信号 | 触发源 | 复位范围 | 类型 | 同步释放? |
|---|---|---|---|---|---|
| RD1 | `power_on_rst_n` | 外部 PAD | 全芯片 (含 PCIe 子系统全部) | Cold Reset | 是 (片上 POR 电路) |
| RD2 | `PERST#` | Host 边带信号 (PCIe connector) | **全芯片** (直接接 `power_on_rst_n`，spec §4.5.2) | Warm Reset → 实际 = Cold | 是 (等同于 Cold) |
| RD3 | `Hot Reset` (带内 TS1) | 上游 RC/Switch (连续 2 个 TS1 OS, Hot Reset bit=1) | **仅** HotResetBlock, TBU, ReMap; PCIe 子系统其余不复位 (§4.5.3, REQ_1603) | Hot Reset (带内) | N/A (部分逻辑复位) |
| RD4 | `Warm Reset` (SoC 级) | 全芯片 warm reset 控制器 | 全芯片 | Warm Reset | 是 |
| RD5 | `phy_laneX_reset_n` | CRG/控制器 | 每 lane PHY 逻辑 | Per-Lane PHY Reset | 是 |
| RD6 | `mstr_rst_n` | 子系统内部生成 | AXI Master NoC 接口逻辑 | 接口复位 | 是 |
| RD7 | `slv_rst_n` | 子系统内部生成 | AXI Slave NoC 接口逻辑 | 接口复位 | 是 |
| RD8 | `cfg_rst_n` | SoC 输入 | AXI DBI 配置接口 | 接口复位 | 是 |
| RD9 | `pll_rst_n` | SoC 输入 | CRG PLL | PLL 复位 | 是 |
| RD10 | `pipe_msgbus_rst_n` | 控制器/CRG | PIPE message bus (pipe_clk 域) | 接口复位 | 是 |
| RD11 | `pipe_rx_rst_n` | 控制器/CRG | PIPE RX (pipe_rx_clk 域) | 接口复位 | 是 |
| RD12 | `core_rst` (内部) | 控制器/CRG 生成 | controller core 逻辑 | 内部复位 | 是 |

### 2.1 复位域与时钟域映射

| 复位域 | 对应时钟域 | 备注 |
|---|---|---|
| RD1 (power_on_rst_n) | 所有 | 全局冷复位 |
| RD2 (PERST#) | 所有 | 等同于 RD1 |
| RD3 (Hot Reset) | CD4 (axi_clk) | 仅 HotResetBlock + TBU + ReMap |
| RD5 (phy_laneX_rst_n) | CD_PHY | per-lane |
| RD6 (mstr_rst_n) | CD4 (axi_clk) | AXI Master 侧 |
| RD7 (slv_rst_n) | CD4 (axi_clk) | AXI Slave 侧 |
| RD8 (cfg_rst_n) | CD7 (pcie_cfg_clk) | AXI DBI 侧 |
| RD9 (pll_rst_n) | CD4/5/6 (CRG 输出) | PLL 本身 |
| RD10 (pipe_msgbus_rst_n) | CD1 (core_clk) | PIPE message bus |
| RD11 (pipe_rx_rst_n) | CD2 (pipe_rx_clk) | PIPE RX |

---

## 3. 跨时钟域路径 (CDC Path Inventory)

### 3.1 数据路径 CDC

| # | 源时钟域 | 目的时钟域 | 路径描述 | 数据宽度 | 带宽需求 | 当前 CDC 方案 | 风险等级 |
|---|---|---|---|---|---|---|---|
| CDC-01 | CD2 (pipe_rx_clk) | CD1 (core_clk) | PIPE RX data → Controller 逻辑 | 40bit/lane × 16 lanes | Gen5 32GT/s | 需确认: async FIFO 或 valid-based CDC | **Critical** |
| CDC-02 | CD1 (core_clk) | CD2 (pipe_rx_clk) | Controller → PIPE TX data | 40bit/lane × 16 lanes | Gen5 32GT/s | PIPE 协议定义的 TX 侧同步 | **Critical** |
| CDC-03 | CD1 (core_clk) | CD4 (axi_clk) | Controller IB → AXI Master (Inbound TLP → NoC) | 512bit data + 48bit addr | ~32GB/s (x16 Gen5) | 需确认: async FIFO | **Critical** |
| CDC-04 | CD4 (axi_clk) | CD1 (core_clk) | AXI Slave → Controller OB (Outbound NoC → TLP) | 512bit data + 48bit addr | ~32GB/s | 需确认: async FIFO | **Critical** |
| CDC-05 | CD7 (pcie_cfg_clk) | CD4 (axi_clk) | AXI-lite 配置 → 内部 NoC → 各子模块 CFG | 32bit | 低 (<100MB/s) | 内部 CFG_NOC 应含 async bridge | High |
| CDC-06 | CD4 (axi_clk) | CD5 (cr_para_clk) | CFG_NOC → PHY CR bus | CR bus 位宽 | 低 | APB→CR 协议转换 + CDC | High |
| CDC-07 | CD8 (cfg_clk) | CD1 (core_clk) | AXI DBI → Controller CDM 寄存器 | 32bit | 低 | DBI slave 内部处理 | High |
| CDC-08 | CD1 (core_clk) | CD3 (aux_clk) | GFM 动态切换逻辑 | 控制信号 | 低 | **spec 声明"无 CDC 处理"** | **Critical** |
| CDC-09 | CD4 (axi_clk) | CD_PHY | CR bus → PHY 内部寄存器/SRAM | CR bus | 低 | PHY CR 接口内部同步 | Medium |

### 3.2 控制/状态信号 CDC

| # | 源时钟域 | 目的时钟域 | 信号 | 风险等级 |
|---|---|---|---|---|
| CDC-10 | CD1 (core_clk) | CD4 (axi_clk) | Hot Reset 标志 → HotResetBlock | High |
| CDC-11 | CD1 (core_clk) | CD4 (axi_clk) | `app_ltssm_enable` (LTSSM 使能控制) | High |
| CDC-12 | CD1 (core_clk) | — (multi-dest) | `phy_pll_lock_int`, `phy_pll_unlock_int` → 中断汇聚 | Medium |
| CDC-13 | CD_PHY | CD1 (core_clk) | `phy_mac_phystatus`, `phy_mac_rxvalid`, `phy_mac_rxelecidle`, `phy_mac_rxstatus` | High |
| CDC-14 | CD1 (core_clk) | CD_PHY | `mac_phy_powerdown`, `mac_phy_txelecidle`, `mac_phy_rate` | High |
| CDC-15 | CD4 (axi_clk) | CD5 (cr_para_clk) | PHY CR 配置寄存器读写 | Medium |
| CDC-16 | CD1 (core_clk) | SoC 中断域 | 所有 `irq_src` → `irq_func`, `irq_err`, `edma_int[31:0]` | Medium |
| CDC-17 | CD4 (axi_clk) | CD2 (pipe_rx_clk) | `pipe_msgbus_rst_n` (复位跨域) | High |
| CDC-18 | CD1 (core_clk) | CD4 (axi_clk) | IB `ib_rreq_c2a_cdc_ram`*, `ib_wreq_c2a_cdc_ram`*, `ib_mcpl_a2c_cdc_ram`* | **Critical** |

> * 注: spec §4.11 parity 信号列表中存在 `ib_rreq_c2a_cdc_ram` / `ib_wreq_c2a_cdc_ram` / `ib_mcpl_a2c_cdc_ram`，命名中包含 "cdc_ram"，证明确实存在 core↔AXI 时钟域跨域 RAM，需重点检查 CDC 电路。

### 3.3 Clock Gating 相关 CDC 风险

| # | 场景 | 风险 |
|---|---|---|
| CG-01 | L1 时 `core_clk` / `pipe_clk` 停止 (§REQ_902) | 停止域中未完成的总线事务、FIFO 状态丢失 |
| CG-02 | L1 Substate 时 PHY 参考时钟移除 (§REQ_903) | PHY 内部状态丢失，恢复后需重新初始化 |
| CG-03 | Isolation 状态下 AXI 时钟可能停振 (20MHz 生存时钟) | 接口信号需 isolation clamp，防止 X 态传播 |

---

## 4. Synchronizer / Reset Bridge 需求

### 4.1 两级/多级同步器 (2-FF Synchronizer) 需求

以下控制信号/状态信号必须使用标准 2-FF synchronizer（或等效）:

| 信号 | 源域 | 目的域 | MTBF 要求 | 备注 |
|---|---|---|---|---|
| Hot Reset 标志 | CD1 | CD4 | > 10 年 | 带内触发，软件可干预 |
| `app_ltssm_enable` | SoC (CD7) | CD1 | > 10 年 | 建连关键信号 |
| `freeze_reg` CDC (LTSSM debug) | CD7 (APB) | CD1 | 标准 | spec §4.13.2 描述上升沿检测 + 脉冲同步 |
| 所有中断信号 (26+ function/error interrupts) | CD1 | SoC 中断域 (CD7) | > 10 年 | level 中断，需 pulse→level 转换 + 2-FF sync |
| `phy_mac_phystatus` | CD_PHY | CD1 | > 10 年 | PIPE 状态指示 |
| `phy_mac_rxvalid` | CD_PHY | CD1 | > 10 年 | 指示 pipe_rx_clk stable |
| `mac_phy_powerdown` | CD1 | CD_PHY | > 10 年 | 低功耗状态控制 |
| PHY PLL lock/unlock | CD_PHY | CD1 | 标准 | 中断上报用 |

### 4.2 Async FIFO 需求

| 路径 | 源域 → 目的域 | FIFO 深度需求 | 备注 |
|---|---|---|---|
| AXI Master IB (TLP→NoC) | CD1 → CD4 | 需根据 Max Outstanding (256 IDs) × Max Payload (256B) = >64KB 评估 | `ib_rreq_c2a_cdc_ram` / `ib_wreq_c2a_cdc_ram` 即为此 CDC FIFO |
| AXI Slave OB (NoC→TLP) | CD4 → CD1 | 同上规模 | OB 方向对应的 CDC FIFO |
| PIPE RX Data | CD2 → CD1 | PIPE 协议定义 | elastic buffer |

### 4.3 Reset Bridge 需求

| 复位桥接 | 源复位域 | 目的复位域 | 类型 | 需求 |
|---|---|---|---|---|
| RB-01 | RD1 (power_on_rst_n) | 所有时钟域 | 异步复位同步释放 | 每个时钟域独立 reset synchronizer |
| RB-02 | RD2 (PERST#) | 所有 | 同 RD1 (PERST# 接 power_on_rst_n) | 见风险分析 §5.1 |
| RB-03 | RD3 (Hot Reset) | CD4 (axi_clk) | 部分复位同步释放 | HotResetBlock/TBU/ReMap 需独立 reset bridge |
| RB-04 | RD5 (phy_laneX_rst_n) | CD_PHY (per-lane) | 异步复位同步释放 | 每个 lane 独立 reset bridge |
| RB-05 | RD10 (pipe_msgbus_rst_n) | CD1 (core_clk) | 跨域复位桥接 | PIPE 协议定义 |
| RB-06 | RD11 (pipe_rx_rst_n) | CD2 (pipe_rx_clk) | 同域同步释放 | PIPE 协议定义 |

---

## 5. 各复位类型风险分析

### 5.1 PERST# (Warm Reset) — 风险等级: **High**

**问题**: spec §4.5.2 明确写"芯片需要将 PERST# 直接接到硬件冷复位 power_on_rst_n 对全芯片进行复位"。

**风险**:
1. **语义降级**: PCIe 协议定义 PERST# 为 Warm Reset，本应仅复位 PCIe 相关逻辑；实际实现等同于 Cold Reset，**丢失 Warm Reset 语义**，无法保留部分 SoC 状态
2. **恢复时间**: 全芯片冷复位 → 重新 boot → 重新 link training，远超 PCIe spec 定义的 100ms LTSSM 恢复时间
3. **L2/L3 唤醒路径**: 若从 L2/L3 唤醒依赖 PERST# 退出，全芯片复位将丢失唤醒上下文
4. **Inbound 数据丢失**: 正在进行中的 DMA、未完成的 TLP 全部丢失

**建议**:
- 确认 SoC 架构是否允许 PERST# 仅复位 PCIe 子系统而非全芯片
- 如果无法修改，需在 sign-off checklist 中明确此为"informed deviation"
- 评估对 PCI-SIG compliance test 中 PERST# 测试项的影响

### 5.2 Hot Reset (带内 TS1) — 风险等级: **Critical**

**问题**: spec §4.5.3 定义 Hot Reset 仅复位 HotResetBlock、TBU、ReMap，**PCIe 控制器和 PHY 不复位**。

**风险**:
1. **部分复位一致性**: 控制器状态机 (LTSSM、DLC、TL) 不复位，但下游 AXI 数据通路 (HotResetBlock) 被复位 — 两者状态不一致可能导致协议错乱
2. **Flush 时序依赖**: Hot Reset 流程: 接收 TS1 → 拉低 `app_ltssm_enable` → flush timer (最长 134ms) → flush done → SoC 复位 HotResetBlock/TBU → SoC 重新配置 → 拉高 LTSSM enable。任何环节超时/卡死都会导致无法恢复
3. **AXI Blocking 死锁**: HotResetBlock 截获新 AXI 访问等 flush 完成，若 flush 本身因 NoC 反压无法完成 → 死锁（spec §4.5.3.1）
4. **TBU 复位副作用**: TBU 含 DTI 接口接 TCU，Hot Reset 复位 TBU 但不复位 TCU → DTI 协议状态错位
5. **中断丢失**: Hot Reset 期间产生的中断（如 `phy_pll_unlock_int`, `link_down_event_int`）可能因复位而丢失
6. **Spec 声明"HotResetBlock 不能主动退出 Hot Reset 状态"** (§4.5.3.1) — 必须依赖 SoC reset

**建议**:
- 增加 Hot Reset 恢复超时 watchdog（SoC 侧 >134ms 超时触发 SoC 级复位）
- 验证所有 Hot Reset 期间的 CDC 路径（Hot Reset 标志从 CD1→CD4，app_ltssm_enable 从 SoC→CD1）
- 门级仿真覆盖 Hot Reset + 正在进行的 AXI burst + DMA 传输组合场景

### 5.3 Warm Reset (全芯片) — 风险等级: **Medium**

**问题**: spec §4.5.2 提 Warm Reset 但未详述与 PERST# 的关系；REQ_1605 声明 "Warm reset 会对全芯片进行 Reset"。

**风险**:
1. 全芯片 warm reset 序列与 PCIe controller flush 需求的协调（controller 内部可能需要先 flush 再 reset）
2. Warm reset 释放后，PHY 需要重新初始化 (boot FW load)，Controller 需要重新配置 → link training 延迟
3. PCIe 配置空间在 warm reset 后是否保留？spec 未明确

**建议**:
- 区分 SoC warm reset 与 PCIe warm reset (PERST#) 的行为差异
- 明确 warm reset 后哪些寄存器需要软件恢复、哪些由硬件恢复

### 5.4 Cold Reset (power_on_rst_n) — 风险等级: **Medium**

**风险**:
1. PHY SRAM 上电初始化和 FW boot (spec §4.1.2 PHY boot 模式 1/2/3)
2. PHY boot 模式 3 (bypass ROM, 从 CR bus load FW): 如果 CR bus 在 cold reset 后未就绪 → PHY 无法初始化 → link training 无法开始
3. PLL lock 时间: cold reset 释放到 PLL stable 的时间需纳入上电时序预算
4. Cold reset 去抖动: power_on_rst_n 来自 PAD，需要内部去抖

### 5.5 FLR (Function Level Reset) — 风险等级: **N/A**

Spec REQ_1606 明确**不支持 FLR**，无需检查。

### 5.6 Link Disable — 风险等级: **Medium**

Spec REQ_1604: "Link Disable 等同于 HotReset 处理"。风险集合与 Hot Reset 相同，额外风险:
- Link Disable 可由本地软件触发（非上游带内触发），触发路径不同，CDC 需求相同

---

## 6. 复位序列交互矩阵

|  | 进入 Cold (RD1) | 进入 PERST# (RD2) | 进入 Hot Reset (RD3) | 进入 Warm (RD4) |
|---|---|---|---|---|
| **从 L0 (正常)** | OK | OK (=Cold) | 需 flush→block→SoC reset | OK |
| **从 L1 (ASPM/PM)** | PHY P1→复位 | 同 Cold | controller 在 L1 时需先唤醒再 flush? | 需验证 core_clk 停止时的复位行为 |
| **从 L1 Substate** | ref clk removed, PHY 初始化 | 同 Cold | 需先恢复参考时钟 | 需先恢复参考时钟 |
| **从 Hot Reset 中** | OK (全复位覆盖) | OK | 嵌套 Hot Reset? spec 未描述 | 需确认优先级 |
| **从 Isolation** | OK | OK | Isolation + Hot Reset 双重状态 | Isolation enable→强制 firewall mode (§REQ_2101) |

---

## 7. Sign-Off Checklist

### 7.1 设计检查 (Pre-Synthesis)

- [ ] **CDC-01~09**: 每条数据路径 CDC 是否有对应的 async FIFO / handshake 电路？
  - [ ] 重点: `ib_rreq_c2a_cdc_ram` / `ib_wreq_c2a_cdc_ram` / `ib_mcpl_a2c_cdc_ram` 是否用标准 dual-clock RAM + gray-code 指针？
- [ ] **CDC-10~18**: 每条控制信号 CDC 是否有 2-FF synchronizer + 复位脉冲展宽？
- [ ] **CDC-08**: core_clk ↔ aux_clk GFM 切换，"无 CDC 处理"是否可接受？是否经过 glitch-free 验证？
- [ ] **RB-01~06**: 每个时钟域是否有独立的 reset synchronizer？
- [ ] **RB-03**: Hot Reset 路径的 reset bridge 是否正确处理部分复位？
- [ ] **PERST# = Cold Reset**: 是否记录为 informed deviation？
- [ ] **Hot Reset Flow**: 是否有完整的 flush→block→SoC reset→reconfig→LTSSM enable 超时保护？
- [ ] PHY boot mode 3 (CR bus load FW) 在 cold reset 后的初始化时序是否满足？
- [ ] Isolation 时 AXI 接口 clamp 值是否正确（防止 X 态传播到 NoC）？
- [ ] Parity 信号跨域: spec §4.11 列出的所有 parity check 信号，跨域者是否有正确处理？

### 7.2 静态验证 (SpyGlass/Questa CDC)

- [ ] CDC 静态检查全通过（无 waiver 需评审）
  - [ ] Waiver 重点: core↔AXI async FIFO 的 gray-code 指针
  - [ ] Waiver 重点: GFM 切换逻辑（可能报 CDC 路径但实际有 glitch-free 保护）
- [ ] RDC 静态检查全通过
  - [ ] Waiver 重点: Hot Reset 部分复位 — 不复位的域对已复位域的信号是否被正确处理
- [ ] 所有 CDC/RDC violation 有对应的 waiver justification

### 7.3 动态仿真 (Gate-Level Simulation)

- [ ] **Test CDC-01**: PIPE RX data 跨域 — 注入 `pipe_rx_clk` jitter/频率变化，验证 async FIFO 无 overflow/underflow
- [ ] **Test CDC-03/04**: AXI 数据跨域 — 满带宽 IB+OB 同时传输 + 随机反压，验证 CDC FIFO 深度足够
- [ ] **Test CDC-08**: core_clk ↔ aux_clk GFM 切换 — 在切换窗口注入 glitch，验证无亚稳态传播
- [ ] **Test Hot Reset**: L0 状态下注入连续 TS1 (Hot Reset=1) + AXI burst 未完成 + DMA 传输中 — 验证 flush 完成且不死锁
- [ ] **Test PERST#**: L0 状态下拉低 PERST# → 全芯片复位 → 释放 → PHY boot → link training 完整序列
- [ ] **Test Warm Reset**: 验证与 Cold Reset 时序差异
- [ ] **Test L1 Entry/Exit**: core_clk 停止/恢复 + AXI 接口行为
- [ ] **Test L1 Substate Entry/Exit**: PHY ref clock 移除/恢复 + PHY 重新初始化
- [ ] **Test Isolation Entry/Exit**: 时钟可能停止的情况下接口行为
- [ ] **Test Hot Reset + L1 组合**: 在 L1 状态下接收 Hot Reset

### 7.4 时序收敛 (STA)

- [ ] 所有 async FIFO 的 gray-code 指针跨域路径是否有 max_delay / set_data_check 约束？
- [ ] 所有 2-FF synchronizer 的 MTBF 是否满足 > 10 年（考虑工艺、电压、温度）？
- [ ] `phy_laneX_reset_n` 到各 lane PHY 逻辑的复位释放 timing check
- [ ] PLL lock 信号到各时钟域的 CDC timing

---

## 8. 附录: 关键 spec 引用

| 条款 | 内容摘要 |
|---|---|
| REQ_901 | L0s 支持 |
| REQ_902 | ASPM L1 / PM L1 — 停 core_clk/pipe_clk |
| REQ_903 | L1 Substate — 停 PHY 参考时钟 |
| REQ_1603 | Hot Reset 仅复位 HotResetBlock, TBU, ReMap |
| REQ_1604 | Link Disable = HotReset |
| REQ_1605 | Warm Reset = 全芯片复位 |
| REQ_1606 | FLR 不支持 |
| REQ_2002/2003 | Isolation 行为要求 |
| REQ_2101 | HotResetBlock: bypass/firewall/Hot Reset 三种模式 |
| §4.4.1 | core_clk↔pipe_rx_clk 异步；core_clk↔aux_clk 异步且无 CDC；AMBA↔core 异步 |
| §4.5.2 | PERST# 接 power_on_rst_n 做全芯片复位 |
| §4.5.3 | Hot Reset 流程详述 |
| §4.13.2 | LTSSM debug shifter freeze_reg CDC（core_clk 域同步） |
| §4.11 | Parity 信号列表 — 暴露 cdc_ram 存在证据 |
