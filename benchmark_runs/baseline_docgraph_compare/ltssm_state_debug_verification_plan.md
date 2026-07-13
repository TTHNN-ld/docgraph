# PCIe Subsystem LTSSM State Debug Verification Plan

> 基于 `pcie_spec_text.txt` (v3.21, 28 Jan 2026)，面向 DV (Design Verification) 和 bring-up debug 使用。
> 每条验证项标注对应的 Spec 页码和需求 ID。

---

## 1. 架构概览

LTSSM State debug 是一个纯硬件 debug 机制 (p.35 §4.13.2)，用于在 PCIe 链路挂死后读取最近 128 个 LTSSM 状态跳变历史，无需外部逻辑分析仪。

```
ltssm_state[4:0] ──► shift_reg[127:0] ──► DMUX ──► state_reg[127:0] ──► APB ──► SW read
                         ▲                      ▲
                    freeze_reg[0]         freeze_reg↑ edge
```

**需求 ID**: `PCIE_MAS_REQ_DEBUG_2301` (p.11 L215)：LTSSM debug shifter 功能，支持在 PCIe 挂死以后读取最新的 128 个 LTSSM 状态。

---

## 2. 寄存器定义

### 2.1 寄存器列表

| Reg name | Reg num | Field | Msb | Lsb | SW Access | HW Access | Default | Description |
|----------|---------|-------|-----|-----|-----------|-----------|---------|-------------|
| `freeze_reg` | 1 | `freeze_reg` | 0 | 0 | RW | RW | 0x0 | 软件查询开关：写 1 触发快照，HW 完成后回写 0 |
| `ltssm_state_reg` | 128 | `ltssm_state` | 5 | 0 | RO | WO | 0x0 | LTSSM 状态编码 (5-bit) |
| | | `ltssm_state_vld` | 6 | 6 | RO | WO | 0x0 | 状态有效位 (1=valid) |

**页码证据**: p.35 L886–L889.

### 2.2 LTSSM State 编码 (5-bit)

> 注：以下为标准 PCIe LTSSM 状态编码，具体编码值以 DWC_pcie_ctl_ep_databook 为准。

| 编码 | LTSSM State | 说明 |
|------|-------------|------|
| 0x00 | Detect.Quiet | 检测静默 |
| 0x01 | Detect.Active | 检测激活 |
| 0x02 | Polling.Active | 轮询激活 |
| 0x03 | Polling.Compliance | 轮询兼容 |
| 0x04 | Polling.Configuration | 轮询配置 |
| 0x05 | Configuration.Linkwidth.Start | 配置链路宽度开始 |
| 0x06 | Configuration.Linkwidth.Accept | 配置链路宽度接受 |
| 0x07 | Configuration.Lanenum.Wait | 配置通道号等待 |
| 0x08 | Configuration.Lanenum.Accept | 配置通道号接受 |
| 0x09 | Configuration.Complete | 配置完成 |
| 0x0A | Configuration.Idle | 配置空闲 |
| 0x0B | L0 | 正常工作状态 |
| 0x0C | L0s | 低功耗短空闲 |
| 0x0D | L1 | 低功耗长空闲 |
| 0x0E | L2 | 低功耗深度睡眠 |
| 0x0F | Recovery.RcvrLock | 恢复-接收锁定 |
| 0x10 | Recovery.RcvrCfg | 恢复-接收配置 |
| 0x11 | Recovery.Speed | 恢复-速率变更 |
| 0x12 | Recovery.Equalization | 恢复-均衡 (Gen3+) |
| 0x13 | Hot Reset | 热复位 |
| 0x14 | Disabled | 禁用 |
| 0x15 | Loopback | 环回 |
| 0x16–0x1F | Reserved | 保留 |

---

## 3. 硬件捕获流程

### 3.1 正常运行 (shift_reg 持续记录)

```
1. pcie_ctrl_wrap 监测 ltssm_state[4:0] 信号
2. 每次 ltssm_state 值发生变化时:
   a. 新状态值 + vld=1 写入 shift_reg 头部
   b. shift_reg 内既有数据向尾部移动一个位置
   c. 若 shift_reg 已满 (128 条)，尾部最旧条目被挤出丢弃
```

**关键特性**：
- shift_reg 深度 = **128 条** (p.35 L878)
- 每个位置 = 5-bit state + 1-bit vld = **6-bit**
- 仅在状态**变化**时记录（非每个 cycle 采样）
- 满后 FIFO 行为：新进挤出最旧

### 3.2 Freeze / Capture / Read 流程

| Step | Actor | 操作 | 说明 |
|------|-------|------|------|
| 1 | SW | 读 `freeze_reg` 确认当前为 0 | 确保无未完成快照 |
| 2 | SW | 写 `freeze_reg = 1` | 发起快照请求 |
| 3 | HW | 检测 `freeze_reg` 上升沿 | 同步到 core_clk 域 |
| 4a | HW | DMUX 写使能拉高 1 拍 | 当拍 shift_reg 全部内容写入 DMUX |
| 4b | HW | shift_reg clear 拉高 1 拍 | 下一拍 shift_reg 全部清空 |
| 5 | HW | DMUX 输出使能拉高 | 数据写入 `state_reg[127:0]` |
| 6 | HW | 写 `freeze_reg = 0` | 通知 SW 快照完成 |
| 7 | SW | 轮询 `freeze_reg` 直到读回 0 | 等待完成 |
| 8 | SW | 通过 APB 依次读 `ltssm_state_reg[0]` ~ `ltssm_state_reg[127]` | 取回状态历史 |

**页码证据**: p.35 L878–L884.

### 3.3 时序图 (文字描述)

```
         T0    T1    T2    T3    T4    T5
core_clk  ██....██....██....██....██....██

freeze_reg (SW→HW)  ▁▁▁▁/▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁
                          1

freeze_reg (HW→SW)  ▁▁▁▁▁▁▁▁▁▁▁▁\▁▁▁▁▁▁
                                    0

DMUX wr_en          ▁▁▁▁▁▁▁/▁\▁▁▁▁▁▁▁▁

shift_reg clear     ▁▁▁▁▁▁▁/▁\▁▁▁▁▁▁▁▁

state_reg           XXXXX <snapshot> XXXXX

SW read             ▁▁▁▁▁▁▁▁▁▁▁ APB read loop
```

---

## 4. 相关信号与中断

### 4.1 LTSSM 关联中断 (p.24–25 §4.6.1)

| 中断信号 | 位宽 | 触发条件 | 页码 |
|----------|------|----------|------|
| `ltssm_into_gen5_int` | 1 | LTSSM 进入 L0 且 signaling rate = 32.0 GT/s | p.25 L592 |
| `hot_reset_int` | 1 | LTSSM 进入 HOT_RESET 状态 | p.25 L602 |
| `rdlh_link_up` | 1 | Data Link Layer up/down (1=up, 0=down) | p.24 L576 |
| `smlh_link_up` | 1 | PHY Link up/down (1=up, 0=down) | p.24 L577 |
| `link_down_event_int` | 1 | Link down 事件 | p.25 L588 |
| `core_rst_int` | 1 | core_rst assert | p.25 L593 |

### 4.2 LTSSM 控制信号

| 信号 | 说明 | 页码 |
|------|------|------|
| `app_ltssm_enable` | 软件使能 LTSSM 状态机，写 1 启动 link training | p.40 L994 |
| `rdlh_link_up` | Link training 完成标志，软件轮询直到 =1 | p.40 L994 |

---

## 5. Assertions (SVA)

### 5.1 shift_reg 基本行为

```systemverilog
// A1: shift_reg 深度不超过 128
property p_shift_reg_depth_max;
    @(posedge core_clk) disable iff (!rst_n)
    (shift_reg_count <= 128);
endproperty
a_shift_reg_depth_max: assert property(p_shift_reg_depth_max);

// A2: 每次 ltssm_state 变化, vld 必须为 1
property p_shift_reg_vld_on_change;
    @(posedge core_clk) disable iff (!rst_n)
    ($changed(ltssm_state) |=> shift_reg[0].vld === 1'b1);
endproperty
a_shift_reg_vld_on_change: assert property(p_shift_reg_vld_on_change);

// A3: ltssm_state 不变时, shift_reg 不写入
property p_no_write_on_stable;
    @(posedge core_clk) disable iff (!rst_n)
    ($stable(ltssm_state) |=> $stable(shift_reg[0]));
endproperty
a_no_write_on_stable: assert property(p_no_write_on_stable);
```

### 5.2 freeze_reg 握手机制

```systemverilog
// A4: freeze_reg SW写1 → HW必须在 N 拍内回写0
property p_freeze_ack;
    @(posedge core_clk) disable iff (!rst_n)
    (freeze_reg == 1'b1) |-> ##[1:32] (freeze_reg == 1'b0);
endproperty
a_freeze_ack: assert property(p_freeze_ack);

// A5: freeze_reg 下降沿必须伴随 state_reg 更新
property p_state_reg_update_on_freeze_done;
    @(posedge core_clk) disable iff (!rst_n)
    $fell(freeze_reg) |-> $past(dmux_wr_en, 1);
endproperty
a_state_reg_update_on_freeze_done: assert property(p_state_reg_update_on_freeze_done);

// A6: freeze 期间 shift_reg 被清空
property p_shift_reg_cleared_after_freeze;
    @(posedge core_clk) disable iff (!rst_n)
    $fell(freeze_reg) |-> ##1 (shift_reg_valid_count == 0);
endproperty
a_shift_reg_cleared_after_freeze: assert property(p_shift_reg_cleared_after_freeze);
```

### 5.3 数据一致性

```systemverilog
// A7: freeze 后 state_reg 与 freeze 前 shift_reg 一致
// (需 checker 保存 golden copy)
property p_state_reg_match_shift_reg;
    @(posedge core_clk) disable iff (!rst_n)
    $rose(freeze_reg) |-> ##[1:8] (state_reg == $past(shift_reg, 1));
endproperty
a_state_reg_match_shift_reg: assert property(p_state_reg_match_shift_reg);

// A8: 清空后 vld 全部为 0
property p_all_vld_clear_after_reset;
    @(posedge core_clk) disable iff (!rst_n)
    !rst_n ##1 rst_n |-> ##1 (|ltssm_state_vld_bits == 1'b0);
endproperty
a_all_vld_clear_after_reset: assert property(p_all_vld_clear_after_reset);
```

### 5.4 DMUX / state_reg 写行为

```systemverilog
// A9: dmux 写使能仅持续 1 拍
property p_dmux_wr_en_one_shot;
    @(posedge core_clk) disable iff (!rst_n)
    $rose(dmux_wr_en) |=> $fell(dmux_wr_en);
endproperty
a_dmux_wr_en_one_shot: assert property(p_dmux_wr_en_one_shot);
```

---

## 6. Functional Coverage

### 6.1 Covergroup: LTSSM State Debug

```systemverilog
covergroup cg_ltssm_debug @(posedge core_clk);
    option.per_instance = 1;

    // CP1: shift_reg fill level
    cp_shift_reg_fill: coverpoint shift_reg_valid_count {
        bins empty        = {0};
        bins partial_low  = {[1:31]};
        bins partial_mid  = {[32:95]};
        bins partial_high = {[96:127]};
        bins full         = {128};
    }

    // CP2: freeze 时的 shift_reg fill level
    cp_freeze_at_fill: coverpoint shift_reg_valid_count iff ($rose(freeze_reg)) {
        bins empty        = {0};
        bins partial      = {[1:127]};
        bins full         = {128};
    }

    // CP3: 连续 freeze 次数 (压力场景)
    cp_consecutive_freezes: coverpoint freeze_count_1s {
        bins single       = {1};
        bins burst_2_4    = {[2:4]};
        bins burst_5_8    = {[5:8]};
        bins heavy        = {[9:16]};
    }

    // CP4: LTSSM 状态遍历
    cp_ltssm_state_seen: coverpoint ltssm_state {
        bins detect_states      = {DETECT_QUIET, DETECT_ACTIVE};
        bins polling_states     = {POLLING_ACTIVE, POLLING_COMPLIANCE, POLLING_CONFIG};
        bins config_states      = {CFG_LINKWIDTH_START, CFG_LINKWIDTH_ACCEPT,
                                   CFG_LANENUM_WAIT, CFG_LANENUM_ACCEPT,
                                   CFG_COMPLETE, CFG_IDLE};
        bins L0                 = {L0};
        bins L0s                = {L0s};
        bins L1                 = {L1};
        bins L2                 = {L2};
        bins recovery_states    = {RECOVERY_RCVRLOCK, RECOVERY_RCVRCFG,
                                   RECOVERY_SPEED, RECOVERY_EQ};
        bins hot_reset          = {HOT_RESET};
        bins disabled_loopback  = {DISABLED, LOOPBACK};
        illegal_bins reserved   = {[5'h16:$]} iff (ltssm_state_vld);
    }

    // CP5: freeze_reg 握手延迟
    cp_freeze_latency: coverpoint freeze_ack_cycles {
        bins fast_1_2       = {[1:2]};
        bins typical_3_8    = {[3:8]};
        bins slow_9_31      = {[9:31]};
    }

    // CP6: shift_reg eviction on full
    cp_eviction_count: coverpoint eviction_event_count {
        bins never          = {0};
        bins once           = {1};
        bins few            = {[2:10]};
        bins many           = {[11:100]};
    }

    // CP7: state_reg 读出数据与 shift_reg 一致性
    cp_data_match: coverpoint data_match_result {
        bins pass = {1};
        bins fail = {0};
    }

    // CP8: ltssm_state 变化速率
    cp_transition_rate: coverpoint ltssm_transitions_per_1k_cycles {
        bins slow         = {[1:5]};
        bins medium       = {[6:20]};
        bins fast         = {[21:50]};
        bins very_fast    = {[51:100]};
    }
endgroup
```

### 6.2 Cross Coverage

```systemverilog
// CROSS1: freeze at fill level × data match
cr_freeze_fill_x_match: cross cp_freeze_at_fill, cp_data_match;

// CROSS2: LTSSM state × shift_reg fill level
cr_state_x_fill: cross cp_ltssm_state_seen, cp_shift_reg_fill;
```

---

## 7. 测试场景

### 7.1 基本功能测试

| Test | 场景 | 检查点 | 页码 |
|------|------|--------|------|
| **debug_01** | shift_reg 空时 freeze | state_reg 全 vld=0, freeze_reg 正确握手 | p.35 L878–884 |
| **debug_02** | shift_reg 部分填充 (<128) 时 freeze | state_reg 中有效条目数 = freeze 前 shift_reg 条目数 | p.35 L878 |
| **debug_03** | shift_reg 满 (128) 时 freeze | state_reg 128 条全有效，freeze 后 shift_reg 被清空 | p.35 L878 |
| **debug_04** | shift_reg 满后继续记录 (触发 eviction) | 验证旧数据被正确挤出，新数据写入头部 | p.35 L878 |
| **debug_05** | 连续 2 次 freeze | 第 2 次 freeze 时 shift_reg 可能为空，验证不会挂死 | — |
| **debug_06** | 连续多次 freeze (压力) | 每次握手正确完成，无数据残留 | — |

### 7.2 场景注入测试

| Test | 场景 | 注入方式 | 检查点 |
|------|------|----------|--------|
| **debug_07** | 正常 link training 全流程 | 使能 LTSSM → 等待 link up | 128 条记录覆盖 Detect → L0 全路径 |
| **debug_08** | Link down 后 freeze | 模拟 PERST# 拉低 | 捕获 link down 前的 LTSSM 变化 |
| **debug_09** | Hot Reset 后 freeze | RC 发送 TS1 (Hot Reset=1) | 验证 hot_reset_int 触发 + LTSSM 进入 HOT_RESET 状态 |
| **debug_10** | L0s/L1 进出 | 配置 ASPM，等待低功耗进入 | 捕获 L0→L0s→L0 / L0→L1→Recovery→L0 序列 |
| **debug_11** | Gen1→Gen5 速率切换 | 正常 link training | 验证 Recovery.Speed 和 Recovery.Equalization 状态记录 |
| **debug_12** | 链路挂死注入 | Force ltssm_state 卡住 | freeze 后 state_reg 应有正确历史 (挂死原因可定位) |

### 7.3 跨时钟域 / 时序测试

| Test | 场景 | 检查点 |
|------|------|--------|
| **debug_13** | core_clk 与 APB clk 异步 | freeze_reg 上升沿检测正确跨 CDC，无亚稳态 |
| **debug_14** | freeze 期间 ltssm_state 继续变化 | DMUX 快照与 shift_reg clear 的 race-free 设计 |
| **debug_15** | APB 读 state_reg 时 core_clk 域继续运行 | state_reg 读一致性 (RO 寄存器，读期间不应变化) |

---

## 8. Bring-Up Debug 操作 Checklist

### 8.1 挂死诊断流程

```
Step 1: 确认挂死
  □ 读 rdlh_link_up → 期望 1, 若为 0 则已 link down
  □ 读 smlh_link_up → 期望 1, 若为 0 则 PHY link down
  □ 读中断状态寄存器, 检查 link_down_event_int

Step 2: 触发 LTSSM 快照
  □ 读 freeze_reg → 确认 = 0
  □ 写 freeze_reg = 1

Step 3: 等待快照完成
  □ 轮询 freeze_reg, 等待 1→0
  □ 若超时 (>1ms), 报告 freeze_reg 握手失败

Step 4: 读取状态历史
  □ for i = 0 to 127:
      读 ltssm_state_reg[i]
      若 vld=1, 记录 (index, state)
  □ 生成 LTSSM trace: 按 index 顺序输出状态名

Step 5: 分析
  □ 确认最后一个有效状态 (挂死时的 LTSSM 状态)
  □ 回溯状态跳变序列, 定位异常跳变
  □ 对比预期序列 (正常: Detect→Polling→Config→L0)
```

### 8.2 常见异常模式

| 模式 | 现象 | 可能原因 |
|------|------|----------|
| 停在 Detect.Quiet | LTSSM 未启动 | `app_ltssm_enable` 未写 1, PHY 未就绪, PERST# 未释放 |
| 停在 Polling.Active | 接收端检测未完成 | PHY 未 lock, lane 连接问题 |
| 停在 Configuration | 链路协商失败 | Lane reversal 配置, link width 不匹配 |
| Recovery 反复出现 | 链路不稳定 | SI 问题, 均衡失败, BER 高 |
| 大量 L0→Recovery→L0 | 频繁重训练 | 时钟抖动, 信号质量问题 |
| 停在 Hot Reset | RC 发起热复位 | 检查 hot_reset_int |

---

## 9. APB 访问地址映射

> 注：以下为示例，实际基地址以 `pcie_ss_ctl_reg` 地址 Map 为准。

| 偏移 | 寄存器 | 位宽 | 访问 | 说明 |
|------|--------|------|------|------|
| `BASE + 0x00` | `freeze_reg` | 1 | RW | freeze 控制 |
| `BASE + 0x04` ~ `BASE + 0x200` | `ltssm_state_reg[0]` ~ `ltssm_state_reg[127]` | 7 | RO | 每个 entry 7-bit (state[5:0] + vld[6]) |

---

## 10. 设计约定 (遵循 Spec 条款)

| 条款 | 内容 | 遵循方式 |
|------|------|----------|
| `PCIE_MAS_REQ_DEBUG_2301` | LTSSM debug shifter, 128 条最新状态 | 所有测试覆盖 shift_reg 深度 = 128 及 eviction 行为 |
| `layered-architecture §2` | L0 无损, L1 可寻址 | state_reg 为 APB 可寻址寄存器, ltssm_state 完整保留 |
| `CLAUDE.md §4` | 提交前测试全绿 | DV 环境回归包含 debug_01~debug_15 |

---

## 附录 A: LTSSM State 完整跳转图 (PCIe Base Spec 5.0)

```
                    ┌──────────┐
        reset →────►│ Detect   │◄──────────────────────────────┐
                    └────┬─────┘                               │
                         │ RX detected                         │
                         ▼                                     │
                    ┌──────────┐                               │
                    │ Polling  │                               │
                    └────┬─────┘                               │
                         │ TS1/TS2 ordered sets                │
                         ▼                                     │
                    ┌──────────┐                               │
              ┌────►│  Config  │                               │
              │     └────┬─────┘                               │
              │          │ link up                             │
              │          ▼                                     │
              │     ┌──────────┐    ASPM/L1 entry     ┌─────┐  │
              │     │    L0    │─────────────────────►│ L0s │  │
              │     └────┬─────┘◄─────────────────────┴──┬──┘  │
              │          │                                │    │
              │          │ ASPM/PM L1                    │    │
              │          ▼                                │    │
              │     ┌──────────┐                          │    │
              │     │    L1    │──────────────────────────┘    │
              │     └────┬─────┘                               │
              │          │ exit L1                             │
              │          ▼                                     │
              │     ┌──────────┐    Hot Reset TS1     ┌──────┐ │
              └─────│ Recovery │────────────────────►│ Hot  │ │
                    └────┬─────┘                      │Reset │ │
                         │                            └──┬───┘ │
                         │ link down / disabled            │    │
                         ▼                                 │    │
                    ┌──────────┐                           │    │
                    │ Disabled │───────────────────────────┘    │
                    └──────────┘                                │
                                                                │
                    ┌──────────┐                                │
                    │ Loopback │ (测试专用，不进主流程)           │
                    └──────────┘                                │
```
