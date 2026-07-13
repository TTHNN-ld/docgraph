"""Run 6-case Baseline vs DocGraph comparison using Claude Code.

Cases: 3(port table), 7(MSI testplan), 8B(register RAL), 9(UVM seq), 11(clock/reset), 17(KG audit)
Plus already-run: Case 1, Case 8A
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]

# ── Case definitions ──

CASES = [
    {
        "id": "case3",
        "name": "RTL 端口表与接口约束",
        "baseline_prompt": """你是 RTL 设计工程师。请基于 PCIe Subsystem Spec 生成顶层端口表。

文档文本已预提取至: benchmark_runs/baseline_docgraph_compare/pcie_spec_text.txt

要求:
1. 列出接口/信号组、方向、位宽、clock/reset 归属、用途
2. 至少覆盖 AXI Master、AXI Slave、AXI Lite/CFG、TX/RX lane、PHY 参考时钟、复位
3. 输出结构化表格，带页码证据
4. 最多读 15 页""",
        "docgraph_prompt": """你是 RTL 设计工程师。请基于 PCIe Subsystem Spec 生成顶层端口表。

用 docgraph MCP: search_chunks → fetch 读取接口表原文 → 查 entities 加速

要求:
1. 列出接口/信号组、方向、位宽、clock/reset 归属、用途
2. 输出结构化表格，带页码+block/chunk 证据""",
    },
    {
        "id": "case7",
        "name": "MSI/MSI-X 中断 test plan",
        "baseline_prompt": """你是 PCIe 中断验证 owner。请综合 Spec 和 TRS 生成 MSI/MSI-X/Legacy Interrupt test plan。

文档文本:
- benchmark_runs/baseline_docgraph_compare/pcie_spec_text.txt (Spec)
- benchmark_runs/baseline_docgraph_compare/PCIe Subsystem TRS_r2p0_text.txt (TRS)

要求:
1. 覆盖 Function/Error interrupt、MSI、MSI-X、Legacy Interrupt
2. 包含 REQ_PCIE_TRS 需求编号、寄存器/配置对象、test scenario
3. 说明 MSI 最多 32 vectors，MSI-X 最多 2048 vectors
4. 带文档名+页码证据
5. 最多读 25 页""",
        "docgraph_prompt": """你是 PCIe 中断验证 owner。请综合 Spec 和 TRS 生成 MSI/MSI-X/Legacy Interrupt test plan。

用 docgraph MCP: search_chunks 定位中断相关章节 → fetch 读取原文 → search 查 interrupt/requirement 实体

要求:
1. 覆盖 Function/Error interrupt、MSI、MSI-X、Legacy Interrupt
2. 包含 REQ_PCIE_TRS 需求编号、寄存器、test scenario
3. 输出带页码+chunk 证据
4. L2 entities 有 needs_source_check 标注时注意验证""",
    },
    {
        "id": "case8b",
        "name": "PCIe 寄存器 RAL 输入",
        "baseline_prompt": """你是 UVM RAL owner。请基于 Spec 找到真正包含寄存器字段定义(offset/access/reset/bit-range)的表格，生成 RAL 建模输入。

文档文本: benchmark_runs/baseline_docgraph_compare/pcie_spec_text.txt

要求:
1. 找到含 access/reset 字段的真正 register table（不是 interrupt source 信号表）
2. 列出 register name、field 名、bit range、access、reset value
3. 区分「真正的寄存器表」和「中断源信号表」
4. 带页码证据
5. 最多读 15 页""",
        "docgraph_prompt": """你是 UVM RAL owner。请基于 Spec 找到含 register field 定义的表，生成 RAL 建模输入。

用 docgraph MCP: search("per_vector_misc") 或 search("freeze_reg") 找 register 实体 → fetch source chunk 读原表 → 检查表头是否有 access/reset 列

要求:
1. 区分真正 register 表(有 access/reset)和信号表(无)
2. 列出 register/field/bit range/access/reset
3. 带页码+block/chunk 证据
4. 注意 L2 entities 的 source_quality: deterministic → 可信""",
    },
    {
        "id": "case9",
        "name": "MSI-X doorbell UVM sequence",
        "baseline_prompt": """你是 UVM test writer。请基于 Spec 的 INT_NUM/per_vector_misc 寄存器字段设计 UVM sequence 草案。

文档文本: benchmark_runs/baseline_docgraph_compare/pcie_spec_text.txt

要求:
1. 列出 per_vector_misc 的所有 field: mask_bit, priority, pf, vf, vfactive, tc
2. 每个 field 的 bit range、access、reset value
3. 说明 tc 字段控制什么(Traffic Class)
4. 设计 sequence 步骤: 配置→doorbell 触发→预期 AXI/MSI-X 行为
5. 带页码证据
6. 最多读 10 页""",
        "docgraph_prompt": """你是 UVM test writer。基于 per_vector_misc 寄存器字段设计 UVM sequence。

用 docgraph MCP: search("per_vector_misc") 找 register → fetch source chunk → 从 entities 拿 bitfield 的精确 bit_high/bit_low/access/reset_value

要求:
1. 列出所有 field 及精确 bit/access/reset 值
2. 设计 sequence 步骤
3. L2 bitfield entities 的 needs_source_check=false 表示可信(从原表确定性抽取)""",
    },
    {
        "id": "case11",
        "name": "Clock/Reset 验证计划",
        "baseline_prompt": """你是 clock/reset 验证 owner。请基于 Spec 生成 clock/reset verification plan。

文档文本: benchmark_runs/baseline_docgraph_compare/pcie_spec_text.txt

要求:
1. clock source、PLL/CRG/GFM/DIV/MUX 作用
2. reset source、冷/暖/热复位场景
3. 关键 clock 信号: clk_ref_in, axi_clk, core_clk, cfg_clk, pipe_rx/tx
4. 关键 reset: cfg_rst_n, mstr_rst_n, slv_rst_n, PERST#
5. 输出 assertion/checker/coverage item
6. 带页码/图号证据
7. 最多读 10 页""",
        "docgraph_prompt": """你是 clock/reset 验证 owner。基于 Spec 生成 clock/reset verification plan。

用 docgraph MCP: search_chunks("clock CRG PLL") 定位时钟章节 → fetch 读取 → search clock/信号实体

注意: 当前 KG 中 clock 实体覆盖有限(~15%)，可能需要多靠 L1 search_chunks + L0 fetch 读原文。
L2 entities 的 needs_source_check=true 表示来自 VLM 图抽取，需验证。

要求:
1. clock/reset source 和作用
2. 冷/暖/热复位场景
3. 输出 assertion/checker/coverage
4. 带页码证据""",
    },
    {
        "id": "case17",
        "name": "KG 缺失发现",
        "baseline_prompt": """你是芯片 spec 审阅人。请检查 PCIe Subsystem Spec 中 clock 和 register 实体的完整性。

文档文本: benchmark_runs/baseline_docgraph_compare/pcie_spec_text.txt

要求:
1. 从原文找出所有 clock 信号(搜索 "clk", "clock", "aclk", "rclk")
2. 从原文找出所有定义了的寄存器(搜索 "Reg name", "Field", "Msb", "Lsb", "access", "reset")
3. 列表对比: 原文有什么 vs 你预期 KG 中应该有什么
4. 标注哪些信息可能缺失
5. 最多读 20 页""",
        "docgraph_prompt": """你是芯片 spec 审阅人。审计 DocGraph KG 中 clock 和 register 实体的完整性。

用 docgraph MCP:
1. docgraph_status 看当前 KG 的各实体类型数量
2. docgraph_search 查 clock/register 实体
3. docgraph_search_chunks 在 L1 搜原文 "clock" "clk" "register"
4. docgraph_fetch 读原文对比

要求:
1. 对比 L2 实体 vs L0 原文，找出 KG 中缺失或属性不全的实体
2. 每个发现给出原文页码/表格号证据
3. 区分"KG 没抽到"vs"原文就没有"
4. L2 缺失时能否从 L1/L0 原文获取同样信息? (验证架构契约)""",
    },
]


def run_claude(prompt: str, mode: str, case_id: str, mcp_config: str | None = None) -> dict:
    """Run Claude Code in headless mode and return result as dict."""
    prompt_file = OUT / f"{case_id}_{mode}_prompt.txt"
    out_file = OUT / f"{case_id}_{mode}.json"

    prompt_file.write_text(prompt)

    cmd = [
        "claude", "--print", "--output-format", "json",
        "--max-turns", "35",
        "--dangerously-skip-permissions",
    ]

    if mcp_config:
        cmd.extend(["--mcp-config", mcp_config])
    else:
        # Empty MCP config (no MCP servers)
        cmd.extend(["--mcp-config", "/dev/stdin"])

    cmd.extend(["-p", prompt])

    print(f"  [{mode}] Running {case_id}...", flush=True)

    t0 = time.time()
    try:
        if mcp_config:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600, cwd=str(ROOT),
            )
        else:
            # Baseline: pass empty MCP config via stdin
            result = subprocess.run(
                cmd, input='{"mcpServers":{}}', capture_output=True, text=True,
                timeout=600, cwd=str(ROOT),
            )

        wall_time = round(time.time() - t0, 1)
        out_file.write_text(result.stdout)

        if result.returncode != 0:
            return {"error": f"exit code {result.returncode}", "stderr": result.stderr[:500]}

        data = json.loads(result.stdout)
        data["_wall_time_s"] = wall_time
        return data

    except subprocess.TimeoutExpired:
        return {"error": "timeout after 600s"}
    except json.JSONDecodeError:
        return {"error": "JSON parse failed", "stdout": result.stdout[:500] if 'result' in dir() else ""}


def run_all():
    mcp_config = str(OUT / "docgraph_mcp.json")

    results = {}

    for case in CASES:
        cid = case["id"]
        name = case["name"]
        print(f"\n{'='*60}")
        print(f"{cid}: {name}")
        print(f"{'='*60}")

        # Baseline
        baseline = run_claude(case["baseline_prompt"], "baseline", cid)
        results[f"{cid}_baseline"] = baseline

        # DocGraph
        docgraph = run_claude(case["docgraph_prompt"], "docgraph", cid, mcp_config)
        results[f"{cid}_docgraph"] = docgraph

        # Quick comparison
        if "error" not in baseline and "error" not in docgraph:
            print(f"  Baseline: {baseline.get('num_turns')}t, {baseline['_wall_time_s']}s, ${baseline.get('total_cost_usd', 0):.3f}")
            print(f"  DocGraph: {docgraph.get('num_turns')}t, {docgraph['_wall_time_s']}s, ${docgraph.get('total_cost_usd', 0):.3f}")

    # Save all results
    all_out = OUT / "all_results.json"
    with open(all_out, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nAll results saved to {all_out}")

    return results


if __name__ == "__main__":
    run_all()
