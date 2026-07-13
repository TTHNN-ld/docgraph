# PCIe 地址空间、BAR 与内部寄存器空间 DV Test Plan

> 编制依据：
> - **PCIe Subsystem Spec v3.21** (子系统内部地址映射、BAR 空间、DBI 位域、寄存器分区)
> - **PCIe Subsystem TRS r2p0** (系统级地址规划、BAR 类型/用途、Inbound/Outbound 约束)
> - 两份文档为互补关系：TRS 定义架构约束（"要什么"），Spec 定义实现细节（"怎么接"）。本 plan 以 TRS 约束为 check 目标，以 Spec 映射表为参考值。

---

## 1. 测试范围总览

| 测试域 | TRS 约束来源 | Spec 实现来源 | 测试重点 |
|--------|-------------|--------------|---------|
| 系统地址空间 | REQ_PCIE_TRS_020~022, §5.1 | §4.2 Address Map | Outbound 128T 窗口、Local vs 高地址段隔离 |
| BAR 空间 | REQ_PCIE_TRS_043~047, §5.2.1 | §4.3 BAR 空间, §4.2 Table 4-3 | BAR 数量/类型/大小/映射正确性 |
| Inbound/Outbound 访问规则 | REQ_PCIE_TRS_040~045, §5.2~5.3 | §4.3, §4.14 iATU | 地址转换、死锁防护、访问隔离 |
| 内部 NoC 地址映射 | (隐含) | §4.2 Table 4-2 | 9 个子模块空间基地址与大小 |
| DBI 地址空间 | REQ_PCIE_TRS_046~047 | §4.2 Table 4-3 | CDM/iATU/DMA/MSI-X Table/MSI-X PBA 位域译码 |
| 寄存器访问路径 | REQ_PCIE_TRS_046~047, REQ_PCIE_TRS_463 | §4.21, §3.1 | AXI-lite / BAR0 / CFG TLP 三路径一致性 |
| Reserved 空间行为 | PCIE_MAS_REQ_CFG_517 | §3.1 | 不挂死 |

---

## 2. 系统地址空间 Test Plan

### 2.1 Unified 地址空间 & Outbound 窗口

**TRS 约束：**
- REQ_PCIE_TRS_020: 全系统 unified 地址空间，不同 Die/Chip 独立寻址
- REQ_PCIE_TRS_021: Chip 内 Master 访问内部资源必须通过 Local 地址段（最低 512GB）；不能通过高 128T 空间
- REQ_PCIE_TRS_022: SoC 为 PCIe Outbound 分配 128T~256T 空间

| 测试项 | 测试描述 | 激励 | 期望结果 | 优先级 |
|--------|---------|------|---------|--------|
| **ADDR-001** | Outbound 窗口基地址验证 | 配置 Outbound iATU region，使能地址匹配模式，从 AXI Slave 发起落在 128T~256T 区间的访问 | TLP 正确发出，地址转换公式 `out_addr = in_addr - base + target` 成立 | P0 |
| **ADDR-002** | Outbound 窗口边界检查 | 发起地址 = 128T（窗口下边界）和 256T（窗口上边界）的访问 | 下边界命中，上边界（256T 本身为保留）正确处理 | P0 |
| **ADDR-003** | Local 地址段隔离 | Chip 内 Master 用高 128T 地址段访问本 Chip 资源 | 访问被拒绝或视为 Reserved，不产生正确 TLP | P1 |
| **ADDR-004** | 跨 Die 地址访问差异 | 从 Host 通过 Inbound 分别访问 Die0 和 Die1 的同一偏移地址 | 两路径均正确完成，但延迟差异应在可接受范围（TRS REQ_PCIE_TRS_008） | P1 |

### 2.2 内部 NoC 地址映射（Spec §4.2 Table 4-2）

**基地址：** AXI-lite = `0x2C000000`

| 测试项 | 测试描述 | 激励 | 期望结果 | 优先级 |
|--------|---------|------|---------|--------|
| **NOC-001** | Top CFG 空间 (offset 0x00000000, 1MB) | AXI-lite 读写 0x2C000000 ~ 0x2C0FFFFF | 正常读写子系统顶层寄存器（含 ltssm_enable, flush_time_ctrl 等） | P0 |
| **NOC-002** | CRG CFG 空间 (offset 0x00100000, 1MB) | AXI-lite 读写 0x2C100000 ~ 0x2C1FFFFF | PLL 配置寄存器正常访问 | P0 |
| **NOC-003** | PHY0 CFG 空间 (offset 0x00200000, 1MB) | AXI-lite 读写 0x2C200000 ~ 0x2C2FFFFF | PHY0 CR 寄存器正常访问 | P0 |
| **NOC-004** | PHY1 CFG 空间 (offset 0x00300000, 1MB) | AXI-lite 读写 0x2C300000 ~ 0x2C3FFFFF | PHY1 CR 寄存器正常访问 | P1 |
| **NOC-005** | PHY2 CFG 空间 (offset 0x00400000, 1MB) | AXI-lite 读写 0x2C400000 ~ 0x2C4FFFFF | PHY2 CR 寄存器正常访问 | P1 |
| **NOC-006** | PHY3 CFG 空间 (offset 0x00500000, 1MB) | AXI-lite 读写 0x2C500000 ~ 0x2C5FFFFF | PHY3 CR 寄存器正常访问 | P1 |
| **NOC-007** | Msix2Dbi CFG 空间 (offset 0x00600000, 1MB) | AXI-lite 读写 0x2C600000 ~ 0x2C6FFFFF | MSIX2DBI 寄存器正常访问 | P0 |
| **NOC-008** | ReMap CFG 空间 (offset 0x00700000, 1MB) | AXI-lite 读写 0x2C700000 ~ 0x2C7FFFFF | ReMap 寄存器正常访问 | P0 |
| **NOC-009** | DBI CFG 空间 (offset 0x01000000, 16MB) | AXI-lite 读写 0x2D000000 ~ 0x2DFFFFFF | DBI 寄存器（CDM/iATU/DMA/MSI-X）正常访问 | P0 |
| **NOC-010** | Reserved 空间访问不挂死 | AXI-lite 访问各子空间之间的 Reserved 区域（如 0x00800000~0x00FFFFFF） | 返回 Error 或 0，总线不挂死（PCIE_MAS_REQ_CFG_517） | P0 |
| **NOC-011** | 各空间大小边界 | 对每个 1MB/16MB 空间，读写其最后一个 word（offset + size - 4）和第一个超出 word（offset + size） | 边界内正常访问，边界外不产生副作用或挂死 | P1 |

---

## 3. BAR 空间 Test Plan

### 3.1 BAR 配置与属性

**Spec 配置 (Table 4-4, §4.1.1):**
- BAR0: 32-bit, Memory, Non-prefetchable, Resizable (default 1MB)
- BAR1: 32-bit, Memory, Non-prefetchable, Resizable (default 1MB)
- BAR2: 64-bit, Memory, Prefetchable, Resizable (default 1MB)
- BAR4: 64-bit, Memory, Prefetchable, Resizable (default 1MB)

**TRS 约束 (REQ_PCIE_TRS_043):**
- 1× 64bit MEM BAR (prefetchable) → seDRAM
- 1× 32bit REG BAR (non-prefetchable) → 低 4G 配置寄存器空间
- 1× 32bit BAR → PCIe Controller 内部寄存器访问
- (注：TRS 与 Spec 在 BAR 数量描述上有差异——TRS 说 3 个 BAR，Spec 说 4 个。需在设计评审中确认。DV 按 Spec 4 BAR 实现验证，同时覆盖 TRS 的 3 种功能角色。)

| 测试项 | 测试描述 | 激励 | 期望结果 | 优先级 |
|--------|---------|------|---------|--------|
| **BAR-001** | BAR0 属性验证 | RC 枚举读取 BAR0 配置寄存器 | 32-bit, Memory, Non-prefetchable, Resizable | P0 |
| **BAR-002** | BAR1 属性验证 | RC 枚举读取 BAR1 配置寄存器 | 32-bit, Memory, Non-prefetchable, Resizable | P0 |
| **BAR-003** | BAR2 属性验证 | RC 枚举读取 BAR2 配置寄存器 | 64-bit, Memory, Prefetchable, Resizable | P0 |
| **BAR-004** | BAR4 属性验证 | RC 枚举读取 BAR4 配置寄存器 | 64-bit, Memory, Prefetchable, Resizable | P0 |
| **BAR-005** | BAR 数量验证 | RC 枚举所有 BAR | 共 4 个 BAR（BAR0/1/2/4），BAR3 为 BAR2 的高 32bit，BAR5 为 BAR4 的高 32bit | P0 |

### 3.2 Resizable BAR

**TRS REQ_PCIE_TRS_241:** 支持 2 个 Resize BAR (1MB ~ 512GB)
**Spec PCIE_MAS_REQ_BAR_602:** 4 个 BAR 均为 ReSize BAR，默认 1MB

| 测试项 | 测试描述 | 激励 | 期望结果 | 优先级 |
|--------|---------|------|---------|--------|
| **RBAR-001** | 默认大小验证 | 上电后不配置 ReSize，读 BAR 实际大小 | 每个 BAR = 1MB | P0 |
| **RBAR-002** | BAR0 ReSize 到 2MB | 通过 ReSize BAR capability 将 BAR0 扩至 2MB | 新 BAR0 窗口 = 2MB，BAR0+0x00000~BAR0+0xFFFFF 内寄存器可访问，超出部分按原映射扩展 | P0 |
| **RBAR-003** | BAR0 ReSize 到 4KB (最小) | 尝试将 BAR0 缩至 4KB | 若硬件支持 4KB 粒度，仅 4KB 窗口有效；若不支持，返回 unsupported | P1 |
| **RBAR-004** | BAR2 ReSize 到 512GB | 通过 ReSize BAR capability 将 BAR2 扩至 512GB | 新 BAR2 窗口 = 512GB，可映射 SoC 大段地址空间（TRS REQ_PCIE_TRS_241） | P0 |
| **RBAR-005** | ReSize 后地址对齐 | 将 BAR0 扩至 4MB，检查 BAR 基地址 | BAR 基地址自然对齐到新 size（4MB 对齐） | P1 |
| **RBAR-006** | ReSize 过程中访问 BAR | 在 ReSize 配置过程中，Host 访问对应 BAR 空间 | 访问应被正确处理（返回 error 或等待重试），数据不损坏 | P1 |
| **RBAR-007** | 非 2 的幂次 size | 尝试配置 size = 3MB | 被硬件拒绝或不支持 | P2 |

### 3.3 BAR0 内部寄存器映射（Spec §4.3 Table 4-4）

| 测试项 | 测试描述 | 激励 | 期望结果 | 优先级 |
|--------|---------|------|---------|--------|
| **BAR0-001** | DMA 寄存器偏移 | Host 通过 BAR0 + DMA 寄存器偏移 (offset 0x00000) 读写 DMA 寄存器 | DMA 寄存器正确读写 | P0 |
| **BAR0-002** | iATU 寄存器偏移 | Host 通过 BAR0 + 0x8000 读写 iATU region 寄存器 | iATU 寄存器正确读写 | P0 |
| **BAR0-003** | MSI-X Table 偏移 (新) | Host 通过 BAR0 + 0x80000 读写 32KB MSI-X Table | MSI-X Table SRAM 正确读写，Table Size ≤ 0x7FF (2048 entries) | P0 |
| **BAR0-004** | MSI-X PBA 偏移 (新) | Host 通过 BAR0 + 0x88000 读写 32KB MSI-X PBA | MSI-X PBA 正确读写 | P0 |
| **BAR0-005** | BAR0 内部地址译码位域 | 根据 Spec Table 4-3，遍历 bit[22:12] 的不同组合 | bit22=0, bit21=0 → DMA; bit22=0, bit21=1, bit20=0 → iATU; bit22=1, bit21=1, bit20=0, bit[19:16]=0 → MSI-X Table; bit22=1, bit21=1, bit20=0, bit[19:16]=1 → MSI-X PBA | P0 |
| **BAR0-006** | BAR0 各子空间边界 | 分别在 DMA/iATU/MSI-X Table/MSI-X PBA 子空间的边界地址和超出地址发起访问 | 边界内正常访问，超出空间译码到相邻空间（或 reserved 处理） | P1 |

### 3.4 BAR1/BAR2/BAR4 — SoC 内存映射

**TRS 约束：**
- REQ_PCIE_TRS_043: 64bit MEM BAR → seDRAM; 32bit REG BAR → 低 4G 配置寄存器空间
- REQ_PCIE_TRS_044: Inbound 可访问两个 Die 的所有地址空间
- REQ_PCIE_TRS_045: Inbound 不可访问 PCIe Outbound 地址空间

| 测试项 | 测试描述 | 激励 | 期望结果 | 优先级 |
|--------|---------|------|---------|--------|
| **MEMBAR-001** | BAR1 映射到 SoC 低 4G 空间 | 配置 iATU/ReMap 使 BAR1 映射到 SoC 配置寄存器区，Host 通过 BAR1 访问 | 正确访问 SoC 寄存器空间 | P0 |
| **MEMBAR-002** | BAR2 映射到 seDRAM | 配置 BAR2 映射到 seDRAM 空间，Host 通过 BAR2 读写 | 数据正确往返 | P0 |
| **MEMBAR-003** | BAR4 映射到 seDRAM | 配置 BAR4 映射到 seDRAM 空间，大面积读写测试 | 数据正确往返 | P0 |
| **MEMBAR-004** | Prefetchable vs Non-prefetchable 语义 | 对 BAR1 (non-prefetchable) 和 BAR2 (prefetchable) 分别做读操作 | BAR1 读不应有 side effect；BAR2 读允许 prefetch，但读数据正确 | P1 |
| **MEMBAR-005** | Inbound 访问 Outbound 空间防护 | Host 通过 BAR 发起地址落在 128T~256T (Outbound 窗口) 的访问 | 返回 Decode Error，不产生 deadlock (TRS REQ_PCIE_TRS_045) | P0 |
| **MEMBAR-006** | Inbound 访问 C2C 地址空间防护 | Host 通过 BAR 发起地址落在 C2C 96T 空间的访问 | 返回 Decode Error (TRS REQ_PCIE_TRS_041) | P0 |
| **MEMBAR-007** | 跨 Die 访问 (Die0+Die1) | Host 通过同一个 BAR 配置分别访问 Die0 和 Die1 的地址空间 | 两 Die 均可正常访问 (TRS REQ_PCIE_TRS_044) | P0 |
| **MEMBAR-008** | 地址重映射 (ReMap) 功能验证 | 配置 ReMap 将离散的 SoC 物理地址映射为连续的 BAR 空间地址，Host 通过 BAR 访问 | 离散物理地址被正确映射，连续 BAR 地址可访问 | P1 |

### 3.5 BAR Match Mode vs Address Match Mode

**Spec §4.14.2:**
- MEM I/O TLP: MATCH_MODE=0 → Address Match; MATCH_MODE=1 → BAR Match
- ReMap 工作时 iATU 使用 BAR Match Mode，输出地址 offset=0

| 测试项 | 测试描述 | 激励 | 期望结果 | 优先级 |
|--------|---------|------|---------|--------|
| **BMATCH-001** | BAR Match Mode 基本功能 | Inbound iATU 配置 BAR Match Mode，BAR_NUM 匹配到对应 BAR | TLP 地址落在已使能 BAR 范围内时命中 region，转换正确 | P0 |
| **BMATCH-002** | Address Match Mode 基本功能 | Inbound iATU 配置 Address Match Mode，设定起始/结束地址 | TLP 地址在 region 范围内时命中，未命中时 bypass | P0 |
| **BMATCH-003** | ReMap + BAR Match 协同 | 使能 ReMap (256 windows)，iATU 设为 BAR Match Mode + offset=0 | ReMap 完成地址重映射后再经 iATU 转换，最终 AXI 地址正确 | P1 |
| **BMATCH-004** | 多 Region 命中优先级 | 配置 2 个 Address Match region 范围有重叠，发送重叠地址的 TLP | 编号最小的使能 region 命中（Spec §4.14.1） | P1 |

---

## 4. 内部寄存器空间 Test Plan

### 4.1 访问路径

内部寄存器通过 3 条路径可访问：

| 路径 | 发起方 | 访问范围 | 约束 |
|------|--------|---------|------|
| **AXI-lite** | SoC 本地 CPU | 全部 9 个子空间 (NoC 转换后) | 主配置路径 |
| **BAR0 (MRd/MWr)** | Remote Host | DMA, iATU, MSI-X Table, MSI-X PBA | 需 iATU BAR Match 转换 |
| **CFG TLP** | Remote Host | CDM (Configuration Space) | BDF + 配置空间偏移 |

### 4.2 子系统顶层寄存器 (Top CFG, offset 0x00000000)

| 测试项 | 测试描述 | 激励 | 期望结果 | 优先级 |
|--------|---------|------|---------|--------|
| **TOPREG-001** | app_ltssm_enable 寄存器 | AXI-lite 读写 pcie_ss_ctl_reg.sii_core_ctrl.app_ltssm_enable | 写 1 使能 LTSSM，写 0 禁止；读回一致 | P0 |
| **TOPREG-002** | app_hold_phy_rst 寄存器 | AXI-lite 读写 | 控制 PHY reset 释放时序 | P0 |
| **TOPREG-003** | flush_time_ctrl 寄存器 | AXI-lite 配置 flush timer timeout 值 | timer 按配置值计时；写 timeout clear 寄存器可提前退出 | P0 |
| **TOPREG-004** | rdlh_link_up 状态寄存器 | Link training 完成后读 | 读回 1；link down 时读回 0 | P0 |
| **TOPREG-005** | 中断状态寄存器 | 触发各类中断后读取 | 对应中断状态位置 1；写 irq_clear 清 0 | P0 |
| **TOPREG-006** | isolation enable 寄存器 | 读写 isolation 控制位 | 默认 1；写 0 后 isolation disable，子系统正常工作 (PCIE_MAS_REQ_ISO_2002) | P0 |
| **TOPREG-007** | HotResetBlock 模式配置 | 配置 bypass/firewall/hot_reset 模式 | 模式切换正确，firewall 模式在 isolation 使能时强制进入 (PCIE_MAS_REQ_HotResetBlock_2101) | P1 |
| **TOPREG-008** | 所有顶层寄存器复位默认值 | POR 后遍历读取所有 Top CFG 寄存器 | 与 Spec 定义的默认值一致 | P1 |

### 4.3 DBI 空间寄存器 (offset 0x01000000, 16MB)

**Spec Table 4-3 DBI 地址译码位域:**

| 访问类型 | bit[31:30] | bit[29] | bit[28:23] | bit[22] | bit[21] | bit[20] | bit[19:16] | bit[15:12] | 大小 |
|----------|-----------|---------|------------|---------|---------|---------|------------|------------|------|
| CDM | 00 | 0 | 0 | 0 | 0 | 0 | 0 | 4KB 空间 | 4KB |
| iATU | 00 | 0 | 0 | 1 | 1 | 0 | iATU 地址 | iATU 地址 | — |
| DMA | 00 | 0 | 0 | 1 | 1 | 1 | DMA 地址 | DMA 地址 | — |
| MSI-X Table | 00 | 0 | 1 | 1 | 1 | 0 | 0 | 32KB 空间 | 32KB |
| MSI-X PBA | 00 | 0 | 1 | 1 | 1 | 0 | 1 | 32KB 空间 | 32KB |

| 测试项 | 测试描述 | 激励 | 期望结果 | 优先级 |
|--------|---------|------|---------|--------|
| **DBI-001** | CDM 空间 (bit22=0,bit21=0,bit20=0) | AXI-lite 访问 DBI 空间内 bit[22:20]=000 的地址 | 命中 CDM 配置寄存器，PCIe 标准 capability 结构可读 | P0 |
| **DBI-002** | iATU 空间 (bit22=1,bit21=1,bit20=0) | AXI-lite 访问 bit[22:20]=110 的地址 | 命中 iATU region 寄存器 (16 IB + 64 OB regions)，读写正确 | P0 |
| **DBI-003** | DMA 空间 (bit22=1,bit21=1,bit20=1) | AXI-lite 访问 bit[22:20]=111 的地址 | 命中 DMA/HDMA 寄存器 (16 RD + 16 WR channels) | P0 |
| **DBI-004** | MSI-X Table 空间 (bit29=1,bit22=1,bit21=1,bit20=0) | AXI-lite 访问 MSI-X Table 地址区 | 32KB Table SRAM 正确读写 | P0 |
| **DBI-005** | MSI-X PBA 空间 (bit29=1,bit22=1,bit21=1,bit20=0,bit19=1) | AXI-lite 访问 MSI-X PBA 地址区 | 32KB PBA SRAM 正确读写 | P0 |
| **DBI-006** | DBI 译码全遍历 | 生成遍历全部 bit[29:20] 组合的地址 | 每种组合仅命中一个功能块或 reserved；无地址混叠（aliasing） | P0 |
| **DBI-007** | DBI 内 Reserved 区域 | AXI-lite 访问译码表中不存在的 bit 组合 | 不挂死，返回 Error 或 0 (PCIE_MAS_REQ_CFG_517) | P0 |
| **DBI-008** | DBI 空间未用高位地址 | 访问 DBI 空间内 offset ≥ 实际有效空间的地址（如 0x010F0000 以上） | 不挂死 | P1 |

### 4.4 多路径一致性

| 测试项 | 测试描述 | 激励 | 期望结果 | 优先级 |
|--------|---------|------|---------|--------|
| **PATH-001** | AXI-lite vs BAR0 访问 DMA 寄存器一致性 | 通过 AXI-lite 写 DMA 寄存器，通过 BAR0 读同一寄存器 | 读取值 = 写入值 | P0 |
| **PATH-002** | AXI-lite vs BAR0 访问 iATU 寄存器一致性 | 通过 AXI-lite 写 iATU region 寄存器，通过 BAR0 读 | 读取值 = 写入值 | P0 |
| **PATH-003** | AXI-lite vs BAR0 访问 MSI-X Table 一致性 | 通过 AXI-lite 写 MSI-X Table，通过 BAR0 读 | 读取值 = 写入值（TRS REQ_PCIE_TRS_047, REQ_PCIE_TRS_463） | P0 |
| **PATH-004** | AXI-lite vs CFG TLP 访问 CDM 一致性 | 通过 AXI-lite 写 CDM，通过 CFG TLP 读 CDM | 读取值 = 写入值 | P0 |
| **PATH-005** | BAR0 vs CFG TLP 访问 CDM 一致性 | 通过 BAR0 访问 CDM 映射地址，通过 CFG TLP 访问同一寄存器 | 两种路径看到同一寄存器内容（TRS REQ_PCIE_TRS_046） | P1 |
| **PATH-006** | 并发访问冲突 | AXI-lite 和 BAR0 同时对同一寄存器发起写操作 | 不出现数据损坏或总线挂死；后完成的写覆盖先完成的写 | P1 |
| **PATH-007** | ELBI 访问不挂死 | 外部无 ELBI 使用场景，通过内部发起 ELBI 访问 | 不挂死（PCIE_MAS_REQ_ELBI_518） | P1 |

### 4.5 特殊寄存器空间

| 测试项 | 测试描述 | 激励 | 期望结果 | 优先级 |
|--------|---------|------|---------|--------|
| **SPREG-001** | MSI-X Table Size 验证 | 读 MSI-X Table Size 字段 | = 0x7FF (支持 2048 entries, Spec §4.1.1 & TRS REQ_PCIE_TRS_460) | P0 |
| **SPREG-002** | MSI-X Table BIR 验证 | 读 MSI-X Table BIR | = 0 (BAR0, Spec §4.1.1) | P0 |
| **SPREG-003** | MSI-X Table/PBA Offset 验证 | 读 MSI-X Table Offset 和 PBA Offset | Table: 0x10000 (DBI 空间), PBA: 0x20000 (DBI 空间) (Spec §4.1.1) | P0 |
| **SPREG-004** | iATU Region 数量验证 | 遍历 iATU region 寄存器 | IB: 16 regions; OB: 64 regions (Spec §4.1.1, PCIE_MAS_REQ_IATU_701) | P0 |
| **SPREG-005** | iATU Region 最小 size 验证 | 配置 region size = 4KB | 接受配置，4KB 窗口正确工作 (PCIE_MAS_REQ_IATU_702) | P1 |
| **SPREG-006** | iATU Region 最大 size 验证 | 配置 region size = 128TB | 接受配置 (PCIE_MAS_REQ_IATU_702) | P1 |
| **SPREG-007** | ReMap window 数量验证 | 遍历 ReMap windows | ≤ 256 windows (Spec §4.7) | P1 |
| **SPREG-008** | PHY CR 空间访问 | 通过 AXI-lite → NoC → APB→CR 总线访问 PHY0-3 内部寄存器 | 可读写 PHY 内部寄存器（PCIE_MAS_REQ_PHY_1210） | P0 |
| **SPREG-009** | PHY SRAM ECC 中断 | 注入 PHY SRAM single/double bit error | single → phy_sram_sec_irq; double → phy_sram_ded_irq (PCIE_MAS_REQ_SRAM_ECC_1213) | P1 |
| **SPREG-010** | MSIX2DBI doorbell 寄存器 | AXI-lite 写 MSIX2DBI doorbell 触发 MSI-X | MSI-X TLP 正确生成，vector/data 与写入值一致 | P0 |

### 4.6 寄存器访问安全与隔离

| 测试项 | 测试描述 | 激励 | 期望结果 | 优先级 |
|--------|---------|------|---------|--------|
| **SEC-001** | Isolation 状态下 AXI Slave 不挂死 | 使能 PCIe partial good isolation (default=1)，MainNoC 发起 AXI Slave 访问 | 访问不挂死，返回 error 或丢弃 (PCIE_MAS_REQ_ISO_2003) | P0 |
| **SEC-002** | Isolation 状态下 AXI Master 不主动发起 | 使能 isolation，检查 AXI Master 接口 | 无主动访问发出 (PCIE_MAS_REQ_ISO_2003) | P0 |
| **SEC-003** | Isolation 状态下配置总线可访问 | 使能 isolation，通过 AXI-lite 访问配置空间 | 配置空间正常响应（CFG NoC 不在 partial good 域内） | P0 |
| **SEC-004** | ReMap 保护内部配置空间 | 通过 ReMap 配置 window 使能/禁止对内部配置空间的访问 | 本地 AON 可配置 ReMap，远端 Host 不可绕过 (Spec §4.7) | P1 |
| **SEC-005** | MSI/MSI-X 触发寄存器 Host 不可见 | Host 通过 BAR 尝试访问 SoC CPU 触发 MSI/MSI-X 的寄存器 | 不应被 Host 访问到 (TRS REQ_PCIE_TRS_466) | P1 |

---

## 5. 跨文档差异与待确认问题

| # | 问题 | TRS (r2p0) | Spec (v3.21) | 建议 | 
|---|------|-----------|-------------|------|
| 1 | BAR 数量 | 3 个 (1×64bit MEM + 1×32bit REG + 1×32bit 内部) | 4 个 (BAR0/1/2/4) | 确认 TRS r2p0 是否已更新至与 Spec 一致；DV 按 Spec 4 BAR 验证 |
| 2 | BAR 用途映射 | REG BAR → 低 4G 配置寄存器 + MSI-X Table; MEM BAR → seDRAM | BAR0 → 内部寄存器; BAR1/2/4 → SoC 地址空间 | BAR0 = REG BAR 角色, BAR2/BAR4 = MEM BAR 角色; BAR1 角色待确认 |
| 3 | Outbound iATU window 数 | 256 (REQ_PCIE_TRS_202) | 64 (Spec §4.1.1) | 确认最终实现值；DV 按 Spec 实际参数验证 |
| 4 | MSI-X Table BAR 偏移 | — | 旧: 0x10000; 新(v3.02): 0x80000 (Table), 0x88000 (PBA) | DV 按新偏移 0x80000/0x88000 验证 |
| 5 | FLR 支持 | 不支持 (r2p0 移除) | 不支持 (PCIE_MAS_REQ_FLR_1606) | 一致，DV 不覆盖 FLR |
| 6 | ATS/eATU 支持 | 不支持 (TC550 r2p0 移除) | 未提及 | 一致，DV 不覆盖 ATS/eATU |
| 7 | SMMUv3 位置 | Inbound AXI Master 路径上 (REQ_PCIE_TRS_050) | TBU 通过 DTI 外接 TCU (Spec §4.8) | DV 需验证 TBU bypass 和使能两种模式 |

---

## 6. 测试优先级汇总

| 优先级 | 数量 | 覆盖范围 |
|--------|------|---------|
| **P0** | 35 项 | 核心地址译码、BAR 基本属性、DBI 空间全遍历、3 条访问路径一致性、Reserved 不挂死、Isolation 基本行为、MSI-X Table/PBA 偏移 |
| **P1** | 20 项 | 边界条件、ReSize 边界、ReMap 协同、并发冲突、ELBI、PHY SRAM ECC、跨 Die 访问、安全隔离 |
| **P2** | 1 项 | 非法 ReSize size 拒绝 |

---

## 7. 测试环境与依赖

- **仿真环境:** PCIe EP Controller VIP + AXI-lite VIP + AXI4 Master/Slave VIP
- **参考模型:** DWC_pcie_ctl_ep_databook 寄存器模型、PHY CR 寄存器模型
- **前置条件:** Link training 完成 (LTSSM = L0), BAR 已被 RC 枚举并分配基地址
- **激励生成:** 
  - AXI-lite: 直接寄存器读写 sequence
  - BAR 路径: 构造 MRd/MWr TLP，地址 = BAR_base + register_offset
  - CFG TLP: 构造 CfgRd/CfgWr TLP，BDF + config_offset
- **检查机制:**
  - 寄存器读写值比对（scoreboard）
  - 总线协议检查（AXI 无 hang、无 protocol violation）
  - 地址译码唯一性检查（无 aliasing）
  - 中断上报正确性检查
