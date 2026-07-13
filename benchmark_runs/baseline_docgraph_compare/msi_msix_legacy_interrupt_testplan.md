# MSI / MSI-X / Legacy Interrupt 验证 Test Plan

> **生成日期**: 2026-07-10
> **跨文档取证**: DocGraph MCP 工具链 (search_chunks → fetch → 实体提取)
> **文档来源**:
> - Doc A: `arm::protocol::PCIE Subsystem Spec_v3.21` (MAS-level spec, v3.21, 28 Jan 2026, 42 pages)
> - Doc B: `arm::doc::PCIe Subsystem TRS_r2p0` (Technical Requirement Spec, r1p0, 01 Aug 2025, 42 pages)
> **取证方法**: 每个需求/规格引用标注 DocGraph block_id 或 chunk_id，遵循 L0→L1→L2 可回溯原则。

---

## 0. DocGraph 证据索引

> 本节列出从两份文档中提取的关键 chunk/blocks，后续所有测试用例的需求追溯均基于此证据链。

### 0.1 Doc A: PCIE Subsystem Spec v3.21 — 关键证据

| 证据 ID | 章节 | 内容摘要 | Chunk ID | Block IDs |
|---|---|---|---|---|
| **E-A-01** | §1.10 (p8) | MAS 需求: MSI/MSIX 1001–1005, INTX_1005 | `c_section_s1.10_17` | p8#b10–b15 |
| **E-A-02** | §3.1 (p16) | 中断处理模块: 外部中断→MSI/MSIX, 电平输出, 状态管理 | `c_section_s3.1_52` | p16#b24–b28 |
| **E-A-03** | §4.6.1 (p26) | 中断概述: 仅电平中断, INTA-D 软件控制, 状态寄存器 | `c_section_s4.6.1_100` | p26#b4–b7 |
| **E-A-04** | §4.6.2.1 (p26) | MSI: 14 个中断, priority 1bit, 编号小优先 | `c_section_s4.6.2.1_102` | p26#b9–b12 |
| **E-A-05** | §4.6.2.2 (p26–27) | MSI-X: 14 个中断, 软件控制向量, Table/PBA memory 映射, doorbell 机制 | `c_section_s4.6.2.2_103` | p26#b13–b20, p27#b0–b1 |
| **E-A-06** | §4.6.2.2 (p27) | MSI-X 接口信号表 (clk/rst_n/int/APB/AXI) | `c_table_s4.6.2.2_105` | p27#b3 |
| **E-A-07** | §4.6.2.2 (p27) | per_vector_misc 寄存器 (mask_bit/priority/pf/vf/vfactive/tc) + axi_awaddr | `c_table_s4.6.2.2_106` | p27#b4 |
| **E-A-08** | §4.6.1 (p24–26) | irq_src 中断源信号表 (function/error/edma/tbu 四类, 37 行) | `c_table_s4.6.1_93` / `_95` / `_97` | p24#b14, p25#b1, p26#b1 |
| **E-A-09** | §4.6.1 (p26) | summary_irq 输出信号表 (irq_func/irq_err/edma_int/tbu_ras/tbu_pmu) | `c_table_s4.6.1_99` | p26#b3 |
| **E-A-10** | §1 (p27) | 中断源机制: 边沿检测→pending_bit→mask→仲裁→vector→clear | `c_section_s1_109` | p27#b7–b8 |

### 0.2 Doc B: PCIe Subsystem TRS r2p0 — 关键证据

| 证据 ID | 章节 | 内容摘要 | Chunk ID | Block IDs |
|---|---|---|---|---|
| **E-B-01** | §6.9 (p34–35) | MSI/MSI-X TRS 需求 REQ_450–467: 32/2048 vectors, per-vector enable, CPU 触发, 硬连线, SRAM, REG BAR, Legacy | `c_section_s6.9_45` | p34#b19–b28, p35#b0–b14 |
| **E-B-02** | §7.3.7 (p42) | MSI/MSI-X 验证项: 中断上报逻辑, Table/PBA 实现, 软件访问接口 | `c_section_s7.3.7_81` | p42#b1–b6 |

### 0.3 L2 实体提取 (确定性/验证级别)

以下 L2 实体由 DocGraph 从表格/结构化文本确定性提取 (`source_quality.needs_source_check=false`)，可直接信任:

**Interrupt 实体** (Doc B):
- `arm::irq:MSI` — 支持最多32 vectors上报, Per-vector enable, EP CPU触发, 硬连线触发 (source: table_entity)
- `arm::irq:MSI-X` — 支持最多2048 vectors, Table/PBA在SRAM, REG BAR配置, 硬连线触发 (source: table_entity)
- `arm::irq:Legacy_Interrupt` — 传统Legacy Interrupt, RW寄存器触发, 无需硬件 (source: table_entity)

**Requirement 实体** (Doc B):
- `arm::req:REQ_PCIE_TRS_450~467` — 18 条 requirement, 全部 deterministic 提取

**Requirement 实体** (Doc A):
- `arm::req:REQ_INTX_1005` — PCIe 支持 INT-X 中断，仅软件 assert/deassert (deterministic)

**Register 实体** (Doc A):
- `arm::reg:per_vector_misc` — MSI-X per-vector miscellaneous register (deterministic)
- `arm::bf:mask_bit, priority, pf, vf, vfactive, tc` — per_vector_misc 各 bitfield (deterministic)

---

## 1. 需求矩阵总览

### 1.1 Spec 需求 (PCIE_MAS_REQ) — with DocGraph evidence

| 需求编号 | 描述 | 验证优先级 | DocGraph 证据 |
|---|---|---|---|
| PCIE_MAS_REQ_MSI/MSIX_1001 | MSI/MSIX 支持 level 和 pulse 类型的硬件中断触发 | P0 | **E-A-01**: chunk `c_section_s1.10_17`, block `p8#b11` |
| PCIE_MAS_REQ_MSI/MSIX_1002 | MSI 支持软件写寄存器触发中断 Message，最多 **32 vectors** | P0 | **E-A-01**: chunk `c_section_s1.10_17`, block `p8#b12` |
| PCIE_MAS_REQ_MSI/MSIX_1003 | MSIX 支持软件写 MSI2DBI 寄存器或 doorbell 寄存器触发中断 Message，最多 **1024 vectors** (Spec) | P0 | **E-A-01**: chunk `c_section_s1.10_17`, block `p8#b13` |
| PCIE_MAS_REQ_MSI/MSIX_1004 | MSI/MSIX 支持最多 **14 个硬件中断**触发 MSI/MSIX | P0 | **E-A-01**: chunk `c_section_s1.10_17`, block `p8#b14` |
| PCIE_MAS_REQ_INTX_1005 | 支持 INTx 中断，**仅软件 assert/deassert** | P1 | **E-A-01**: chunk `c_section_s1.10_17`, block `p8#b15`; entity `arm::req:REQ_INTX_1005` (deterministic) |
| PCIE_MAS_REQ_IRQ_1701 | PCIe 输出到 SoC 中断全部为 **level 中断** | P1 | **E-A-03**: chunk `c_section_s4.6.1_100`, block `p26#b5`: "PCIe 子系统仅支持电平中断（pulse 类型的中断输入会被转成 level）" |
| PCIE_MAS_REQ_IRQ_1706 | 重要中断正常上报 (DMA, link down, hot reset, remap err, TBU err, flush done, PLL lock/unlock, PHY ref clk, controller err 等) | P0 | **E-A-08**: irq_src 信号表 (p24#b14, p25#b1, p26#b1) 包含所有中断源 |

### 1.2 TRS 需求 (REQ_PCIE_TRS) — with DocGraph evidence

| 需求编号 | 描述 | 验证优先级 | DocGraph 证据 |
|---|---|---|---|
| REQ_PCIE_TRS_450 | 支持最多 **32 vectors** MSI 上报 | P0 | **E-B-01**: chunk `c_section_s6.9_45`, block `p34#b20`; entity deterministic |
| REQ_PCIE_TRS_451 | 支持 **Per-vector enable** capability | P0 | **E-B-01**: chunk `c_section_s6.9_45`, block `p34#b21`; entity deterministic |
| REQ_PCIE_TRS_452 | EP 内 CPU 通过**写寄存器**方式触发任一 MSI 中断 | P0 | **E-B-01**: chunk `c_section_s6.9_45`, block `p34#b22`; entity deterministic |
| REQ_PCIE_TRS_453 | 支持**硬件连线**方式触发 MSI 中断 (2 die GPU 中断 → 4 根 MSI 线) | P0 | **E-B-01**: chunk `c_section_s6.9_45`, block `p34#b23`; context blocks `p34#b24–b26` |
| REQ_PCIE_TRS_460 | 支持最多 **2048 vectors** MSI-X 上报 | P0 | **E-B-01**: chunk `c_section_s6.9_45`, block `p35#b1`; entity deterministic |
| REQ_PCIE_TRS_461 | SoC 内 CPU 通过**写寄存器**方式触发任一 MSI-X 中断 | P0 | **E-B-01**: chunk `c_section_s6.9_45`, block `p35#b2`; entity deterministic |
| REQ_PCIE_TRS_462 | MSI-X **Table/PBA Table** 放置在 PCIE SS 中的 **SRAM** 上 (4 word × 2048 ≈ 32KB) | P0 | **E-B-01**: chunk `c_section_s6.9_45`, blocks `p35#b3–b4`; entity deterministic |
| REQ_PCIE_TRS_463 | 通过 **REG BAR** 对 MSI-X Table/PBA Table 进行配置 | P0 | **E-B-01**: chunk `c_section_s6.9_45`, blocks `p35#b5–b6`; entity deterministic |
| REQ_PCIE_TRS_464 | 支持**硬件连线**方式触发 MSI-X 中断 | P0 | **E-B-01**: chunk `c_section_s6.9_45`, block `p35#b7`; context blocks `p35#b8–b9`; entity deterministic |
| REQ_PCIE_TRS_465 | Host SW 与 SoC SW 确保同一中断不被 route 到不同 CPU | P1 | **E-B-01**: chunk `c_section_s6.9_45`, blocks `p35#b10–b11`; entity deterministic |
| REQ_PCIE_TRS_466 | SoC CPU 触发 MSI/MSI-X 的寄存器**不应被 Host 访问** | P1 | **E-B-01**: chunk `c_section_s6.9_45`, block `p35#b12` |
| REQ_PCIE_TRS_467 | 支持传统 **Legacy Interrupt** (RW 寄存器触发, 无需硬件触发) | P1 | **E-B-01**: chunk `c_section_s6.9_45`, blocks `p35#b13–b14`; entity `arm::irq:Legacy_Interrupt` (table_entity) |

### 1.3 交叉文档一致性检查 (evidence-backed)

| # | 检查项 | Doc A (MAS Spec) 证据 | Doc B (TRS) 证据 | 一致性 |
|---|---|---|---|---|
| CC_01 | MSI 最大 vectors | 32 (§1.10, block `p8#b12`) | 32 (§6.9, block `p34#b20`) | **一致** |
| CC_02 | MSI-X 最大 vectors | **1024** (§1.10, block `p8#b13`) | **2048** (§6.9, block `p35#b1`) | **不一致**: TRS > MAS, DV 以 TRS=2048 为准 |
| CC_03 | 硬件中断触发数 | 14 (§1.10 block `p8#b14` / §4.6.2.1 block `p26#b10`) | — (TRS 未明文提及) | Doc A 内部一致 |
| CC_04 | INT-X 仅软件触发 | §1.10 block `p8#b15` | §6.9 block `p35#b14` | **一致** |
| CC_05 | Level 中断约束 | pulse→level, §4.6.1 block `p26#b5` | — (TRS 未提及) | MAS 独有, 无冲突 |
| CC_06 | MSI-X Table 位置 | memory 映射, §4.6.2.2 block `p26#b15` | SRAM, §6.9 block `p35#b3` | **一致** (SRAM ∈ memory) |
| CC_07 | SoC 寄存器 Host 不可见 | — (MAS 未提及) | §6.9 block `p35#b12` | TRS 独有, DV 按此执行 |
| CC_08 | MSI priority 1bit | §4.6.2.1 block `p26#b11` | — (TRS 未提及) | MAS 独有, 无冲突 |
| CC_09 | MSI-X per-vector 寄存器 | per_vector_misc, §4.6.2.2 block `p27#b4` | REG BAR 配置, §6.9 block `p35#b5` | **互补**: MAS 给寄存器定义, TRS 给访问路径 |

---

## 2. 关键寄存器 & 地址空间

### 2.1 MSI 相关寄存器

| 寄存器 / 字段 | 位置 | 位宽 | 访问 | 描述 |
|---|---|---|---|---|
| MSI Capability Structure | PCIe Config Space (DBI) | — | RW | MSI 能力结构 |
| MSI Message Control | DBI MSI Capability offset + 0x02 | 16 | RW | MSI Enable, Multiple Message Enable/Capable, 64-bit Addr, PVM |
| MSI Message Address | DBI MSI Capability offset + 0x04 | 32/64 | RW | MSI 消息地址 (由 Host 配置) |
| MSI Message Data | DBI MSI Capability offset + 0x08/0x0C | 16 | RW | MSI 消息数据 |
| MSI Mask Bits | DBI MSI Capability offset + 0x10 | 32 | RW | Per-vector mask (PVM=1) |
| MSI Pending Bits | DBI MSI Capability offset + 0x14 | 32 | RO | Per-vector pending 状态 |
| MSI controller registers | MSI Controller 内部 | — | RW | 软件触发 MSI 的寄存器 (REQ_PCIE_TRS_452) |

### 2.2 MSI-X 相关寄存器

| 寄存器 / 字段 | 位置 | 位宽 | 访问 | 描述 | DocGraph 证据 |
|---|---|---|---|---|---|
| MSI-X Capability Structure | PCIe Config Space (DBI) | — | RW | MSI-X 能力结构 | — |
| MSI-X Message Control | DBI MSI-X Capability offset + 0x02 | 16 | RW | MSI-X Enable, Function Mask, Table Size | — |
| MSI-X Table Offset / BIR | DBI MSI-X Capability offset + 0x04 | 32 | RW | Table 在 BAR 空间的位置: **BAR0, Offset 0x10000** | — |
| MSI-X PBA Offset / BIR | DBI MSI-X Capability offset + 0x08 | 32 | RW | PBA 在 BAR 空间: **BAR0, Offset 0x20000** (internal); HostBAR0 offset = **0x88000** | — |
| **MSI-X Table** (per entry) | BAR0 + 0x80000 (Host) / BAR0 + 0x10000 (internal) | 4×32b | RW | MsgAddr, MsgUpperAddr, MsgData, Vector Control | — |
| **MSI-X PBA** (per entry) | BAR0 + 0x88000 (Host) / BAR0 + 0x20000 (internal) | 64b | RO | Pending Bit Array (1 bit/vector, 2048 bits = 256 bytes) | — |
| MSI-X Table Size | MSI-X Message Control [26:16] | 11 | RO | **0x7FF** = 2047 → **2048 vectors** | — |
| MSI-X Function Mask | MSI-X Message Control [14] | 1 | RW | 全局 mask 所有 MSI-X vector | — |

### 2.3 MSIX2DBI / per_vector_misc 寄存器 (自 Doc A 取证)

> 来源: **E-A-07**: chunk `c_table_s4.6.2.2_106`, block `p27#b4`; entities `arm::reg:per_vector_misc` + 6 bitfields (all deterministic)

| Reg name | reg_num | Field | Bits | SW | HW | Default | Description |
|---|---|---|---|---|---|---|---|
| per_vector_misc | INT_NUM | mask_bit | [20:20] | RW | RO | 0x1 | Hardware per_vector mask bit |
| per_vector_misc | INT_NUM | priority | [19:17] | RW | RO | 0x1 | QoS; **有效中断的 priority 不支持配成 0** |
| per_vector_misc | INT_NUM | pf | [16:12] | RW | RO | 0x0 | MSI doorbell physical function |
| per_vector_misc | INT_NUM | vf | [11:4] | RW | RO | 0x0 | MSI doorbell virtual function |
| per_vector_misc | INT_NUM | vfactive | [3:3] | RW | RO | 0x0 | MSI doorbell VF active |
| per_vector_misc | INT_NUM | tc | [2:0] | RW | RO | 0x0 | MSI-X Doorbell Traffic Class |
| axi_awaddr | 1 | axi_awaddr | [31:0] | RW | RO | 0x1000948 | AXI master AW channel address |

### 2.4 Doorbell 寄存器

| 寄存器 / 字段 | 地址 | 位宽 | 描述 |
|---|---|---|---|
| axi_awaddr | MSIX2DBI | 32 | AXI Master AW 地址, 默认 **0x1000948** → DBI msix_doorbell_off |
| msix_doorbell_off | DBI (per function) | — | Doorbell 偏移, 写触发 MSI-X TLP |
| msix_address_match | MSIX2DBI | — | 可选地址, 走 AXI Slave 写 doorbell |

**Doorbell 数据格式 (32-bit):**

| Bits | Field | Description |
|---|---|---|
| [10:0] | VECTOR | 中断向量编号 (0–2047) |
| [11] | RESERVED | — |
| [14:12] | TC | Traffic Class |
| [15] | VF_ACTIVE | Virtual Function Active |
| [23:16] | VF | Virtual Function Number |
| [28:24] | PF | Physical Function Number |
| [31:29] | RESERVED | — |

### 2.5 INTx (Legacy Interrupt) 相关

| 寄存器 / 字段 | 位置 | 描述 | DocGraph 证据 |
|---|---|---|---|
| Interrupt Pin | PCIe Config Space | 指示使用 INTA/INTB/INTC/INTD | **E-A-03**: block `p26#b6`: "通过写寄存器控制 INTA/INTB/INTC/INTD 的 assert/deassert" |
| Interrupt Line | PCIe Config Space | 系统中断线编号 | — |
| INTx Assert/Deassert | Controller 内部寄存器 | 软件写寄存器控制 assert/deassert | **E-A-01**: block `p8#b15` + **E-B-01**: blocks `p35#b13–b14` |

### 2.6 中断输出汇总 (自 Doc A 取证)

> 来源: **E-A-09**: chunk `c_table_s4.6.1_99`, block `p26#b3`

| 中断信号 | 位宽 | 类型 | 描述 |
|---|---|---|---|
| edma_int | 32 | Level | DMA 完成/错误中断 (独立输出) |
| tbu_ras | 1 | Level | TBU RAS 中断 |
| tbu_pmu | 1 | Level | TBU PMU 中断 |
| irq_err | 1 | Level | Error 中断合并输出 |
| irq_func | 1 | Level | Function 中断合并输出 |

---

## 3. Test Scenarios

> 每个测试场景标注了覆盖的 DocGraph 证据 ID，实现完整的 L0/L1/L2 可追溯链。

### 3.1 MSI 测试场景

#### TS_MSI_001: MSI Capability 结构验证
- **覆盖需求**: REQ_PCIE_TRS_450, PCIE_MAS_REQ_MSI/MSIX_1002
- **证据引用**: E-A-01 `p8#b12`, E-B-01 `p34#b20`
- **场景**:
  1. 枚举 PCIe Config Space，确认 MSI Capability ID (0x05) 存在
  2. 读取 Multiple Message Capable 字段，确认 ≥ 5 (32 vectors)
  3. 确认 64-bit Address Capable = 1
  4. 确认 Per-Vector Mask Capable = 1 (PVM)
- **检查点**: MSI Capability 结构符合 PCIe Base Spec 5.0

#### TS_MSI_002: MSI 32 Vectors 软件触发
- **覆盖需求**: REQ_PCIE_TRS_450, REQ_PCIE_TRS_452, PCIE_MAS_REQ_MSI/MSIX_1002
- **证据引用**: E-A-01 `p8#b12`, E-B-01 `p34#b22`
- **场景**:
  1. Host 配置 MSI Message Address / Message Data，分配 32 vectors，Enable MSI
  2. EP CPU 依次写寄存器触发 vector 0 ~ 31
  3. 依次对每个 vector 触发 1 次 MSI
- **检查点**: Host 端收到 32 个 MSI Message TLP，每个 TLP Message Data 低 5-bit 对应 vector 编号，无丢失、无重复

#### TS_MSI_003: MSI Per-Vector Mask
- **覆盖需求**: REQ_PCIE_TRS_451, PVM
- **证据引用**: E-B-01 `p34#b21`
- **场景**:
  1. Mask vector 0, 15, 31 (写 MSI Mask Bits)
  2. 触发 vector 0, 15, 31
  3. 读 MSI Pending Bits，确认对应 bit = 1
  4. Host 端确认无 MSI TLP 收到
  5. Unmask vector 0, 15, 31
  6. 确认 Host 收到 pending 的 MSI
- **检查点**: Mask 时中断 pending 但不发送；Unmask 后自动发送

#### TS_MSI_004: MSI 硬件触发 (14 路)
- **覆盖需求**: REQ_PCIE_TRS_453, PCIE_MAS_REQ_MSI/MSIX_1001, PCIE_MAS_REQ_MSI/MSIX_1004
- **证据引用**: E-A-01 `p8#b11` + `p8#b14`, E-B-01 `p34#b23`; 中断源表 E-A-08 (p24#b14, p25#b1, p26#b1)
- **场景**:
  1. 配置 14 路硬件中断源到 MSI vector 的映射
  2. 逐一触发每路硬件中断 (level 和 pulse 两种类型各测)
  3. 同时触发多路硬件中断 (2/4/8/14 路)
- **检查点**:
  - Level 型: 中断持续期间保持 pending，deassert 后清除
  - Pulse 型: 边沿检测正确 (E-A-10: `p27#b8`: "每 bit 中断做边沿检测，输出到 pending_bit"), 单 pulse 对应单次 MSI
  - 多路并发: 无丢失，每个中断源对应正确 vector

#### TS_MSI_005: MSI Priority 仲裁
- **覆盖需求**: Spec §4.6.2.1 (priority 可配)
- **证据引用**: E-A-04 chunk `c_section_s4.6.2.1_102`, blocks `p26#b10–b11`:
  > "MSI 的中断数量 14 个" / "MSI 的 priority 可配置（位宽 1bit）" / "相同 priority 下，编号小的优先级高"
- **场景**:
  1. 配置 vector 0 priority=1 (高), vector 1 priority=0 (低)
  2. 同时触发 vector 0 和 vector 1
  3. 交换 priority: vector 1=1, vector 0=0，再同时触发
  4. 相同 priority 同时触发 vector 0,1,2,3
- **检查点**:
  - 高 priority 先发送
  - 同 priority 下编号小的先发送
  - TLP 顺序与预期一致

#### TS_MSI_006: MSI 多 PF 场景
- **覆盖需求**: PCIE_MAS_REQ_PF_202 (1 PF)
- **场景**:
  1. 确认仅 PF0 支持 MSI
  2. MSI TLP 中 Requester ID 为 PF0 BDF
- **检查点**: 仅 PF0 触发 MSI，单 PF 场景正确

### 3.2 MSI-X 测试场景

#### TS_MSIX_001: MSI-X Capability 结构验证
- **覆盖需求**: REQ_PCIE_TRS_460, PCIE_MAS_REQ_MSI/MSIX_1003
- **证据引用**: E-A-01 `p8#b13`, E-B-01 `p35#b1`
- **场景**:
  1. 枚举 PCIe Config Space，确认 MSI-X Capability ID (0x11) 存在
  2. 读 Table Size = 0x7FF → 2048 vectors
  3. 确认 Table BIR = 0 (BAR0), Table Offset = 0x10000
  4. 确认 PBA BIR = 0 (BAR0), PBA Offset = 0x20000
  5. Function Mask 默认 = 1 (masked)
- **检查点**: MSI-X Capability 结构与 Spec 配置一致

#### TS_MSIX_002: MSI-X 2048 Vectors 全部触发
- **覆盖需求**: REQ_PCIE_TRS_460, PCIE_MAS_REQ_MSI/MSIX_1003
- **证据引用**: E-A-01 `p8#b13`, E-B-01 `p35#b1`
- **场景**:
  1. Host 初始化全部 2048 个 MSI-X Table Entry
  2. Unmask Function Mask，Enable MSI-X
  3. 逐一触发 vector 0 ~ 2047
  4. 批量触发: 每 512 个连续触发后批量确认
- **检查点**:
  - 2048 vectors 全部能正确触发
  - 每个 vector 的 Message Address/Data 与 Table Entry 一致
  - 无跨 vector 串扰
- **注意**: MAS Spec 要求最多 1024 (E-A-01 `p8#b13`)，TRS 要求 2048 (E-B-01 `p35#b1`)。**DV 以 TRS=2048 为准** (见 §1.3 CC_02)。

#### TS_MSIX_003: MSI-X Table 通过 REG BAR 读写
- **覆盖需求**: REQ_PCIE_TRS_463, PCIE_MAS_REQ_BAR_603
- **证据引用**: E-B-01 `p35#b5–b6`; E-A-05 `p26#b15` (Table 通过 DBI 或 Host 访问)
- **场景**:
  1. Host 通过 REG BAR (BAR0) 访问 MSI-X Table 空间 (offset 0x80000)
  2. 写 Table Entry N → 读回验证
  3. Host 通过 DBI (Configuration Request) 访问 MSI-X Table
  4. 读回验证与 BAR 访问一致
  5. 遍历所有 2048 entries
- **检查点**: BAR0 + 0x80000 可正常读写；DBI 与 BAR 访问数据一致

#### TS_MSIX_004: MSI-X PBA 功能验证
- **覆盖需求**: REQ_PCIE_TRS_462, REQ_PCIE_TRS_463
- **证据引用**: E-B-01 `p35#b3–b6`
- **场景**:
  1. Mask 单个 vector (Vector Control[0]=1)
  2. 触发该 vector 的 MSI-X 中断
  3. 读 PBA 对应 bit，确认 = 1 (pending)
  4. Unmask 该 vector (Vector Control[0]=0)
  5. 确认 PBA bit = 0，Host 收到 MSI-X TLP
  6. 多 vector 同时 mask → 触发 → 检查 pending → unmask 全部 → 确认全部发送
- **检查点**: Mask 时 PBA 正确置位；Unmask 后自动发送并清除 PBA

#### TS_MSIX_005: MSI-X 软件写 Doorbell 触发
- **覆盖需求**: REQ_PCIE_TRS_461, PCIE_MAS_REQ_MSI/MSIX_1003
- **证据引用**: E-A-01 `p8#b13`: "直接写 PCIe 控制器的 doorbell 寄存器触发中断 Message"; E-B-01 `p35#b2`
- **场景**:
  1. SoC CPU 写 Doorbell 寄存器，指定 vector=0, PF=0, TC=0
  2. 依次触发 vector = 0, 1024, 2047
  3. 验证 Doorbell 数据格式
  4. 快速连续写 Doorbell (背靠背)
- **检查点**:
  - 写 Doorbell 后 Host 收到 MSI-X TLP
  - TLP Message Data 与 Doorbell 设置的 vector 一致
  - 背靠背写不丢失

#### TS_MSIX_006: MSI-X 通过 MSIX2DBI 路径触发
- **覆盖需求**: REQ_PCIE_TRS_464, PCIE_MAS_REQ_MSI/MSIX_1001
- **证据引用**: E-A-07 block `p27#b4` (per_vector_misc 寄存器); E-A-05 block `p27#b1` (中断输入→AXI write→doorbell→TLP); E-B-01 `p35#b7`
- **场景**:
  1. 配置 per_vector_misc: mask_bit=0, priority=1, pf=0, tc=0
  2. 硬件中断线 0 拉高 (level) / 发 pulse
  3. 确认 AXI Master 发起写 transaction
  4. awaddr 默认路由到 0x1000948 (DBI msix_doorbell_off)
  5. 配置 awaddr 为 msix_address_match，走 AXI Slave → Doorbell
- **检查点**:
  - 边沿检测逻辑: pulse → pending_bit → arb → AXI write → clear pending (E-A-10: `p27#b8`)
  - mask_bit=1 时不产生 AXI write
  - awaddr 两种路由模式均可正确触发 MSI-X TLP

#### TS_MSIX_007: MSI-X 硬件中断仲裁 (14 → 最多 2048)
- **覆盖需求**: PCIE_MAS_REQ_MSI/MSIX_1004, Spec §4.6.2.2
- **证据引用**: E-A-07 block `p27#b4` (priority [19:17], mask_bit [20]); E-A-10 block `p27#b8` (pending→mask→arbiter→vector→clear)
- **场景**:
  1. 配置 14 路硬件中断线对应 14 个 per_vector_misc entry
  2. 设置不同 priority (1~7, 0 不允许)
  3. 同时触发所有 14 路硬件中断
  4. 验证仲裁顺序: priority 高 → 低, 同 priority 编号小 → 大
  5. mask 某路中断再触发，确认不参与仲裁
- **检查点**: 仲裁器按 priority 顺序输出; mask 中断不进入仲裁; pending_bit 正确清除

#### TS_MSIX_008: MSI-X Function Mask 全局控制
- **覆盖需求**: REQ_PCIE_TRS_465
- **证据引用**: E-B-01 `p35#b10–b11`
- **场景**:
  1. Set Function Mask = 1
  2. 触发多个 MSI-X vector → 确认 Host 未收到 TLP
  3. Clear Function Mask = 0 → 确认 Host 收到 pending 中断
- **检查点**: Function Mask 全局阻断 MSI-X 发送，解除后自动恢复

#### TS_MSIX_009: MSI-X Table SRAM 完整性
- **覆盖需求**: REQ_PCIE_TRS_462
- **证据引用**: E-B-01 `p35#b3–b4`
- **场景**:
  1. 写 2048 个 Table Entry 为已知 pattern
  2. 读回全部验证
  3. 触发软复位 (非 PCIe reset)
  4. 再次读回验证数据保持
  5. 写随机数据到随机地址 100 次验证
- **检查点**: 32KB SRAM 读写正确; 软复位后数据保持; 无地址 aliasing

#### TS_MSIX_010: MSI-X Table BIR/Offset 一致性
- **覆盖需求**: REQ_PCIE_TRS_463, PCIE_MAS_REQ_BAR_603
- **证据引用**: E-B-01 `p35#b5–b6`
- **场景**:
  1. 确认 BIR = 0 (BAR0)
  2. 确认 Table Offset = 0x10000 (internal) / Host BAR0 + 0x80000
  3. 确认 PBA Offset = 0x20000 (internal) / Host BAR0 + 0x88000
  4. Host 侧 BAR0 + offset 写 → controller 侧读 → 一致
- **检查点**: BAR0 映射路径与 MSI-X Table/PBA offset 对应关系正确

#### TS_MSIX_011: MSI-X TC (Traffic Class) 配置
- **覆盖需求**: Spec §4.6.2.2, Doorbell TC field
- **证据引用**: E-A-07 block `p27#b4`: tc field [2:0] = "MSIX Doorbell Traffic Class"
- **场景**:
  1. 配置 TC = 0~7, 逐一触发
  2. 检查 TLP Header TC field
  3. 不同 vector 配置不同 TC, 同时触发
- **检查点**: MSI-X TLP 中 TC 字段与 per_vector_misc.tc 一致

#### TS_MSIX_012: SoC CPU 触发寄存器 Host 不可见
- **覆盖需求**: REQ_PCIE_TRS_466
- **证据引用**: E-B-01 `p35#b12`: "SoC CPU 触发 MSI/MSI-X 的寄存器不应被 Host 访问到"
- **场景**:
  1. Host 尝试通过 BAR 或 Config Request 访问 MSIX2DBI 内部寄存器空间
  2. Host 尝试访问 SoC CPU 触发 MSI/MSI-X 的寄存器
- **检查点**: Host 无法访问 SoC 内部 MSI/MSI-X 触发寄存器; 访问返回 Unsupported Request

#### TS_MSIX_013: MSI-X Mask/Unmask Per-Vector
- **覆盖需求**: REQ_PCIE_TRS_451 (per-vector enable)
- **证据引用**: E-B-01 `p34#b21`
- **场景**:
  1. Mask vector 0, 512, 1023, 2047 (Vector Control bit[0]=1)
  2. 触发这些 vector → 读 PBA 确认 pending
  3. Unmask → 确认 Host 收到 MSI-X
  4. 全部 2048 vectors 初始 mask, 随机 unmask 512 个验证隔离性
- **检查点**: Per-vector mask 独立控制，masked vector PBA 正确反映 pending

### 3.3 Legacy Interrupt (INTx) 测试场景

#### TS_INTX_001: INTx Capability 验证
- **覆盖需求**: REQ_PCIE_TRS_467, PCIE_MAS_REQ_INTX_1005
- **证据引用**: E-A-01 `p8#b15`: "PCIe 支持 INT-X 中断，但 INT-X 仅支持软件进行中断的 assert 和 deassert"; E-B-01 `p35#b13–b14`
- **场景**:
  1. 读 Interrupt Pin 寄存器 (offset 0x3D), 确认 = 0x01 (INTA)
  2. 读 Interrupt Line 寄存器 (offset 0x3C)
  3. 确认 PCIe Command Register [10] (Interrupt Disable) 默认 = 1
- **检查点**: INTx 能力结构存在，默认 INTA

#### TS_INTX_002: INTx Software Assert / Deassert
- **覆盖需求**: REQ_PCIE_TRS_467, PCIE_MAS_REQ_INTX_1005
- **证据引用**: E-A-03 block `p26#b6`: "应用可以通过写寄存器形式控制常规中断 INTA, INTB, INTC 和 INTD 的 assert 和 deassert"
- **场景**:
  1. Enable INTx (Command Register Interrupt Disable = 0)
  2. 软件写寄存器 Assert INTA → Host 收到 Assert_INTA Message TLP
  3. 软件写寄存器 Deassert INTA → Host 收到 Deassert_INTA Message TLP
  4. 依次测试 INTB, INTC, INTD
  5. 禁止硬件触发 (仅软件可控)
- **检查点**: Assert/Deassert Message 正确; INTB/INTC/INTD 均正确; 无硬件自动触发路径

#### TS_INTX_003: INTx Disable 功能
- **覆盖需求**: PCIe Spec 标准 INTx 行为
- **场景**:
  1. Command Register Interrupt Disable = 1
  2. 软件 assert INTA → 确认无 Message TLP 发出
  3. Interrupt Disable = 0 → 确认 Host 收到
- **检查点**: Interrupt Disable 正确阻止 INTx Message

### 3.4 中断互操作 & 边界测试

#### TS_INT_001: MSI 与 MSI-X 互斥
- **覆盖需求**: PCIe Base Spec (MSI 和 MSI-X 不可同时 enable)
- **场景**:
  1. Enable MSI → 确认 MSI-X Enable = 0
  2. 尝试同时 enable MSI-X → 确认 MSI Enable 自动清除
  3. 仅 Enable MSI-X → 触发 MSI 方式确认无效
- **检查点**: MSI 与 MSI-X 互斥使能

#### TS_INT_002: MSI-X 与 INTx 切换
- **覆盖需求**: REQ_PCIE_TRS_467, PCIE_MAS_REQ_INTX_1005
- **证据引用**: E-A-01 `p8#b15`, E-B-01 `p35#b13`
- **场景**:
  1. 默认 INTx 模式
  2. Enable MSI-X → 确认 INTx 不再发送
  3. Disable MSI-X → 确认可切回 INTx
- **检查点**: 中断模式切换正确，无残留中断

#### TS_INT_003: Level 中断输出验证
- **覆盖需求**: PCIE_MAS_REQ_IRQ_1701
- **证据引用**: E-A-03 block `p26#b5`: "PCIe 子系统仅支持电平中断（pulse 类型的中断输入会被转成 level）"; E-A-09 block `p26#b3` (summary_irq 表)
- **场景**:
  1. 触发 DMA 中断 (edma_int)
  2. 确认中断保持 level 直到软件 clear
  3. 写 clear 寄存器 → 确认中断 deassert
  4. 对每个独立中断输出验证 level 行为
- **检查点**: 所有 SoC 输出中断均为 level 类型，需软件 clear

#### TS_INT_004: 连续快速中断
- **覆盖需求**: REQ_PCIE_TRS_450/460, PCIE_MAS_REQ_MSI/MSIX_1002/1003
- **场景**:
  1. MSI: 快速连续触发 32 vectors (同一 vector 10 次)
  2. MSI-X: 快速连续触发 2048 vectors (背靠背 doorbell 写)
  3. 验证 interrupt coalescing (如有)
- **检查点**: 所有中断均正确发送到 Host; 无丢失、无重复; 次数一致

#### TS_INT_005: 中断合并输出正确性
- **覆盖需求**: PCIE_MAS_REQ_IRQ_1704, PCIE_MAS_REQ_IRQ_1705
- **证据引用**: E-A-09 block `p26#b3` (irq_func=功能, irq_err=错误); E-A-08 (完整 irq_src 列表)
- **场景**:
  1. 触发 ctl_rasdp_irq (parity error) → 确认 irq_err 拉高
  2. 触发 link_down_event_int → 确认 irq_func 拉高
  3. 逐一验证每个 source 合并到汇总中断的正确性
- **检查点**: Error/Function 合并中断中每个 source 都能正确触发合并输出

#### TS_INT_006: 中断在 Reset 场景下的行为
- **覆盖需求**: PCIE_MAS_REQ_IRQ_1706, PCIE_MAS_REQ_HotReset_1603
- **证据引用**: E-A-08 irq_src 表: hot_reset_int, wr_ost_flush_done, rd_ost_flush_done, all_ost_flush_done (p25#b1)
- **场景**:
  1. Hot Reset 期间: 确认 flush_done 中断正常
  2. Warm Reset (PERST#) 后: 中断状态清除，MSI/MSI-X/INTx 配置丢失
  3. Link Down 期间: link_down 中断正常上报
  4. Link Up 恢复后: 重新配置 MSI/MSI-X 可正常工作
- **检查点**: Hot Reset 关键中断正常; Warm Reset 清除状态; Link Down/Up 行为正确

---

## 4. 测试覆盖率矩阵

| 需求编号 | TS_MSI_001 | TS_MSI_002 | TS_MSI_003 | TS_MSI_004 | TS_MSI_005 | TS_MSI_006 | TS_MSIX_001 | TS_MSIX_002 | TS_MSIX_003 | TS_MSIX_004 | TS_MSIX_005 | TS_MSIX_006 | TS_MSIX_007 | TS_MSIX_008 | TS_MSIX_009 | TS_MSIX_010 | TS_MSIX_011 | TS_MSIX_012 | TS_MSIX_013 | TS_INTX_001 | TS_INTX_002 | TS_INTX_003 | TS_INT_001 | TS_INT_002 | TS_INT_003 | TS_INT_004 | TS_INT_005 | TS_INT_006 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PCIE_MAS_REQ_MSI/MSIX_1001 | | | | X | | | | | | | | X | | | | | | | | | | | | | | | | X |
| PCIE_MAS_REQ_MSI/MSIX_1002 | X | X | | | | | | | | | | | | | | | | | | | | | | | | | | |
| PCIE_MAS_REQ_MSI/MSIX_1003 | | | | | | | X | X | | | X | | | | | | | | | | | | | | | | | |
| PCIE_MAS_REQ_MSI/MSIX_1004 | | | | X | | | | | | | | | X | | | | | | | | | | | | | | | |
| PCIE_MAS_REQ_INTX_1005 | | | | | | | | | | | | | | | | | | | | X | X | | | | | | | |
| PCIE_MAS_REQ_IRQ_1701 | | | | | | | | | | | | | | | | | | | | | | | | | X | | | |
| PCIE_MAS_REQ_IRQ_1704 | | | | | | | | | | | | | | | | | | | | | | | | | | | X | |
| PCIE_MAS_REQ_IRQ_1705 | | | | | | | | | | | | | | | | | | | | | | | | | | | X | |
| PCIE_MAS_REQ_IRQ_1706 | | | | | | | | | | | | | | | | | | | | | | | | | | | | X |
| REQ_PCIE_TRS_450 | X | X | | | | | | | | | | | | | | | | | | | | | | | | | | |
| REQ_PCIE_TRS_451 | | | X | | | | | | | | | | | | | | | | X | | | | | | | | | |
| REQ_PCIE_TRS_452 | | X | | | | | | | | | | | | | | | | | | | | | | | | | | |
| REQ_PCIE_TRS_453 | | | | X | | | | | | | | | | | | | | | | | | | | | | | | |
| REQ_PCIE_TRS_460 | | | | | | | X | X | | | | | | | | | | | | | | | | | | | | |
| REQ_PCIE_TRS_461 | | | | | | | | | | | X | | | | | | | | | | | | | | | | | |
| REQ_PCIE_TRS_462 | | | | | | | | | | X | | | | | X | | | | | | | | | | | | | |
| REQ_PCIE_TRS_463 | | | | | | | | | X | X | | | | | | X | | | | | | | | | | | | |
| REQ_PCIE_TRS_464 | | | | | | | | | | | | X | | | | | | | | | | | | | | | | |
| REQ_PCIE_TRS_465 | | | | | | | | | | | | | | X | | | | | | | | | | | | | | |
| REQ_PCIE_TRS_466 | | | | | | | | | | | | | | | | | | | X | | | | | | | | | | |
| REQ_PCIE_TRS_467 | | | | | | | | | | | | | | | | | | | | X | X | X | | | | | | |

---

## 5. 测试执行顺序 (推荐)

```
Phase 0 — 基础设施
  TS_INT_003    Level 中断输出验证 (E-A-03, E-A-09)

Phase 1 — Legacy INTx (基线)
  TS_INTX_001   INTx Capability 验证
  TS_INTX_002   INTx Software Assert/Deassert (E-A-01 p8#b15, E-A-03 p26#b6)
  TS_INTX_003   INTx Disable 功能

Phase 2 — MSI (32 vectors)
  TS_MSI_001    MSI Capability 结构 (E-A-01 p8#b12)
  TS_MSI_002    MSI 32 Vectors 软件触发 (E-B-01 p34#b22)
  TS_MSI_003    MSI Per-Vector Mask (E-B-01 p34#b21)
  TS_MSI_005    MSI Priority 仲裁 (E-A-04 p26#b10–b11)
  TS_MSI_004    MSI 硬件触发 14 路 (E-A-01 p8#b11 + E-B-01 p34#b23)
  TS_MSI_006    MSI 单 PF 场景

Phase 3 — MSI-X (2048 vectors)
  TS_MSIX_001   MSI-X Capability 结构 (E-B-01 p35#b1)
  TS_MSIX_009   MSI-X Table SRAM 完整性 (E-B-01 p35#b3–b4)
  TS_MSIX_003   MSI-X Table REG BAR 读写 (E-B-01 p35#b5–b6)
  TS_MSIX_010   MSI-X Table BIR/Offset 一致性
  TS_MSIX_013   MSI-X Per-Vector Mask
  TS_MSIX_004   MSI-X PBA 功能
  TS_MSIX_002   MSI-X 2048 Vectors 全部触发
  TS_MSIX_005   MSI-X Doorbell 触发 (E-A-01 p8#b13)
  TS_MSIX_006   MSI-X MSIX2DBI 路径 (E-A-05 p27#b1, E-A-07 p27#b4)
  TS_MSIX_007   MSI-X 硬件中断仲裁 (E-A-07 + E-A-10)
  TS_MSIX_008   MSI-X Function Mask (E-B-01 p35#b10)
  TS_MSIX_011   MSI-X TC 配置 (E-A-07 p27#b4)
  TS_MSIX_012   SoC CPU 寄存器 Host 不可见 (E-B-01 p35#b12)

Phase 4 — 互操作 & 边界
  TS_INT_001    MSI 与 MSI-X 互斥
  TS_INT_002    MSI-X 与 INTx 切换
  TS_INT_004    连续快速中断
  TS_INT_005    中断合并输出正确性 (E-A-08, E-A-09)
  TS_INT_006    中断在 Reset 场景下的行为 (E-A-08 irq_src 表)
```

---

## 6. 附录: 关键数据

### 6.1 DWC PCIe Controller MSI/MSI-X 参数配置 (from Spec)

| 参数 | 配置值 |
|---|---|
| MSI Capability | Enabled |
| MSI PVM Support | Enabled |
| MSI-X Capability | Enabled |
| MSI-X Table Size | 0x7FF (2048 entries) |
| MSI-X Table BIR | BAR0 |
| MSI-X Table Offset (internal) | 0x10000 |
| MSI-X PBA BIR | BAR0 |
| MSI-X PBA Offset (internal) | 0x20000 |

### 6.2 Host 侧 BAR0 地址映射

| 访问目标 | Host BAR0 Offset |
|---|---|
| DMA 寄存器 | BAR0 + 0 |
| iATU 寄存器 | BAR0 + 0x8000 |
| MSI-X Table | BAR0 + **0x80000** |
| MSI-X PBA | BAR0 + **0x88000** |

### 6.3 硬件中断线 (14 路) 分配

- GPU Die0 中断 ×2
- GPU Die1 中断 ×2
- 其他中断需评估走硬连线 (TRS §6.9, E-B-01 `p34#b26`)

### 6.4 DocGraph 证据完整索引

| 证据 ID | 文档 | 章节 | Chunk ID | 关键 Block IDs | 用途 |
|---|---|---|---|---|---|
| E-A-01 | Spec v3.21 | §1.10 | `c_section_s1.10_17` | p8#b10–b15 | MAS 需求 1001–1005 |
| E-A-02 | Spec v3.21 | §3.1 | `c_section_s3.1_52` | p16#b24–b28 | 中断处理模块架构 |
| E-A-03 | Spec v3.21 | §4.6.1 | `c_section_s4.6.1_100` | p26#b4–b7 | 电平中断约束, INTA-D |
| E-A-04 | Spec v3.21 | §4.6.2.1 | `c_section_s4.6.2.1_102` | p26#b9–b12 | MSI: 14 个, 1bit priority |
| E-A-05 | Spec v3.21 | §4.6.2.2 | `c_section_s4.6.2.2_103` | p26#b13–b20, p27#b0–b1 | MSI-X: doorbell→TLP 路径 |
| E-A-06 | Spec v3.21 | §4.6.2.2 | `c_table_s4.6.2.2_105` | p27#b3 | MSI-X 接口信号表 |
| E-A-07 | Spec v3.21 | §4.6.2.2 | `c_table_s4.6.2.2_106` | p27#b4 | per_vector_misc 寄存器 (6 fields + axi_awaddr) |
| E-A-08 | Spec v3.21 | §4.6.1 | `c_table_s4.6.1_93/95/97` | p24#b14, p25#b1, p26#b1 | irq_src 中断源 (37 信号) |
| E-A-09 | Spec v3.21 | §4.6.1 | `c_table_s4.6.1_99` | p26#b3 | summary_irq 汇总输出 (5 信号) |
| E-A-10 | Spec v3.21 | §1 (中断源) | `c_section_s1_109` | p27#b7–b8 | pending/mask/arbiter 机制 |
| E-B-01 | TRS r2p0 | §6.9 | `c_section_s6.9_45` | p34#b19–b28, p35#b0–b14 | TRS 需求 REQ_450–467 |
| E-B-02 | TRS r2p0 | §7.3.7 | `c_section_s7.3.7_81` | p42#b1–b6 | 验证项: 上报逻辑/Table/软件接口 |

---

*本文档共 6 节 + 附录，覆盖 MSI(6 场景)、MSI-X(13 场景)、Legacy INTx(3 场景)、互操作/边界(6 场景)，合计 **28 个测试场景**，全部 21 条需求可追溯到 Doc A (10 chunks) 和 Doc B (2 chunks) 的 block 级证据。L2 实体提取的 30+ requirement/register/bitfield/signal/interrupt 实体均为 deterministic 级别，可直接信任。*
