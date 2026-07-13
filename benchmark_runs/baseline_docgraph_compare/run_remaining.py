"""Run remaining 12 benchmark cases (Baseline + DocGraph)."""
from __future__ import annotations

import json, subprocess, sys, time
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
MCP = str(OUT / "docgraph_mcp.json")
SPEC_TXT = "benchmark_runs/baseline_docgraph_compare/pcie_spec_text.txt"
TRS_TXT = "benchmark_runs/baseline_docgraph_compare/PCIe Subsystem TRS_r2p0_text.txt"

CASES = [
    ("case2", "RTL 模块边界与接口清单",
     f"你是 PCIe subsystem 集成工程师。基于 Spec 生成 RTL module boundary/interface checklist。列出主要模块、接口、clock/reset、PHY lane。文档: {SPEC_TXT}。带页码证据。最多读 15 页。",
     "你是 PCIe subsystem 集成工程师。基于 Spec 生成 RTL module boundary/interface checklist。用 docgraph MCP: search_chunks 定位接口表+架构图 → fetch 读取 → search 查 module/interface/clock 实体。带 block/chunk 证据。"),

    ("case3", "RTL 端口表与接口约束",
     f"你是 RTL 设计工程师。从 Spec 建顶层端口表。列出接口/信号组、方向、位宽、clock/reset 归属。至少覆盖 AXI Master/Slave/Lite、TX/RX lane、PHY ref clock、复位。文档: {SPEC_TXT}。最多读 12 页。",
     "你是 RTL 设计工程师。从 Spec 建顶层端口表。用 docgraph MCP: search_chunks 定位接口表 → fetch → search signal/interface 实体。列出方向/位宽/clock归属。带 block/chunk 证据。"),

    ("case4", "地址/BAR test plan",
     f"你是 DV owner。为 PCIe 地址空间、BAR 和内部寄存器空间制定 test plan。综合两份文档。TRS 偏系统级地址规划，Spec 偏子系统内部映射。文档: {SPEC_TXT} 和 {TRS_TXT}。最多读 25 页。",
     "你是 DV owner。为 PCIe 地址空间/BAR/寄存器制定 test plan。用 docgraph MCP: search_chunks 搜 System Address Map/BAR → fetch → search memory_map/register 实体。跨两份文档。"),

    ("case5", "数据路径 RTL/DV 方案",
     f"你是设计验证联合评审人。比较 TRS 中 Inbound/Outbound/DMA 三条数据路径。列出每条路径的参与模块、数据方向、地址转换点、关键图号/页码。文档: {SPEC_TXT} 和 {TRS_TXT}。最多读 20 页。",
     "你是设计验证联合评审人。比较 Inbound/Outbound/DMA 数据路径。用 docgraph MCP: search_chunks 搜 Inbound/Outbound/DMA → fetch → search module/interface 实体。带 block/chunk 证据。"),

    ("case6", "IOMMU/ATS 验证矩阵",
     f"你是 DV owner。基于 TRS 输出 IOMMU/ATS test matrix: IOMMU on+ATS off, IOMMU on+ATS on, ATC miss/hit, translation failure。文档: {SPEC_TXT} 和 {TRS_TXT}。最多读 15 页。",
     "你是 DV owner。基于 TRS 输出 IOMMU/ATS test matrix。用 docgraph MCP: search_chunks 搜 IOMMU/ATS/ATC → fetch → search requirement 实体。带 block/chunk 证据。"),

    ("case7", "MSI/MSI-X 中断 test plan",
     f"你是中断验证 owner。综合 Spec+TRS 生成 MSI/MSI-X/Legacy Interrupt test plan。含 REQ_PCIE_TRS 需求编号、寄存器、test scenario。MSI 最多 32 vectors, MSI-X 最多 2048 vectors。文档: {SPEC_TXT} 和 {TRS_TXT}。最多读 25 页。",
     "你是中断验证 owner。生成 MSI/MSI-X test plan。用 docgraph MCP: search_chunks 搜 MSI/interrupt → fetch → search interrupt/requirement 实体。跨两份文档。带 block/chunk 证据。"),

    ("case10", "LTSSM State debug 流程",
     f"你是 bring-up/debug 工程师。基于 Spec 生成 LTSSM State debug 调试流程: 寄存器/字段、freeze/capture/read 流程、assertion/coverage。文档: {SPEC_TXT}。最多读 8 页。",
     "你是 bring-up/debug 工程师。生成 LTSSM debug 流程。用 docgraph MCP: search freeze_reg/ltssm_state_reg → fetch → search register entity。带 block/chunk 证据。"),

    ("case12", "Tape-in 设计评审清单",
     f"你是 PCIe 子系统 tape-in 前设计验证联合评审人。基于两份 spec 列出 10 个 sign-off 前必须确认的问题。每个问题映射到章节/图/寄存器/需求编号。覆盖地址映射、IOMMU/ATS、中断、DMA、时钟复位、LTSSM、PHY/JTAG。文档: {SPEC_TXT} 和 {TRS_TXT}。最多读 30 页。",
     "你是 tape-in 评审人。列出 10 个 sign-off 问题。用 docgraph MCP: search_chunks 搜各领域关键词 → fetch → search 各类实体。覆盖地址/IOMMU/中断/DMA/时钟/PHY/JTAG。带 block/chunk 证据。"),

    ("case13", "STA/SDC 约束输入",
     f"你是 STA/约束工程师。基于 Spec 生成 SDC/STA 约束输入 checklist: clock source/domain/generated clock、PLL/CRG/GFM/DIV/MUX、reset source、false/multicycle path 候选。文档: {SPEC_TXT}。最多读 10 页。",
     "你是 STA 工程师。生成 SDC 约束 checklist。用 docgraph MCP: search_chunks 搜 clock/CRG/PLL → fetch → search clock 实体。注意 clock L2 覆盖有限，多靠 L1/L0 原文。带 chunk 证据。"),

    ("case14", "CDC/RDC sign-off checklist",
     f"你是 CDC/RDC sign-off 工程师。输出 PCIe 子系统 CDC/RDC 检查计划: clock/reset domain 列表、跨域路径、synchronizer/reset bridge 需求、PERST#/hot/warm/cold reset 风险。文档: {SPEC_TXT}。最多读 10 页。",
     "你是 CDC/RDC 工程师。输出 CDC/RDC checklist。用 docgraph MCP: search_chunks 搜 clock domain/reset domain/PERST → fetch。注意 clock L2 覆盖有限。带 chunk 证据。"),

    ("case15", "P&R/floorplan/PHY 集成",
     f"你是 physical integration owner。生成 P&R/floorplan 集成 checklist: PCIe core、UPCS PIPE、PHY0-3、CRG、NoC/AXI、JTAG、TX/RX lane 物理边界和风险。文档: {SPEC_TXT}。最多读 12 页。",
     "你是 physical integration owner。生成 P&R/PHY 集成 checklist。用 docgraph MCP: search_chunks 搜 PHY/PIPE/JTAG/CRG → fetch → search module/interface 实体。带 block/chunk 证据。"),

    ("case16", "DFT/JTAG bring-up 可测性",
     f"你是 DFT/bring-up 工程师。生成 DFT/JTAG/debug 可测性计划: JTAG decoder、gating/level shifter/isolation、PHY0-3 测试连接、LTSSM debug、interrupt/status 观测点。文档: {SPEC_TXT}。最多读 10 页。",
     "你是 DFT 工程师。生成可测性计划。用 docgraph MCP: search_chunks 搜 JTAG/debug/LTSSM → fetch → search register/module 实体。带 block/chunk 证据。"),
]


def run(mode, prompt, case_id, mcp_config=None):
    t0 = time.time()
    cmd = ["claude", "--print", "--output-format", "json", "--max-turns", "30",
           "--dangerously-skip-permissions"]
    if mcp_config:
        cmd.extend(["--mcp-config", mcp_config])
    else:
        cmd.extend(["--mcp-config", "/dev/stdin"])
    cmd.extend(["-p", prompt])

    try:
        if mcp_config:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        else:
            r = subprocess.run(cmd, input='{"mcpServers":{}}', capture_output=True,
                              text=True, timeout=600, cwd=str(ROOT))
        wall = round(time.time() - t0, 1)
        out_file = OUT / f"{case_id}_{mode}.json"
        out_file.write_text(r.stdout)
        if r.returncode != 0:
            return {"error": f"exit {r.returncode}", "stderr": r.stderr[:300], "_wall": wall}
        data = json.loads(r.stdout)
        data["_wall_time_s"] = wall
        return data
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "_wall": 600}
    except Exception as e:
        return {"error": str(e), "_wall": round(time.time()-t0,1)}


def main():
    results = {}
    for cid, name, b_prompt, d_prompt in CASES:
        print(f"\n{'='*50}\n{cid}: {name}\n{'='*50}")

        b = run("baseline", b_prompt, cid)
        results[f"{cid}_baseline"] = b
        if "error" not in b:
            print(f"  B: {b.get('num_turns')}t, {b['_wall_time_s']}s, ${b.get('total_cost_usd',0):.3f}")
        else:
            print(f"  B: ERROR - {b['error']}")

        d = run("docgraph", d_prompt, cid, MCP)
        results[f"{cid}_docgraph"] = d
        if "error" not in d:
            print(f"  D: {d.get('num_turns')}t, {d['_wall_time_s']}s, ${d.get('total_cost_usd',0):.3f}")
        else:
            print(f"  D: ERROR - {d['error']}")

        # Save incrementally
        (OUT / "remaining_results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str))

    print(f"\nDone! {len(results)} runs")


if __name__ == "__main__":
    main()
