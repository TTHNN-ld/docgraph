# PCIe Subsystem Clock/Reset Verification Plan

> 基于 `pcie_spec_text.txt` (v3.21, 28 Jan 2026)，面向 DV (Design Verification) 使用。
> 每条验证项标注对应的 Spec 页码和需求 ID。

---

## 1. Clock Source & PLL/CRG/GFM/DIV/MUX 验证

### 1.1 Clock Source 拓扑

Spec 定义 3 类时钟源 (p.22 §4.4.1)：

| 时钟源 | 来源 | 用途 | 页码 |
|--------|------|------|------|
| SoC PLL | 芯片外部 | pcie_cfg_clk @ **250MHz**，供 NoC 和系统寄存器 | p.22 L539 |
| PCIe 本地 PLL (CRG) | PHY ref 或 PAD | AMBA 总线时钟 (axi_clk, cr_clk, local_ref_clk) | p.22 L515, L533 |
| PHY 内置 PLL | PAD 差分时钟 | PHY, PIPE, Controller 主时钟 (core_clk, pipe_clk) | p.22 L516 |

**验证项 Clock-01 (Clock Source 枚举)**
- 确认 3 类时钟源的存在性和正确连接。
- 覆盖率：toggle + functional coverage on clock source selection.
- 页码证据：p.22 L513–L516.

### 1.2 PCIE_CRG 模块 (PLL/DIV/MUX)

**CRG 功能需求 (p.9–10 §1.15)：**

| 需求 ID | 内容 | 页码 |
|---------|------|------|
| CRG_1501 | PLL 功能模式最高输出 **1GHz**，测试模式最高 **2GHz** | p.9 L163 |
| CRG_1502 | PLL ref_clk 可选 **20MHz** (clk_ref_clk)，用于测试/debug | p.9 L164 |
| CRG_1503 | PLL ref_clk 可选 PHY 同源 **100MHz**，正常功能模式 | p.9 L165 |
| CRG_1504 | 功能模式输出：axi_clk **1GHz**, cr_para_clk **125MHz**, local_phy_ref_clk **100MHz** | p.10 L172 |
| CRG_1505 | 测试模式输出：clk_test0 **2GHz**, clk_test1 **1GHz**, clk_test2 **125MHz**, clk_test3 **100MHz** | p.10 L173 |
| CRG_1506 | PCIe 控制器使用 lane0 固定 **1GHz max_pclk** 作为逻辑时钟，Controller 根据 lane rate 分频生成 core_clk 和 pclk | p.10 L174 |

**MUX 结构 (p.22 L517–L520)：**
本地 PLL 参考时钟有 3 个可选源：
1. PHY mplla 的 `refa_dig_fr_clk` **100MHz**
2. PHY mpllb 的 `refb_dig_fr_clk` **100MHz**
3. 芯片的 `clk_ref_clk` **20MHz**

**PHY 参考时钟 3 种形态 (p.22 L521–L524)：**
1. PHY0 从 PAD 输入，PHY1~3 级联接前一 PHY
2. PHY0 从本地 PLL 输出接入，PHY1~3 级联
3. PHY0~3 各自从 PAD 独立输入，且时钟源必须一致

**验证项 Clock-02 (CRG PLL 频率验证)**
- 功能模式下验证 axi_clk = 1GHz, cr_para_clk = 125MHz, local_phy_ref_clk = 100MHz。
- 测试模式下验证 clk_test[0:3] 频率。
- 断言：`assert property (@(posedge axi_clk) axi_clk_period == 1000ps)` (functional mode)。
- 页码证据：p.10 L172–L173 (CRG_1504/1505).

**验证项 Clock-03 (PLL Reference Clock MUX)**
- 验证 3 路参考时钟 MUX 选择正确性。
- 遍历 3 种参考时钟源 (mplla 100MHz / mpllb 100MHz / clk_ref_clk 20MHz)。
- Covergroup: `cp_refclk_mux_sel: coverpoint refclk_sel { bins mplla, mpllb, clk_ref_20m; }`
- 页码证据：p.22 L517–L520.

**验证项 Clock-04 (PHY Reference Clock 分发模式)**
- 验证 3 种 PHY ref clock 分发形态 (独立 PAD / 本地 PLL 级联 / 全部独立 PAD)。
- 确认 PHY1~3 级联模式下 ref clock 来源正确。
- 页码证据：p.22 L521–L524；p.8 L137–L144 (CRG_1203–1205).

**验证项 Clock-05 (max_pclk → core_clk DIV 分频)**
- 验证 Controller 从 lane0 max_pclk (1GHz) 按 lane rate 分频生成 core_clk。
- Gen1 (2.5GT/s) → core_clk = 62.5MHz, Gen2 (5GT/s) → 125MHz, Gen3 (8GT/s) → 250MHz, Gen4 (16GT/s) → 500MHz, Gen5 (32GT/s) → 1GHz。
- 断言：频率比值与 lane rate 对应关系。
- 页码证据：p.10 L174 (CRG_1506); p.37 L940–L941.

### 1.3 GFM (Glitch-Free MUX)

**core_clk ↔ aux_clk 动态切换 (p.22 L529–L532)：**
- core_clk：normal 模式使用
- aux_clk：低功耗模式使用，从 clk_ref_clk 接入 (p.13 L250)
- 二者通过 GFM 动态切换，**无 CDC 处理**（core_clk 和 aux_clk 之间）

**验证项 Clock-06 (GFM 无缝切换)**
- 验证 core_clk ↔ aux_clk 切换无 glitch。
- 断言：切换过程中输出时钟不出现 < 最小脉宽的毛刺。
- 验证低功耗进入/退出时 GFM 切换时序。
- Cross coverage: `cp_gfm_switch: cross cp_power_state, cp_gfm_sel`.
- 页码证据：p.22 L529–L532.

### 1.4 异步时钟域关系

| 异步对 | 说明 | 页码 |
|--------|------|------|
| core_clk ↔ pipe_rx_clk | PIPE RX 恢复时钟 vs Controller 逻辑时钟 | p.22 L526–L528 |
| core_clk ↔ aux_clk | 通过 GFM 切换，无 CDC | p.22 L529–L532 |
| AMBA 时钟 ↔ core 时钟 | AMBA (axi_clk, cr_clk, local_ref_clk) 与 core 异步 | p.22 L537–L538 |

**验证项 Clock-07 (异步时钟域 CDC 检查)**
- CDC structural check: 确认 core_clk ↔ pipe_rx_clk 之间有正确的异步 FIFO/同步器。
- 确认 AMBA 时钟域（axi_clk, cr_clk）与 core_clk 之间有正确的 CDC 处理（p.22 L537–L538 "AMBA 时钟和 core 时钟之间异步" 要求必须有 CDC）。
- 确认 core_clk ↔ aux_clk 之间**无** CDC 处理（GFM 替代）。
- 页码证据：p.22 L526–L538.

---

## 2. Reset Source & Cold/Warm/Hot Reset 验证

### 2.1 复位源分类

| 复位类型 | 触发源 | 复位范围 | 页码 |
|----------|--------|----------|------|
| **Cold Reset** (冷复位) | `power_on_rst_n` (外部 PAD) | 全芯片重新初始化 | p.23 L550 |
| **Warm Reset** (暖复位) | `PERST#` (Host 边带信号) | 全芯片复位（PERST# → power_on_rst_n） | p.23 L551 |
| **Hot Reset** (热复位) | RC/Switch 发送连续 2 个 Hot Reset=1 的 TS1 Ordered Set | SoC 复位，PCIe 子系统不复位 (HotResetBlock/TBU/ReMap 除外) | p.23 L553, p.10 L178 |

**补充说明：**
- PERST# 直接连接到硬件冷复位 `power_on_rst_n` (p.23 L551)
- Link Disable 等同于 Hot Reset 处理 (p.10 L179, CRG_1604)
- **FLR 不支持** (p.10 L181, CRG_1606)

**验证项 Reset-01 (复位源触发)**
- 验证 power_on_rst_n 断言后全芯片进入初始状态。
- 验证 PERST# 拉低 → power_on_rst_n 复位的信号链路。
- 验证 TS1 Hot Reset bit=1 连续 2 个 → Hot Reset 状态机的触发。
- Covergroup: `cp_reset_source: coverpoint reset_type { bins cold, warm, hot; }`
- 页码证据：p.23 L550–L553.

### 2.2 Cold Reset 流程

**验证项 Reset-02 (Cold Reset Sequence)**
- PAD power_on_rst_n 拉低 → 全芯片复位。
- 复位释放后，PHY boot (ROM/SRAM/CR bus 模式之一)，PLL lock，Controller 初始化。
- 最终 LTSSM 进入 Detect 状态等待建连。
- 验证 PLL lock 在冷复位后重新锁定。
- 页码证据：p.23 L550；p.9 L147–L149 (PHY boot 模式).

### 2.3 Warm Reset (PERST#) 流程

**验证项 Reset-03 (PERST# Sequence)** — 参考 Program Guide p.40 §4.20.2：
1. RC 拉低 PERST# → EP 接收 link_down 中断
2. CPU 收到 core_rst_int
3. CPU 重新配置 PCIe 控制器和子系统寄存器
4. `app_ltssm_enable = 1`
5. 等待 `rdlh_link_up = 1` (Link Training 完成)
6. RC 重新枚举 EP
7. 开始传数据

- 断言：PERST# 断言期间 PCIe 控制器处于复位状态。
- 断言：PERST# 释放后 `app_ltssm_enable` 配置前，LTSSM 不进入 training。
- 页码证据：p.40 L995 (PERST# sequence Step 14–19).

### 2.4 Hot Reset 流程

**验证项 Reset-04 (Hot Reset Sequence)** — 参考 §4.5.3 (p.23–24) 和 p.40–41 §4.20.3：

1. RC 发送连续 2 个 Hot Reset=1 的 TS1 Ordered Set
2. EP 收到 `hot_reset_int` 中断
3. EP CPU 收到 `link_down` 中断
4. HotResetBlock 开始屏蔽新的 AXI Slave 访问
5. PCIe Controller 开始 flush 未完成的 Outstanding 事务
6. flush 完成后 `app_ltssm_enable` 被拉低（flush timer 可选延长）
7. Controller assert `core_rst_n`，写 `app_ltssm_enable = 0`
8. CPU 收到 `core_rst_int` → 触发 watchdog timer → SoC Reset
9. SoC 重新 boot（PCIe 不需要）
10. 配置 `app_ltssm_enable = 1`
11. 等待 `rdlh_link_up = 1`
12. RC 重新枚举

- 断言：Hot Reset 期间 PCIe 子系统（Controller/PHY/CRG）**不**复位。
- 断言：HotResetBlock 在 Hot Reset 标志后阻断新 AXI Slave 请求。
- 断言：flush done 后 `core_rst_n` 被 assert。
- 断言：SoC Reset 后 HotResetBlock 退出 Hot Reset 状态。
- 页码证据：p.23 L552–L553；p.24 L560–L570；p.40–41 L995–L1003.

**验证项 Reset-05 (Hot Reset Flush Timer)**
- Flush Timer timeout 默认 **2us** (p.10 L176, CRG_1601)。
- Timeout 范围 **0 ~ 134ms** 软件可配 (p.24 L566)。
- 验证 flush timer 拉低 `app_ltssm_enable` 的时序。
- 验证软件 flush (写 clear 寄存器) 可提前结束 timer。
- 页码证据：p.10 L176 (CRG_1601); p.24 L566.

**验证项 Reset-06 (HotResetBlock Modes)**
- 3 种模式：Bypass / Firewall / Hot Reset (p.11 L208, CRG_2101)
- Isolation 使能时强制进入 Firewall 模式
- Firewall/Hot Reset 模式 AXI resp Error 或 OK 软件可配 (CRG_2103)
- 软件和硬件均可触发 Block AXI 数据流 (CRG_2102)
- 页码证据：p.11 L207–L210.

---

## 3. 关键 Clock 验证

### 3.1 clk_ref_in / clk_ref_clk

| 属性 | 值 | 来源 |
|------|-----|------|
| 信号名 | `clk_ref_clk` | p.13 L249 |
| 频率 | **20MHz** | p.9 L164 (CRG_1502) |
| 方向 | Input | p.13 L249 |
| 用途 | CRG PLL 外部参考时钟（测试/debug）+ 低功耗模式 aux_clk 源 | p.13 L250 |

**验证项 Key-Clock-01 (clk_ref_clk)**
- 验证 20MHz 频率准确性。
- 验证 clk_ref_clk 经 MUX 后可作为 PLL 参考时钟。
- 验证 clk_ref_clk 作为 aux_clk 源用于低功耗模式 (L2)。
- 页码证据：p.9 L164, p.13 L249–L250.

### 3.2 axi_clk

| 属性 | 值 | 来源 |
|------|-----|------|
| 信号名 | `mstr_aclk` / `slv_aclk` (外部接口) | p.13 L242, L244 |
| 频率 | **1GHz** (功能模式) | p.10 L172 (CRG_1504) |
| 驱动模块 | AXI master/slave, TBU, ReMap, HotResetBlock | p.22 L534 |
| 来源 | 本地 PLL | p.22 L533 |

**验证项 Key-Clock-02 (axi_clk)**
- 验证 axi_clk 频率 = 1GHz（仅支持 1GHz，p.3 V3.1 changelog）。
- 验证 axi_clk 连接到 AXI master/slave/TBU/ReMap/HotResetBlock 的逻辑。
- 验证 axi_clk 与 core_clk 异步（CDC 检查）。
- 页码证据：p.10 L172, p.22 L533–L534.

### 3.3 core_clk

| 属性 | 值 | 来源 |
|------|-----|------|
| 来源 | lane0 max_pclk (1GHz) 经 Controller DIV 分频 | p.10 L174 (CRG_1506), p.37 L940 |
| 频率 | 随 lane rate 变化 (62.5MHz ~ 1GHz) | p.37 L940 |
| 用途 | PCIe Controller 主逻辑时钟 | p.22 L527 |

**验证项 Key-Clock-03 (core_clk)**
- 验证 core_clk 频率随 lane rate 正确变化。
- 验证各 Gen (Gen1–5) 下 core_clk 频率对应关系。
- 验证 ASPM L1 / PM L1 时 core_clk 停止 (p.8 L118, CRG_902)。
- 页码证据：p.10 L174; p.8 L118; p.22 L527.

### 3.4 cfg_clk (pcie_cfg_clk)

| 属性 | 值 | 来源 |
|------|-----|------|
| 信号名 | `cfg_clk` | p.13 L246 |
| 频率 | **250MHz** | p.22 L539 |
| 方向 | Input (从 SoC PLL) | p.13 L246 |
| 用途 | NoC 和系统寄存器逻辑时钟 | p.22 L539 |

**验证项 Key-Clock-04 (cfg_clk)**
- 验证 cfg_clk 频率 = 250MHz。
- 验证 cfg_clk 下 DBI/APB 配置访问正常。
- 页码证据：p.13 L246; p.22 L539.

### 3.5 pipe_rx_clk / pipe_tx_clk (PIPE 接口时钟)

| 属性 | 值 | 来源 |
|------|-----|------|
| pipe_rx_clk | SerDes RX 数据恢复时钟，输入 Controller | p.29 L721 |
| pipe_rx_rst_n | pipe_rx_clk 时钟域复位 | p.29 L722 |
| pipe_pclk | max_pclk 分频生成，PIPE TX 接口时钟 | p.37 L940 |
| pipe_msgbus_rst_n | pipe_clk 时钟域复位 | p.29 L720 |
| phy_mac_rxvalid | 指示 pipe_rx_clk stable | p.29 L733 |

**验证项 Key-Clock-05 (pipe_rx_clk / pipe_pclk)**
- 验证 pipe_rx_clk 在 `phy_mac_rxvalid = 1` 后才有效。
- 验证 pipe_rx_clk 与 core_clk 异步（CDC 检查）。
- 验证 pipe_pclk (TX 方向) 频率正确且相位对齐。
- 验证 `mac_phy_rate` 改变后 pipe_pclk rate 切换正确（通过 maxpclkreq/ack 握手）。
- 页码证据：p.29 L720–L725, L733; p.37 L940–L941.

### 3.6 低功耗时钟行为

| 低功耗状态 | 时钟行为 | 页码 |
|-----------|---------|------|
| L0s | core_clk/pipe_clk 保持 ON | p.31 L806 |
| L1 (ASPM/PM) | core_clk/pipe_clk **停止** (CRG_902) | p.8 L118 |
| L1 substate | PHY 参考时钟**移除** (CRG_903) | p.8 L119 |
| L1 Clock PM | PLL 和 reference clock 关闭 | p.33 L827 |

**验证项 Key-Clock-06 (Low Power Clock Gating)**
- 验证 L1 entry → core_clk/pipe_clk 停止。
- 验证 L1 substate → PHY ref clock 移除。
- 验证 L1 CPM → PLL power down，reference clock 关闭。
- 验证 L1/L0s exit → 时钟恢复且 PLL re-lock。
- 页码证据：p.8 L118–L119; p.31 L803–L808; p.33 L827.

---

## 4. 关键 Reset 验证

### 4.1 cfg_rst_n

| 属性 | 值 | 页码 |
|------|-----|------|
| 信号名 | `cfg_rst_n` | p.13 L247 |
| 方向 | Input | p.13 L247 |
| 时钟域 | cfg_clk (250MHz) | p.13 L246–L247 |
| 用途 | AXI DBI 配置接口复位 | p.13 L247 |

**验证项 Key-Reset-01 (cfg_rst_n)**
- 验证 cfg_rst_n 断言时 DBI 配置接口不可访问。
- 验证 cfg_rst_n 释放后配置空间可正常读写。
- 验证 cfg_rst_n 与 Cold/Warm Reset 的关系（是否随 power_on_rst_n 一同断言）。
- 页码证据：p.13 L246–L247.

### 4.2 mstr_rst_n

| 属性 | 值 | 页码 |
|------|-----|------|
| 信号名 | `mstr_rst_n` | p.13 L243 |
| 方向 | Output (PCIe 子系统输出到 NoC) | p.13 L243 |
| 时钟域 | mstr_aclk (1GHz) | p.13 L242–L243 |
| 用途 | AXI Master NoC 接口复位 | p.13 L243 |

**验证项 Key-Reset-02 (mstr_rst_n)**
- 验证 PCIe 内部复位源能正确驱动 mstr_rst_n 到 NoC。
- 验证 Cold/Warm Reset 时 mstr_rst_n 断言。
- 验证 Hot Reset 时 mstr_rst_n 的行为（根据复位范围，PCIe 子系统不复位 → mstr_rst_n 可能不断言）。
- 页码证据：p.13 L242–L243.

### 4.3 slv_rst_n

| 属性 | 值 | 页码 |
|------|-----|------|
| 信号名 | `slv_rst_n` | p.13 L245 |
| 方向 | Output (PCIe 子系统输出到 NoC) | p.13 L245 |
| 时钟域 | slv_aclk (1GHz) | p.13 L244–L245 |
| 用途 | AXI Slave NoC 接口复位 | p.13 L245 |

**验证项 Key-Reset-03 (slv_rst_n)**
- 同 mstr_rst_n 验证策略。
- 额外验证：Hot Reset 期间 HotResetBlock 阻断 AXI Slave 访问时，slv_rst_n 的行为。
- 页码证据：p.13 L244–L245.

### 4.4 PERST#

| 属性 | 值 | 页码 |
|------|-----|------|
| 信号名 | `PERST#` | p.13 L251, p.14 L276 |
| 方向 | Input PAD | p.39 L979 |
| 有效电平 | 低有效 | p.14 L275 (WAKE# 描述) |
| 用途 | Warm Reset — Host 边带信号复位 PCIe 控制器 | p.13 L251, p.23 L551 |
| 连接 | 芯片内部连接到 `power_on_rst_n` 对全芯片复位 | p.23 L551 |

**验证项 Key-Reset-04 (PERST#)**
- 验证 PERST# 拉低 → power_on_rst_n 断言的信号链路延时。
- 验证 PERST# 释放后 PHY 重新 boot + PLL lock 时序。
- 验证 PERST# sequence (p.40 §4.20.2) 的完整状态机。
- 验证 PERST# 去抖动（如果存在）。
- 验证 `perst_int` 中断正确上报 (p.25 L589)。
- 页码证据：p.13 L251; p.23 L551; p.40 L995; p.25 L589.

### 4.5 其他关键复位信号

| 信号 | 时钟域 | 用途 | 页码 |
|------|--------|------|------|
| `pll_rst_n` | — | CRG PLL 外部复位输入 | p.13 L248 |
| `pipe_rx_rst_n` | pipe_rx_clk | PIPE RX 时钟域复位 | p.29 L722 |
| `pipe_msgbus_rst_n` | pipe_clk | PIPE message bus 时钟域复位 | p.29 L720 |
| `phy_laneX_reset_n` | — | 每 lane PHY 复位 (p.3 V3.02 changelog: OR→&) | p.3 L28 |

**验证项 Key-Reset-05 (PIPE 域复位)**
- 验证 pipe_rx_rst_n 与 pipe_rx_clk 的复位释放同步。
- 验证 pipe_msgbus_rst_n 不影响 PIPE 数据传输。
- 验证 phy_laneX_reset_n 的 & (AND) 逻辑 — 所有 lane 就绪后才释放复位 (p.3 L28 changelog)。
- 页码证据：p.29 L720–L722; p.3 L28.

### 4.6 复位域互锁

**验证项 Key-Reset-06 (复位域交叉验证)**
- Cold Reset (power_on_rst_n) → 所有时钟域复位 (cfg_rst_n, mstr_rst_n, slv_rst_n, pipe_rst_n, core_rst)。
- Warm Reset (PERST#) → 同 Cold Reset 效果（因为 PERST# → power_on_rst_n）。
- Hot Reset → **仅** SoC 侧复位 (HotResetBlock, TBU)，PCIe 子系统不复位。
  - Controller/PHY/CRG 保持运行。
  - `app_ltssm_enable` 被拉低，但控制器不复位。
- 页码证据：p.10 L178 (CRG_1603); p.23 L550–L553.

---

## 5. Assertions / Checkers / Coverage

### 5.1 SVA Assertions

```systemverilog
// === Clock Assertions ===

// Clock-02: CRG PLL 频率检查
property p_axi_clk_1ghz;
  @(posedge axi_clk) disable iff (!pll_locked)
    $rose(pll_locked) |-> ##[1:$] (axi_clk_period == 1000ps); // functional mode
endproperty
A_AXI_CLK_1GHZ: assert property(p_axi_clk_1ghz);

// Clock-05: max_pclk → core_clk 分频
property p_core_clk_div( int ratio );
  @(posedge core_clk)
    (max_pclk_cnt == ratio) |-> core_clk_toggle;
endproperty

// Clock-06: GFM 切换无 glitch
property p_gfm_no_glitch;
  @(posedge gfm_out_clk)
    !$stable(gfm_sel) |-> ##[1:5] !$isunknown(gfm_out_clk);
endproperty
A_GFM_NO_GLITCH: assert property(p_gfm_no_glitch);

// Clock-07: CDC — core_clk ↔ pipe_rx_clk 异步
// structural: assert that async_fifo or sync_ff exists between domains

// === Reset Assertions ===

// Reset-01: PERST# → power_on_rst_n 链路
property p_perst_to_cold_reset;
  @(negedge PERST_n)
    $fell(PERST_n) |-> ##[0:10] $fell(power_on_rst_n);
endproperty
A_PERST_TO_COLD_RESET: assert property(p_perst_to_cold_reset);

// Reset-02: Cold reset 释放后 PLL lock
property p_cold_reset_pll_lock;
  @(posedge ref_clk) disable iff (!power_on_rst_n)
    $rose(power_on_rst_n) |-> ##[1:MAX_PLL_LOCK_CYCLES] pll_locked;
endproperty
A_COLD_RESET_PLL_LOCK: assert property(p_cold_reset_pll_lock);

// Reset-04: Hot Reset — PCIe 子系统不复位
property p_hot_reset_no_pcie_reset;
  @(posedge core_clk)
    hot_reset_state |-> !core_rst_asserted;
endproperty
A_HOT_RESET_NO_PCIE_RESET: assert property(p_hot_reset_no_pcie_reset);

// Reset-05: Hot Reset flush timer
property p_flush_timer_timeout;
  @(posedge core_clk)
    hot_reset_active |-> ##[FLUSH_TIMEOUT_MIN:FLUSH_TIMEOUT_MAX] flush_done;
endproperty
A_FLUSH_TIMER_TIMEOUT: assert property(p_flush_timer_timeout);

// Reset-04: HotResetBlock 阻断 AXI Slave 访问
property p_hotreset_block_axi;
  @(posedge axi_clk)
    hot_reset_flag |-> !slv_awvalid && !slv_arvalid;
endproperty
A_HOTRESET_BLOCK_AXI: assert property(p_hotreset_block_axi);

// Reset-03: PERST# sequence — app_ltssm_enable
property p_perst_ltssm_enable;
  @(posedge core_clk)
    $rose(PERST_n) |-> !app_ltssm_enable throughout
      (##[0:$] $rose(app_ltssm_enable));
endproperty
A_PERST_LTSSM_ENABLE: assert property(p_perst_ltssm_enable);

// Key-Clock-05: pipe_rx_clk stable before rxvalid
property p_pipe_rxclk_valid;
  @(posedge pipe_rx_clk)
    $rose(phy_mac_rxvalid) |-> $stable(pipe_rx_clk);
endproperty
A_PIPE_RXCLK_VALID: assert property(p_pipe_rxclk_valid);

// Key-Reset-05: phy_lane_reset_n AND logic
property p_phy_lane_reset_and;
  @(posedge phy_clk)
    phy_lane0_reset_n && phy_lane1_reset_n && ... && phy_lane15_reset_n
    |-> phy_reset_released;
endproperty
A_PHY_LANE_RESET_AND: assert property(p_phy_lane_reset_and);
```

### 5.2 Functional Coverage

```systemverilog
// === Clock Coverage ===

covergroup cg_clock_config @(posedge ref_clk);
  // Clock source selection
  cp_refclk_src: coverpoint refclk_sel {
    bins mplla_100m  = {2'b00};
    bins mpllb_100m  = {2'b01};
    bins clk_ref_20m = {2'b10};
  }

  // PHY ref clock distribution mode
  cp_phy_refclk_mode: coverpoint phy_refclk_mode {
    bins pad_cascade    = {0};  // PHY0 from PAD, rest cascade
    bins pll_cascade    = {1};  // PHY0 from local PLL, rest cascade
    bins all_independent = {2}; // All 4 PHY from own PAD
  }

  // GFM switch state
  cp_gfm_state: coverpoint gfm_sel {
    bins core_clk = {0};
    bins aux_clk  = {1};
  }

  // Cross: GFM switch × power state
  cp_gfm_x_pwr: cross cp_gfm_state, cp_power_state {
    bins normal_core   = binsof(cp_gfm_state.core_clk) && binsof(cp_power_state.d0);
    bins lp_aux        = binsof(cp_gfm_state.aux_clk)  && binsof(cp_power_state.l1);
    illegal_bins lp_core = binsof(cp_gfm_state.core_clk) && binsof(cp_power_state.l2);
  }

  // PLL lock status
  cp_pll_lock: coverpoint pll_locked {
    bins locked   = {1};
    bins unlocked = {0};
  }
endgroup

// === Reset Coverage ===

covergroup cg_reset_seq @(posedge core_clk);
  // Reset type
  cp_reset_type: coverpoint reset_event {
    bins cold_reset = {0};
    bins warm_reset = {1};
    bins hot_reset  = {2};
  }

  // Hot Reset flush done
  cp_flush_done: coverpoint {wr_flush_done, rd_flush_done} {
    bins both_done   = {2'b11};
    bins wr_only     = {2'b10};
    bins rd_only     = {2'b01};
    bins none        = {2'b00};
  }

  // LTSSM state during reset
  cp_ltssm_on_reset: coverpoint ltssm_state {
    bins detect = {DETECT};
    bins hot_reset_state = {HOT_RESET};
    bins disable = {DISABLE};
  }

  // Cross: reset type × LTSSM state
  cp_rst_x_ltssm: cross cp_reset_type, cp_ltssm_on_reset;
endgroup

// === Key Clock Frequency Coverage ===

covergroup cg_clk_freq @(posedge core_clk);
  cp_gen_rate: coverpoint current_gen {
    bins gen1 = {0}; // 2.5 GT/s
    bins gen2 = {1}; // 5.0 GT/s
    bins gen3 = {2}; // 8.0 GT/s
    bins gen4 = {3}; // 16.0 GT/s
    bins gen5 = {4}; // 32.0 GT/s
  }
  cp_core_clk_freq: coverpoint core_clk_freq_mhz {
    bins g1_62p5  = {62};
    bins g2_125   = {125};
    bins g3_250   = {250};
    bins g4_500   = {500};
    bins g5_1000  = {1000};
  }
  cp_gen_x_core: cross cp_gen_rate, cp_core_clk_freq;
endgroup
```

### 5.3 Checker 清单

| Checker | 类型 | 描述 | 强制等级 |
|---------|------|------|---------|
| CDC_struct_check | Structural | 跨异步时钟域必须有同步器/FIFO | **Error** |
| clk_freq_monitor | Runtime | 各时钟频率必须在容差范围内 | **Error** |
| reset_tree_check | Structural | 复位信号必须正确扇出到所有受控域 | **Error** |
| pll_lock_timeout | Runtime | PLL 上电后必须在 timeout 内 lock | **Error** |
| gfm_glitch_check | Runtime | GFM 输出不允许有 glitch | **Error** |
| hotreset_axi_block | Runtime | Hot Reset 期间 AXI Slave 新请求必须被阻断 | **Warning** |
| flush_timer_range | Runtime | Flush timer 必须在 0~134ms 范围 | **Warning** |
| ltssm_enable_seq | Runtime | app_ltssm_enable 在复位未完成时不能拉高 | **Error** |
| phy_rxvalid_clk | Runtime | pipe_rx_clk 必须 stable 后 phy_mac_rxvalid 才拉高 | **Error** |
| perst_pulse_width | Runtime | PERST# 最小脉宽检查 | **Error** |

---

## 6. 页码证据索引

| 验证域 | Spec 章节 | 页码 | 关键需求 ID |
|--------|----------|------|------------|
| Clock Source 3 类 | §4.4.1 | p.22 L513–L516 | — |
| CRG PLL 频率 | §1.15 | p.9–10 L163–L174 | CRG_1501–1506 |
| PLL Ref Clock MUX | §4.4.1 | p.22 L517–L520 | — |
| PHY Ref Clock 3 形态 | §4.4.1 | p.22 L521–L524 | CRG_1203–1205 |
| GFM core↔aux 切换 | §4.4.1 | p.22 L529–L532 | — |
| 异步时钟域 | §4.4.1 | p.22 L526–L538 | — |
| Cold Reset | §4.5.2 | p.23 L550 | — |
| Warm Reset (PERST#) | §4.5.2 | p.23 L551 | — |
| Hot Reset 定义 | §1.16, §4.5.3 | p.10 L178, p.23 L553 | CRG_1603 |
| Hot Reset Flush Timer | §1.16, §4.5.3.2 | p.10 L176, p.24 L566 | CRG_1601 |
| HotResetBlock Modes | §1.21 | p.11 L207–L210 | CRG_2101–2103 |
| Link Disable = Hot Reset | §1.16 | p.10 L179 | CRG_1604 |
| FLR 不支持 | §1.16 | p.10 L181 | CRG_1606 |
| Warm Reset (全芯片) | §1.16 | p.10 L180 | CRG_1605 |
| clk_ref_clk 20MHz | Table 2-1 | p.13 L249 | CRG_1502 |
| axi_clk 1GHz | §1.15 | p.10 L172 | CRG_1504 |
| core_clk (max_pclk DIV) | §1.15 | p.10 L174 | CRG_1506 |
| cfg_clk 250MHz | §4.4.1 | p.22 L539 | — |
| pipe_rx_clk / pipe_pclk | §4.9, §4.15.4 | p.29 L720–L725, p.37 L940–L941 | — |
| cfg_rst_n | Table 2-1 | p.13 L247 | — |
| mstr_rst_n | Table 2-1 | p.13 L243 | — |
| slv_rst_n | Table 2-1 | p.13 L245 | — |
| PERST# | Table 2-1, §4.17 | p.13 L251, p.39 L979 | — |
| Low Power 时钟行为 | §4.12 | p.31 L803–L808 | CRG_901–905 |
| Clock PM (L1 CPM) | §4.12.3.4 | p.33 L827 | — |
| phy_laneX_reset_n AND | Changelog V3.02 | p.3 L28 | — |
| PERST# Sequence | §4.20.2 | p.40 L995 | — |
| Hot Reset Sequence | §4.20.3 | p.40–41 L995–L1003 | — |
| Interrupt 列表 (时钟/复位) | §4.6.1 | p.24–25 L589–L605 | CRG_1706 |
| PIPE 接口复位 | §4.9 | p.29 L720–L722 | — |
| Partial Good 域 | §4.19 | p.39 L983–L985 | — |

---

## 7. 验证优先级与依赖

### P0 (必须覆盖)
- [ ] PLL lock/unlock 及频率检查 (CRG_1501–1506)
- [ ] Cold Reset → PLL lock → LTSSM Detect (全链路)
- [ ] PERST# → power_on_rst_n 信号链路
- [ ] Hot Reset — PCIe 子系统不复位 (CRG_1603)
- [ ] HotResetBlock AXI 阻断
- [ ] core_clk 分频随 lane rate 变化 (CRG_1506)
- [ ] CDC 结构检查（异步时钟域间）
- [ ] GFM glitch-free 切换

### P1 (应该覆盖)
- [ ] PHY ref clock 3 种分发模式 (CRG_1203–1205)
- [ ] PLL ref clock MUX 3 选 1
- [ ] Hot Reset Flush Timer 0~134ms
- [ ] Low Power 时钟停止/恢复
- [ ] PERST# Sequence 完整状态机
- [ ] Reset 中断正确上报 (perst_int, hot_reset_int, core_rst_int …)
- [ ] HotResetBlock Bypass/Firewall/Hot Reset 3 模式

### P2 (可选覆盖)
- [ ] 测试模式时钟 (clk_test[0:3])
- [ ] Partial good 域 isolation 时时钟/复位行为
- [ ] Link Disable = Hot Reset 等效性
- [ ] Debug IO 时钟/复位域信号观测
