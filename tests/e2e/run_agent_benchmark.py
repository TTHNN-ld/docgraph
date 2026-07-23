"""Claude Code Agent 对比 Benchmark: DocGraph vs Base (纯文本 grep)。

真实 Agent 流程: Claude Code -p 模式接收问题，自主决策工具调用链，
搜索 → 阅读 → 判断 → 回答。对比指标: 耗时、轮次、工具调用数、token、成本。

运行:
  python tests/e2e/run_agent_benchmark.py

前置:
  - claude CLI 已安装并可用
  - .docgraph/docgraph_mcp.json 已创建 (docgraph MCP 配置)
  - .docgraph/export/chunks.md 已导出 (Base 模式全文检索)
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_project_root = Path(__file__).resolve().parents[2]

# ═══════════════════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════════════════

MCP_CONFIG = str(_project_root / ".docgraph" / "docgraph_mcp.json")
CHUNKS_FILE = str(_project_root / ".docgraph" / "export" / "chunks.md")
OUTPUT_FILE = str(_project_root / ".docgraph" / "agent_benchmark_results.json")
MAX_BUDGET_PER_CASE = "1.00"
TIMEOUT_PER_CASE = 300  # 秒

# 10 个测试用例
CASES: list[dict[str, str]] = [
    {
        "id": "1", "title": "模块定位",
        "question": "FTB 模块的功能是什么？它有哪些关键设计参数？请从文档中找出具体的技术细节。",
    },
    {
        "id": "2", "title": "寄存器配置",
        "question": "sbpctl 寄存器的地址是多少？它有哪些位域？分别控制什么？",
    },
    {
        "id": "3", "title": "时序理解",
        "question": "BPU 的 s1/s2/s3 三级流水各自负责什么工作？什么情况会导致流水线冲刷？",
    },
    {
        "id": "4", "title": "接口协议",
        "question": "BPU 到 FTQ 的接口有哪些握手信号？信号的时序关系是怎样的？",
    },
    {
        "id": "5", "title": "异常处理",
        "question": "BPU 分支预测错误时的恢复流程是什么？有哪些重定向类型？",
    },
    {
        "id": "6", "title": "配置参数",
        "question": "ICache 有哪些可配置参数？各自的默认值和约束是什么？",
    },
    {
        "id": "7", "title": "性能限制",
        "question": "TAGE 预测器在什么情况下准确率会下降？有哪些已知的性能瓶颈？",
    },
    {
        "id": "8", "title": "信号追踪",
        "question": "信号 'pc' 在 BPU 中是怎么产生和传递的？经过哪些模块？",
    },
    {
        "id": "9", "title": "架构差异",
        "question": "RAS 预测器的持久化队列有什么优点？它能解决什么问题？",
    },
    {
        "id": "10", "title": "综合理解",
        "question": "一条指令在香山处理器中从取指到写回的完整流水线路径是怎样的？各阶段的关键模块和可能出现的异常有哪些？",
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# Prompt 模板
# ═══════════════════════════════════════════════════════════════════════════════

DG_SYSTEM = """你是一个芯片设计验证工程师的助手。你有 docgraph MCP 工具可以查询芯片设计文档的知识图谱。

可用的 MCP 工具包括:
- docgraph_search: 搜索实体 (寄存器、信号、模块、图等)
- docgraph_search_chunks: 搜索文档片段
- docgraph_neighbors: 查询实体的关联实体
- docgraph_fetch / docgraph_fetch_many: 获取实体或 chunk 的完整内容
- docgraph_context: 获取文档全局检索上下文
- docgraph_section: 获取章节结构
- docgraph_files: 列出文档文件
- docgraph_status: 查看知识图谱状态

规则:
- 优先用实体搜索 (docgraph_search) 找到精确的寄存器/信号/模块节点
- 对寄存器用 docgraph_neighbors 展开位域
- 对模块用 docgraph_neighbors 展开子模块和信号
- 用 docgraph_search_chunks 补充上下文细节
- 只基于工具返回的结果回答，不要编造
- 给出具体的名称、地址、数值等可操作信息
- 用中文回答，技术术语保留英文原名"""

BASE_SYSTEM = """你是一个芯片设计验证工程师的助手。文档全文导出在 .docgraph/export/chunks.md (2355 个 chunk，约 1.5MB)。

你有以下工具可用:
- Bash: 运行 grep 搜索文档
- Read: 读取文档片段
- Glob: 查找文件

规则:
- 先用 grep 在 chunks.md 中搜索相关关键词
- 再用 Read 查看匹配区域附近的详细内容
- 可能需要多次搜索不同关键词才能找到完整信息
- 只基于文档内容回答，不要编造
- 给出具体的名称、地址、数值等可操作信息
- 用中文回答，技术术语保留英文原名"""


# ═══════════════════════════════════════════════════════════════════════════════
# 运行器
# ═══════════════════════════════════════════════════════════════════════════════

def run_claude_p(
    question: str,
    system_prompt: str,
    *,
    mcp_config: str | None = None,
    add_dir: str | None = None,
    budget: str = MAX_BUDGET_PER_CASE,
    timeout: int = TIMEOUT_PER_CASE,
) -> dict[str, Any]:
    """运行 claude -p 并解析 JSON 结果。"""
    cmd = [
        "claude", "-p",
        "--bare",
        "--output-format", "json",
        "--max-budget-usd", budget,
        "--no-session-persistence",
        "--dangerously-skip-permissions",
        "--system-prompt", system_prompt,
    ]
    if mcp_config:
        cmd.extend(["--mcp-config", mcp_config])
    if add_dir:
        cmd.extend(["--add-dir", add_dir])

    cmd.append(question)

    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=timeout,
            cwd=str(_project_root),
        )
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "duration_ms": timeout * 1000}
    wall_ms = (time.time() - t0) * 1000

    if proc.returncode != 0:
        return {"error": f"exit_code={proc.returncode}", "stderr": proc.stderr[:500], "duration_ms": wall_ms}

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": "json_parse_failed", "stdout": proc.stdout[:500], "stderr": proc.stderr[:500], "duration_ms": wall_ms}

    # 提取指标
    usage = result.get("usage", {})
    iterations = usage.get("iterations", [])

    # 统计工具调用
    tool_calls = []
    for it in iterations:
        for tc in it.get("tool_calls", []):
            tool_calls.append({
                "tool": tc.get("name", tc.get("function", {}).get("name", "?")),
                "input": str(tc.get("input", tc.get("function", {}).get("arguments", "")))[:200],
            })

    return {
        "wall_ms": wall_ms,
        "duration_ms": result.get("duration_ms", 0),
        "duration_api_ms": result.get("duration_api_ms", 0),
        "num_turns": result.get("num_turns", 0),
        "stop_reason": result.get("stop_reason", "?"),
        "total_cost_usd": result.get("total_cost_usd", 0),
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
        "tool_calls": tool_calls,
        "tool_call_count": len(tool_calls),
        "result": result.get("result", ""),
        "is_error": result.get("is_error", False),
        "error_subtype": result.get("subtype", ""),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    # 检查前置条件
    if not Path(MCP_CONFIG).exists():
        print(f"[ERROR] MCP config not found: {MCP_CONFIG}")
        sys.exit(1)
    if not Path(CHUNKS_FILE).exists():
        print(f"[ERROR] Chunks file not found: {CHUNKS_FILE}")
        sys.exit(1)

    print("=" * 72)
    print("Claude Code Agent 对比 Benchmark")
    print(f"  MCP config: {MCP_CONFIG}")
    print(f"  Chunks file: {CHUNKS_FILE}")
    print(f"  Budget/case: ${MAX_BUDGET_PER_CASE}")
    print("=" * 72)

    results: list[dict] = []

    for i, case in enumerate(CASES):
        print(f"\n{'─' * 60}")
        print(f"[{i+1}/10] Case {case['id']}: {case['title']}")
        print(f"       Q: {case['question'][:80]}...")
        print(f"{'─' * 60}")

        # ── DocGraph 模式 ──
        print("  [DocGraph] Running...", end=" ", flush=True)
        dg = run_claude_p(
            case["question"],
            DG_SYSTEM,
            mcp_config=MCP_CONFIG,
        )
        dg_ok = not dg.get("error") and not dg.get("is_error")
        dg_status = "OK" if dg_ok else f"FAIL({dg.get('error') or dg.get('error_subtype')})"
        print(f"{dg_status}  turns={dg.get('num_turns',0)}  tools={dg.get('tool_call_count',0)}  "
              f"tokens_in={dg.get('input_tokens',0)}  tokens_out={dg.get('output_tokens',0)}  "
              f"cost=${dg.get('total_cost_usd',0):.4f}  wall={dg.get('wall_ms',0)/1000:.1f}s")

        # ── Base 模式 ──
        print("  [Base]    Running...", end=" ", flush=True)
        bs = run_claude_p(
            case["question"],
            BASE_SYSTEM,
            add_dir=str(_project_root / ".docgraph" / "export"),
        )
        bs_ok = not bs.get("error") and not bs.get("is_error")
        bs_status = "OK" if bs_ok else f"FAIL({bs.get('error') or bs.get('error_subtype')})"
        print(f"{bs_status}  turns={bs.get('num_turns',0)}  tools={bs.get('tool_call_count',0)}  "
              f"tokens_in={bs.get('input_tokens',0)}  tokens_out={bs.get('output_tokens',0)}  "
              f"cost=${bs.get('total_cost_usd',0):.4f}  wall={bs.get('wall_ms',0)/1000:.1f}s")

        results.append({
            "id": case["id"],
            "title": case["title"],
            "question": case["question"],
            "docgraph": dg,
            "base": bs,
        })

    # ── 汇总 ──
    print("\n" + "=" * 72)
    print("汇总")
    print("=" * 72)

    # 计算总计
    dg_total_cost = sum(r["docgraph"].get("total_cost_usd", 0) for r in results)
    bs_total_cost = sum(r["base"].get("total_cost_usd", 0) for r in results)
    dg_total_tokens = sum(r["docgraph"].get("input_tokens", 0) + r["docgraph"].get("output_tokens", 0) for r in results)
    bs_total_tokens = sum(r["base"].get("input_tokens", 0) + r["base"].get("output_tokens", 0) for r in results)
    dg_total_tools = sum(r["docgraph"].get("tool_call_count", 0) for r in results)
    bs_total_tools = sum(r["base"].get("tool_call_count", 0) for r in results)
    dg_total_turns = sum(r["docgraph"].get("num_turns", 0) for r in results)
    bs_total_turns = sum(r["base"].get("num_turns", 0) for r in results)
    dg_total_wall = sum(r["docgraph"].get("wall_ms", 0) for r in results) / 1000
    bs_total_wall = sum(r["base"].get("wall_ms", 0) for r in results) / 1000

    print(f"{'':20s} {'DocGraph':>15s} {'Base':>15s} {'差异':>15s}")
    print(f"{'总耗时 (wall)':20s} {dg_total_wall:14.1f}s {bs_total_wall:14.1f}s {'':>15s}")
    print(f"{'总成本':20s} ${dg_total_cost:14.4f} ${bs_total_cost:14.4f} {((dg_total_cost - bs_total_cost) / max(bs_total_cost, 0.0001) * 100):14.1f}%")
    print(f"{'总 Token':20s} {dg_total_tokens:15d} {bs_total_tokens:15d} {((dg_total_tokens - bs_total_tokens) / max(bs_total_tokens, 1) * 100):14.1f}%")
    print(f"{'总工具调用':20s} {dg_total_tools:15d} {bs_total_tools:15d}")
    print(f"{'总轮次':20s} {dg_total_turns:15d} {bs_total_turns:15d}")

    # ── 逐 case 表格 ──
    print(f"\n{'Case':6s} {'DG Turns':>9s} {'Base Turns':>10s} {'DG Tools':>9s} {'Base Tools':>11s} {'DG TokIn':>9s} {'Base TokIn':>11s} {'DG Cost':>8s} {'Base Cost':>10s}")
    for r in results:
        dg = r["docgraph"]
        bs = r["base"]
        print(f"#{r['id']:4s}  {dg.get('num_turns',0):9d} {bs.get('num_turns',0):10d} "
              f"{dg.get('tool_call_count',0):9d} {bs.get('tool_call_count',0):11d} "
              f"{dg.get('input_tokens',0):9d} {bs.get('input_tokens',0):11d} "
              f"${dg.get('total_cost_usd',0):7.4f} ${bs.get('total_cost_usd',0):9.4f}")

    # ── 保存完整 JSON ──
    export = {
        "config": {
            "mcp_config": MCP_CONFIG,
            "chunks_file": CHUNKS_FILE,
            "budget_per_case": MAX_BUDGET_PER_CASE,
        },
        "totals": {
            "dg_wall_s": dg_total_wall,
            "bs_wall_s": bs_total_wall,
            "dg_cost": dg_total_cost,
            "bs_cost": bs_total_cost,
            "dg_tokens": dg_total_tokens,
            "bs_tokens": bs_total_tokens,
            "dg_tools": dg_total_tools,
            "bs_tools": bs_total_tools,
            "dg_turns": dg_total_turns,
            "bs_turns": bs_total_turns,
        },
        "cases": results,
    }
    Path(OUTPUT_FILE).write_text(json.dumps(export, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n完整结果: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
