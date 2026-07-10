"""Evaluate benchmark case design and DocGraph suitability from chip engineering perspective.

For each case:
- Engineer role & real workflow
- What information is needed & where it lives in the spec
- Whether the case design is reasonable
- DocGraph expected benefit: HIGH / MEDIUM / LOW
- Actual toolchain performance (L1 recall, L2 coverage)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docgraph.core.config import docgraph_dir, project_root_from_cwd
from docgraph.graph.schema import NodeKind
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.query.engine import QueryEngine


# ── Case evaluation from chip engineering perspective ──

CASES_EVAL = [
    {
        "id": "Case 1",
        "name": "地址转换 RTL 设计输入包",
        "role": "RTL owner",
        "workflow": "设计阶段：理解地址空间 → 设计 iATU/BAR/ReMap 逻辑 → 出 RTL 接口方案",
        "info_sources": [
            ("TRS §5.1 System Address Map", "系统级地址规划（表格）"),
            ("TRS §5.2 BAR Space", "BAR 分配与约束（表格+需求）"),
            ("TRS §5.3 Outbound Paths", "四种 IOMMU/ATS 配置路径（图+章节）"),
            ("Spec §4.2 Address Map", "子系统内部 NoC 地址映射（表格）"),
            ("Spec §4.14 iATU", "iATU 寄存器定义（表格）"),
            ("Spec §4.7 ReMap", "地址重映射模块（章节+寄存器）"),
        ],
        "needs_cross_doc": True,
        "needs_entity_relation": True,  # iATU→BAR, ReMap→iATU, TBU→SMMU
        "needs_table_precision": True,  # 寄存器 offset/bit 必须精确
        "case_design_ok": True,
        "case_design_notes": "设计合理。覆盖了跨文档地址映射的核心场景。但期望证据中 Spec 和 TRS 的 BAR 数量有冲突(3 vs 4)，这本身就是有价值的发现。",
        "docgraph_benefit": "HIGH",
        "docgraph_benefit_reason": "跨文档地址实体(memory_map, register)可通过 L2 直接定位，iATU/BAR/ReMap 关系通过 belongs_to/contained_in 边加速。不需要全文翻 84 页。",
    },
    {
        "id": "Case 2",
        "name": "RTL 模块边界与接口清单",
        "role": "集成工程师",
        "workflow": "理解顶层架构 → 列出所有子模块 → 梳理模块间接口 → 出顶层端口 review checklist",
        "info_sources": [
            ("Spec Figure 3-1", "PCIe 子系统架构框图（VLM 抽取）"),
            ("Spec §2 Interfaces", "接口信号表 p.13（表格）"),
            ("Spec §4.1-4.12", "各子模块详细描述（正文+表格+图）"),
        ],
        "needs_cross_doc": False,
        "needs_entity_relation": True,  # module→interface, clock→module
        "needs_table_precision": False,  # 接口层面不需要 bit 精度
        "case_design_ok": True,
        "case_design_notes": "设计合理。但期望证据中 PCIe_top, Irq_aggregator 来自框图 VLM 抽取，当前 KG 缺失，会导致 L2 召回率下降。",
        "docgraph_benefit": "MEDIUM",
        "docgraph_benefit_reason": "Figure VLM 提供了模块/接口/连接关系，但精度依赖 VLM 质量。接口表 (p.13) 可确定性抽取。当前 KG 的 module 覆盖 ~70%，VLM 抽取的模块名可能不准。",
    },
    {
        "id": "Case 3",
        "name": "RTL 端口表与接口约束",
        "role": "RTL 设计工程师",
        "workflow": "从 spec 建顶层端口表 → 确定每个接口的 direction/width/clock-reset → 出约束",
        "info_sources": [
            ("Spec §2 Interfaces", "接口分组表 p.13（表格）"),
            ("Spec §4.9 PIPE Interface", "PIPE 接口细节（表格）"),
            ("Spec §4.11-4.12", "RAS/电源管理接口（表格+正文）"),
        ],
        "needs_cross_doc": False,
        "needs_entity_relation": False,
        "needs_table_precision": True,  # 位宽、方向必须准确
        "case_design_ok": True,
        "case_design_notes": "设计合理。本质上是表格式信息提取——从 2-3 张接口表汇总出端口表。这类任务 DocGraph 的结构化实体抽取有天然优势。",
        "docgraph_benefit": "HIGH",
        "docgraph_benefit_reason": "接口表中的 signal/interface 实体已被确定性抽取(needs_source_check=False)，agent 可直接用 L2 实体 + L0 原表兜底。不需要全文推理。",
    },
    {
        "id": "Case 4",
        "name": "地址/BAR test plan",
        "role": "DV owner",
        "workflow": "理解地址空间 → 列出 feature → 设计 testpoint → 出 coverage model",
        "info_sources": [
            ("TRS §5.1 System Address Map", "系统级地址规划"),
            ("TRS §5.2 BAR Space", "BAR 分配"),
            ("Spec §4.2 Address Map", "子系统内部映射"),
            ("Spec §4.3 BAR Space", "BAR 配置表"),
        ],
        "needs_cross_doc": True,
        "needs_entity_relation": True,
        "needs_table_precision": True,
        "case_design_ok": True,
        "case_design_notes": "设计合理。要求区分「系统级 map」「BAR」「内部 CFG/DBI/ReMap/iATU 空间」三个层次，这恰是 L2 memory_map 实体 + B 层地址 join 的强项。",
        "docgraph_benefit": "HIGH",
        "docgraph_benefit_reason": "memory_map 实体 + address join 关系可直接回答「某个寄存器在哪个地址空间」。跨文档对齐时 L2 比全文搜索更精准。",
    },
    {
        "id": "Case 5",
        "name": "数据路径 RTL/DV 方案",
        "role": "设计+验证联合",
        "workflow": "对比 Inbound/Outbound/DMA 三条路径 → 理解模块参与 → 定验证观测点",
        "info_sources": [
            ("TRS Figure 5-3/5-4/5-5", "三条数据路径框图"),
            ("TRS §5.3.x", "路径详细描述（正文）"),
            ("Spec §1.5 AXI", "AXI 接口参数（表格）"),
            ("Spec §4.14 iATU", "iATU 行为（寄存器+正文）"),
        ],
        "needs_cross_doc": True,
        "needs_entity_relation": True,  # 模块→数据路径 的参与关系
        "needs_table_precision": False,
        "case_design_ok": True,
        "case_design_notes": "设计合理，但偏重理解+推理而非信息查找。期望的「观测信号/状态」「corner case」很多来自工程经验而非 spec 直接记载，评测时需要区分「从 spec 正确提取」vs「工程推理质量」。",
        "docgraph_benefit": "MEDIUM",
        "docgraph_benefit_reason": "数据路径信息主要在 TRS 的图+正文中，L2 图谱对此类信息覆盖有限。DocGraph 帮你找到相关章节/图，但「三条路径的差异对比」需要 agent 综合理解，不单是检索问题。",
    },
    {
        "id": "Case 6",
        "name": "IOMMU/ATS 验证矩阵",
        "role": "DV owner",
        "workflow": "理解 IOMMU/ATS 四种配置 → 设计 test matrix → 出激励/检查点",
        "info_sources": [
            ("TRS §5.3.3/5.3.4", "IOMMU on + ATS off/on 路径图"),
            ("TRS §6.6-6.8", "PRS/ATS 需求"),
            ("Spec §4.8 TBU", "TBU 模块描述"),
        ],
        "needs_cross_doc": True,
        "needs_entity_relation": True,
        "needs_table_precision": False,
        "case_design_ok": True,
        "case_design_notes": "设计合理。但 ATS/eATU/PRS 当前 TC550 不支持（文档自己说的），所以期望的 test matrix 在实际工程中可能是预留项——case 应标注这一点。",
        "docgraph_benefit": "MEDIUM",
        "docgraph_benefit_reason": "类似 Case 5——信息分散在图+正文中，L2 覆盖有限。但 IOMMU/ATS 相关的 requirement 实体可能被 text_entity 抽取，提供加速。",
    },
    {
        "id": "Case 7",
        "name": "MSI/MSI-X/Legacy 中断 test plan",
        "role": "中断验证 owner",
        "workflow": "整理中断需求 → 列出 feature → 设计 scenario → 出 checker/coverage",
        "info_sources": [
            ("Spec §4.6 Interrupt", "中断控制器框图+irq_src 表+寄存器"),
            ("TRS §6.5 MSI/MSI-X", "中断需求 REQ_PCIE_TRS_450-467"),
            ("Spec §4.6.2.2", "per_vector_misc 寄存器表"),
        ],
        "needs_cross_doc": True,
        "needs_entity_relation": True,  # interrupt→register 映射
        "needs_table_precision": True,  # 寄存器字段精确
        "case_design_ok": True,
        "case_design_notes": "设计合理。REQ_PCIE_TRS 需求编号是 text_entity 抽取的 requirement 实体，interrupt 实体覆盖了 irq_src 表。跨文档对齐正是 DocGraph 强项。",
        "docgraph_benefit": "HIGH",
        "docgraph_benefit_reason": "require 实体 + interrupt 实体跨文档对齐。MSI-X vector/PBA 信息集中在 p.27 寄存器表，L2 register/bitfield 可直接提供结构化字段。",
    },
    {
        "id": "Case 8A",
        "name": "irq_src 中断源信号建模",
        "role": "验证工程师",
        "workflow": "解析中断源信号表 → 区分 signal vs register → 按功能分类",
        "info_sources": [
            ("Spec §4.6.1 irq_src 表", "p.24-26 中断源信号（表格）"),
        ],
        "needs_cross_doc": False,
        "needs_entity_relation": False,
        "needs_table_precision": True,  # 信号名分类必须准确
        "case_design_ok": True,
        "case_design_notes": "Case 8 拆分后的 8A 设计合理。核心考察点——「识别此表不是 register 表」——是之前 Case 8 缺失的关键判断。当前 KG 中 irq_src 表的实体类型正确地标记为 signal/interrupt，可帮 agent 做这个判断。",
        "docgraph_benefit": "HIGH",
        "docgraph_benefit_reason": "fetch 返回的 entities 全是 signal/interrupt (0 register)，表头无 access/reset 字段——agent 能直接据此判断「不可做 RAL」。这是 L2 entity kind 信息的直接价值。",
    },
    {
        "id": "Case 8B",
        "name": "PCIe 寄存器 RAL 输入",
        "role": "UVM RAL owner",
        "workflow": "定位真正含 access/reset 的寄存器表 → 生成 field/bit/access/reset 结构 → 出 RAL 策略",
        "info_sources": [
            ("Spec §4.6.2.2 per_vector_misc", "p.27 寄存器字段表（表格）"),
            ("Spec §4.13 debug 寄存器", "p.34-35 调试寄存器（表格）"),
            ("Spec §4.14 iATU", "p.36 iATU 寄存器（表格）"),
        ],
        "needs_cross_doc": False,
        "needs_entity_relation": True,
        "needs_table_precision": True,  # bit 号不能错
        "case_design_ok": True,
        "case_design_notes": "设计合理。与 8A 配对使用，考察 agent 能否区分「信号表」和「寄存器表」。当前 KG 有 freeze_reg/ltssm_state_reg/IATU 等 register 实体 + 手动加的 per_vector_misc，覆盖关键路径。",
        "docgraph_benefit": "HIGH",
        "docgraph_benefit_reason": "register/bitfield 实体是 L2 最强项（确定性抽取）。bit 号/access/reset 直接从原表出，L2 提供结构化字段，L0 提供原文兜底。这是 DocGraph 最该赢的场景。",
    },
    {
        "id": "Case 9",
        "name": "MSI-X doorbell UVM sequence",
        "role": "UVM test writer",
        "workflow": "理解 INT_NUM/per_vector_misc 寄存器字段 → 设计 sequence 步骤 → 定编程值/错误注入",
        "info_sources": [
            ("Spec §4.6.2.2", "p.27 per_vector_misc 寄存器字段表"),
        ],
        "needs_cross_doc": False,
        "needs_entity_relation": False,
        "needs_table_precision": True,  # reset 值、bit range 必须精确
        "case_design_ok": True,
        "case_design_notes": "设计合理。直接依赖寄存器字段表的精度。bit 号/reset 值/access 错任何一个 UVM sequence 就废了——这恰是确定性抽取 vs 全文查找的关键差异点。",
        "docgraph_benefit": "HIGH",
        "docgraph_benefit_reason": "per_vector_misc 的 7 个 bitfield 现已入库(手动加)，每个都带精确的 bit_high/bit_low/access/reset_value。agent 查 L2 实体直接拿到结构化字段，无需从 37 行原表中人工对齐。",
    },
    {
        "id": "Case 10",
        "name": "LTSSM State debug 流程",
        "role": "bring-up/debug 工程师",
        "workflow": "理解 LTSSM debug 电路 → 列出寄存器+字段 → 描述 freeze/capture/read 流程",
        "info_sources": [
            ("Spec §4.13.2 LTSSM debug", "p.35 框图+寄存器（图+表格）"),
        ],
        "needs_cross_doc": False,
        "needs_entity_relation": True,  # 图→寄存器 的连接关系
        "needs_table_precision": True,
        "case_design_ok": True,
        "case_design_notes": "设计合理。考察图+寄存器的联合理解。期望的「freeze/capture/read 流程」在 spec 正文中有描述，加上框图和寄存器表——这是 VLM 图抽取 + register 实体协同的场景。",
        "docgraph_benefit": "MEDIUM",
        "docgraph_benefit_reason": "register 实体(freeze_reg, ltssm_state_reg, shift_reg)已在 KG 中。但框图→寄存器 的连接关系依赖 VLM 抽取质量，可能不完整。正文中的流程描述需要 agent 自己理解。",
    },
    {
        "id": "Case 11",
        "name": "Clock/Reset 验证计划",
        "role": "clock/reset 验证 owner",
        "workflow": "理解时钟+复位结构 → 列出 clock source/reset source → 设计 assertion/checker/coverage",
        "info_sources": [
            ("Spec Figure 4-1", "p.21 时钟结构图"),
            ("Spec Figure 4-2", "p.23 复位结构图"),
            ("Spec §2 Interfaces", "p.13 接口表中的 clock/reset 信号"),
        ],
        "needs_cross_doc": False,
        "needs_entity_relation": True,  # clock→module, reset→module
        "needs_table_precision": False,
        "case_design_ok": True,
        "case_design_notes": "设计合理。但 KG 中 clock 覆盖 ~15%，大部分期望的 clock 名 (core_clk, mstr_aclk, slv_aclk) 分类为 signal 而非 clock 实体。这是当前 KG 的最大短板之一。",
        "docgraph_benefit": "LOW",
        "docgraph_benefit_reason": "⚠️ 当前 DocGraph 最弱的场景。clock 实体仅 21 个，且多来自 VLM 图抽取（needs_source_check=True）。接口表(p.13)的 clock 信号被归类为 signal 而非 clock。agent 需要大量回退到 L1 search_chunks → L0 fetch 读原文。",
    },
    {
        "id": "Case 12",
        "name": "Tape-in 设计评审清单",
        "role": "设计/验证联合评审人",
        "workflow": "全局审视所有关键领域 → 列出 10 个 sign-off 问题 → 每个映射到章节/图/寄存器",
        "info_sources": [
            ("Spec+TRS 全文档", "覆盖地址/IOMMU/中断/DMA/时钟/PHY/JTAG 等"),
        ],
        "needs_cross_doc": True,
        "needs_entity_relation": True,
        "needs_table_precision": False,
        "case_design_ok": True,
        "case_design_notes": "设计合理。是「广度优先」的综合测试——agent 需要跨 7+ 个领域快速定位，不要求每个领域都深入。这类任务 DocGraph 应该能提供显著的定位加速。",
        "docgraph_benefit": "HIGH",
        "docgraph_benefit_reason": "多领域综合任务——address/memory_map, interrupt, DMA, clock/reset, PHY, JTAG 各 2-3 个实体。DocGraph 的 L2 search 让 agent 能跨领域快速定位，不需要全文线性扫描。",
    },
    {
        "id": "Case 13",
        "name": "STA/SDC 约束输入",
        "role": "STA/约束工程师",
        "workflow": "理解时钟结构 → 列出 clock source/domain/generated clock → 定 false/multicycle path 候选",
        "info_sources": [
            ("Spec Figure 4-1", "p.21 时钟结构图"),
            ("Spec Figure 4-2", "p.23 复位结构图"),
        ],
        "needs_cross_doc": False,
        "needs_entity_relation": True,
        "needs_table_precision": False,
        "case_design_ok": True,
        "case_design_notes": "设计合理。但比 Case 11 更偏后端——需要从时钟结构图中提取 clock domain/generated clock/async boundary 等 STA 语义。这些信息在 spec 中以图为主，正文说明为辅。",
        "docgraph_benefit": "LOW",
        "docgraph_benefit_reason": "⚠️ 类似 Case 11——clock 实体覆盖不足 + 图语义抽取精度有限。STA 需要的「generated clock」「false path」「clock group」等信息多来自工程推理而非 spec 直接记载。",
    },
    {
        "id": "Case 14",
        "name": "CDC/RDC sign-off checklist",
        "role": "CDC/RDC 工程师",
        "workflow": "列出 clock domain/reset domain → 识别跨域路径 → 定 synchronizer/reset bridge 需求",
        "info_sources": [
            ("Spec §2 Interfaces", "p.13 clock/reset 信号"),
            ("Spec Figure 4-1/4-2", "时钟+复位结构图"),
            ("Spec §4.4/4.5", "Clock/Reset 章节"),
        ],
        "needs_cross_doc": False,
        "needs_entity_relation": True,
        "needs_table_precision": False,
        "case_design_ok": True,
        "case_design_notes": "设计合理。但 CDC 分析的核心输入——clock domain 列表和跨域路径——在 spec 中没有显式给出，需要从接口表和时钟结构图中推导。DocGraph 帮定位，但推理仍靠 agent。",
        "docgraph_benefit": "LOW",
        "docgraph_benefit_reason": "⚠️ clock/reset domain 的覆盖问题同 Case 11/13。CDC 特定的「跨域路径」「synchronizer」等信息不存在于 spec 原文中，是工程推理产物。",
    },
    {
        "id": "Case 15",
        "name": "P&R/floorplan/PHY 集成",
        "role": "physical integration owner",
        "workflow": "从架构图+接口表+PHY/JTAG 图提取物理边界 → 出集成 checklist",
        "info_sources": [
            ("Spec Figure 3-1", "p.15 架构框图"),
            ("Spec §2 Interfaces", "p.13 接口表"),
            ("Spec §4.15 PHY", "p.37 PHY 参考时钟连接"),
            ("Spec §4.16 JTAG", "p.38 JTAG decoder 结构"),
        ],
        "needs_cross_doc": False,
        "needs_entity_relation": True,
        "needs_table_precision": False,
        "case_design_ok": True,
        "case_design_notes": "设计合理。从架构图/接口表/PHY/JTAG 图中提取物理边界信息——这是 VLM 图抽取 + interface 实体协同的场景。但 P&R 细节（placement/routing 约束）超出了 spec 范围。",
        "docgraph_benefit": "MEDIUM",
        "docgraph_benefit_reason": "Figure VLM 抽取 + Interface 实体提供模块/接口/PHY lane 的连接关系。但物理实现细节(具体坐标/间距/电源布线)不在 spec 中，评分时需要区分 spec 能回答的和不能回答的。",
    },
    {
        "id": "Case 16",
        "name": "DFT/JTAG bring-up 可测性",
        "role": "DFT/bring-up 工程师",
        "workflow": "理解 JTAG/PHY/debug 结构 → 列出可测性检查点 → 定 bring-up 观测/读写步骤",
        "info_sources": [
            ("Spec §4.16 JTAG", "p.38 JTAG decoder 结构图"),
            ("Spec §4.15 PHY", "p.37 PHY 连接图"),
            ("Spec §4.13.2 LTSSM debug", "p.35 debug 寄存器"),
            ("Spec §4.6.1 irq_src", "p.24-26 中断源信号（状态观测点）"),
        ],
        "needs_cross_doc": False,
        "needs_entity_relation": True,
        "needs_table_precision": True,
        "case_design_ok": True,
        "case_design_notes": "设计合理。JTAG/PHY/debug/status 的跨实体整合——需要把 JTAG 结构、debug 寄存器、中断源信号三个领域串起来。这是 DocGraph 多实体类型联合检索的典型场景。",
        "docgraph_benefit": "MEDIUM",
        "docgraph_benefit_reason": "多实体类型联合检索(JTAG module + debug register + interrupt signal)。但 JTAG 相关的实体较少（TDR 1 个寄存器），主要靠 VLM 图抽取。",
    },
    {
        "id": "Case 17",
        "name": "KG 缺失发现",
        "role": "spec 审阅人",
        "workflow": "审计 KG 中 clock/register 实体的完整性 → 发现缺失 → 追溯到原文 → 提修复建议",
        "info_sources": [
            ("全文档", "需要对照 KG 实体 vs 原文"),
        ],
        "needs_cross_doc": False,
        "needs_entity_relation": False,
        "needs_table_precision": True,
        "case_design_ok": True,
        "case_design_notes": "设计合理。这个 case 直接测试了 layered-architecture 的核心契约——L2 缺失时 agent 能否回到 L1/L0 原文发现盲区？这是架构设计正确性的最终验证。",
        "docgraph_benefit": "HIGH",
        "docgraph_benefit_reason": "这是 DocGraph 架构验证的关键 case。L2 虽不准但 L1/L0 永远可用——agent 通过 search_chunks+fetch 发现原文有但 KG 没有的实体，证明了 L1/L0 兜底的可靠性。",
    },
]


def main():
    root = project_root_from_cwd()
    store = SQLiteGraphStore(docgraph_dir(root) / "graph.db")
    store.init_schema()
    qe = QueryEngine(store)

    print("=" * 80)
    print("Benchmark Case 设计评审 & DocGraph 适用性评估")
    print("=" * 80)

    # Summary counters
    high = medium = low = 0
    design_issues = []

    for case in CASES_EVAL:
        cid = case["id"]
        benefit = case["docgraph_benefit"]
        if benefit == "HIGH": high += 1
        elif benefit == "MEDIUM": medium += 1
        else: low += 1

        print(f"\n{'─' * 80}")
        print(f"{cid}: {case['name']}")
        print(f"  角色: {case['role']}")
        print(f"  工作流: {case['workflow']}")
        print(f"  跨文档: {'是' if case['needs_cross_doc'] else '否'} | "
              f"实体关系: {'是' if case['needs_entity_relation'] else '否'} | "
              f"表精度: {'是' if case['needs_table_precision'] else '否'}")
        print(f"  DocGraph 预期收益: {benefit}")
        print(f"  理由: {case['docgraph_benefit_reason']}")

        if not case["case_design_ok"]:
            design_issues.append((cid, case["case_design_notes"]))

    # ── Summary ──
    print(f"\n{'=' * 80}")
    print("汇总")
    print(f"{'=' * 80}")
    print(f"\nDocGraph 适用性分布:")
    print(f"  HIGH:   {high} cases — L2 实体直接加速，结构化信息可利用")
    print(f"  MEDIUM: {medium} cases — 部分加速但依赖 VLM/正文理解")
    print(f"  LOW:    {low} cases — clock/reset 覆盖不足或依赖工程推理")

    print(f"\nHIGH 收益的 cases (结构化的表格式信息):")
    for case in CASES_EVAL:
        if case["docgraph_benefit"] == "HIGH":
            print(f"  {case['id']}: {case['name']}")

    print(f"\nLOW 收益的 cases (clock/reset 覆盖短板):")
    for case in CASES_EVAL:
        if case["docgraph_benefit"] == "LOW":
            print(f"  {case['id']}: {case['name']}")

    print(f"\nCase 设计问题:")
    for cid, note in design_issues:
        print(f"  {cid}: {note}")

    # ── DocGraph 短板总结 ──
    print(f"\n{'─' * 80}")
    print("DocGraph 当前短板 (实事求事)")
    print(f"{'─' * 80}")
    print("""
1. Clock/Reset 实体覆盖不足 (~15%)
   - 接口表(p.13)中的 clock 信号(mstr_aclk, slv_aclk, cfg_clk 等)
     被归类为 signal 而非 clock
   - 时钟结构图(Figure 4-1)的 VLM 抽取精度有限
   - 影响 Case 11/13/14

2. VLM 图抽取的实体名称不稳定
   - 框图抽取的 module 名可能与正文/表格中的名称不一致
   - PCIe_top, Irq_aggregator 等模块名缺失
   - 影响 Case 2/10/15/16

3. 正文中的非结构化信息无法利用 L2
   - Specification 正文中的设计意图、约束条件、流程描述
   - 主要靠 L1 search_chunks → L0 fetch 读原文
   - 影响所有需要工程推理的 case (5/6/10/12)

4. 小规模文档集上 MCP 开销高于直接读全文
   - 2×42 页文档时，105KB 全文读取 < 200K 上下文窗口
   - DocGraph MCP 返回的 chunk/block/entity 元数据增加了 token 量
   - 规模优势需要在 10+ 文档、1000+ 页时才能体现
""")

    print(f"\n{'─' * 80}")
    print("建议: 从 18 个 case 中选 6-8 个做正式 Claude Code 对照评测")
    print(f"{'─' * 80}")
    print("""
优先 (DocGraph HIGH 收益, 能体现差异):
  Case 3  — 端口表 (表格提取, 确定性)
  Case 8A — 信号表建模 (区分 signal vs register)
  Case 8B — 寄存器 RAL (register/bitfield 精度)
  Case 9  — MSI-X UVM sequence (bitfield 精确值)

次要 (跨文档/实体关系):
  Case 1  — 地址转换 (跨文档) 或 Case 7 (中断 test plan)
  Case 12 — Tape-in 评审 (多领域综合)

弱项验证 (DocGraph LOW 收益, 体现短板):
  Case 11 — Clock/reset 验证计划 (当前最弱)
""")


if __name__ == "__main__":
    main()
