"""Run reproducible Claude Code baseline/current comparisons for existing cases."""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs/evaluation/pcie-agent-benchmark.md"
OUT = ROOT / "benchmark_runs/adaptive_context_compare_20260714"
MCP_CONFIG = ROOT / "benchmark_runs/baseline_docgraph_compare/docgraph_mcp.json"

CASE_HEADINGS = {
    "case1": "## Case 1：",
    "case8b": "## Case 8B：",
    "case11": "## Case 11：",
}

COMMON = """

回答必须包含 answer、engineering_artifact、evidence、uncertainty 四部分。证据至少包含文档名和页码；无法确定时明确说明，不允许编造。

评测隔离规则：不得读取 benchmark_runs 中的历史答案或报告，不得读取 docs/evaluation 中的期望证据和评分说明，也不得使用预生成 JSON、RDL 或 CSV。只完成题目本身。
"""

MODE_RULES = {
    "baseline": """
这是 baseline 运行。只能用 Read/Bash 直接读取以下两个 PDF：
- spec/PCIE Subsystem Spec_v3.21.pdf
- spec/PCIe Subsystem TRS_r2p0.pdf

不得访问 .docgraph，不得调用任何 DocGraph 工具。最多直接读取 15 个 PDF 页面；如需更多页面，在 uncertainty 中说明超出 page budget 以及还需哪些页面。
""",
    "current": """
这是当前 DocGraph 运行。评测范围严格限定为以下两个文档，不得查询或引用索引中的其他文档：
- arm::protocol::PCIE Subsystem Spec_v3.21
- arm::doc::PCIe Subsystem TRS_r2p0

第一次调用应使用 docgraph_context(mode="auto", doc_ids=[上述两个文档]) 获取与任务匹配的透明文档视图。再根据 coverage、rank_reasons、next_cursor 和 block_ids 判断是否需要改写 task、继续调用 docgraph_context、docgraph_fetch 或其他 DocGraph 工具。不要切换 full 模式绕过大语料检索，不要把检索结果误称为完整文档。只有 DocGraph 明确缺少所需证据时才直接读取 PDF，并在 uncertainty 中说明原因。
需要核对多个 L1 命中对应的 L0 证据时，优先使用 docgraph_fetch_many(chunk_ids)，不要对一串 chunk 逐个调用 docgraph_fetch。
""",
}


def extract_prompt(case_id: str) -> str:
    text = SPEC.read_text(encoding="utf-8")
    start = text.index(CASE_HEADINGS[case_id])
    prompt_start = text.index("**Prompt**", start) + len("**Prompt**")
    prompt_end = text.index("**考察点**", prompt_start)
    return text[prompt_start:prompt_end].strip()


def run(case_id: str, mode: str) -> Path:
    prompt = extract_prompt(case_id) + COMMON + MODE_RULES[mode]
    command = [
        "claude",
        "-p",
        "--verbose",
        "--output-format",
        "json",
        "--model",
        "sonnet",
        "--effort",
        "medium",
        "--max-budget-usd",
        "1.50",
        "--no-session-persistence",
        "--permission-mode",
        "bypassPermissions",
    ]
    if mode == "baseline":
        command.extend(["--tools", "Read,Bash"])
    else:
        command.extend(
            [
                "--mcp-config",
                str(MCP_CONFIG),
                "--strict-mcp-config",
                "--tools",
                (
                    "Read,Bash,mcp__docgraph__docgraph_context,"
                    "mcp__docgraph__docgraph_search_chunks,"
                    "mcp__docgraph__docgraph_fetch,"
                    "mcp__docgraph__docgraph_fetch_many,"
                    "mcp__docgraph__docgraph_search,"
                    "mcp__docgraph__docgraph_section,"
                    "mcp__docgraph__docgraph_neighbors,"
                    "mcp__docgraph__docgraph_files,"
                    "mcp__docgraph__docgraph_status"
                ),
            ]
        )

    OUT.mkdir(parents=True, exist_ok=True)
    output_path = OUT / f"{case_id}_{mode}.json"
    started = time.time()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            input=prompt,
            text=True,
            capture_output=True,
            check=False,
            timeout=480,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.time() - started
        payload = {
            "type": "runner_timeout",
            "wall_time_s": elapsed,
            "stdout": exc.stdout,
            "stderr": exc.stderr,
        }
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        return output_path
    elapsed = time.time() - started
    if completed.returncode != 0:
        payload = {
            "type": "runner_error",
            "returncode": completed.returncode,
            "wall_time_s": elapsed,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    else:
        try:
            decoded = json.loads(completed.stdout)
            if isinstance(decoded, list):
                result = next(
                    (
                        event
                        for event in reversed(decoded)
                        if isinstance(event, dict) and event.get("type") == "result"
                    ),
                    None,
                )
                payload = dict(result or {"type": "runner_error"})
                payload["events"] = decoded
            elif isinstance(decoded, dict):
                payload = decoded
            else:
                payload = {
                    "type": "runner_error",
                    "decoded_output": decoded,
                    "stderr": completed.stderr,
                }
        except json.JSONDecodeError:
            payload = {
                "type": "runner_error",
                "returncode": completed.returncode,
                "wall_time_s": elapsed,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
    payload["benchmark_wall_time_s"] = round(elapsed, 3)
    payload["benchmark_case"] = case_id
    payload["benchmark_mode"] = mode
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "path": str(output_path),
                "type": payload.get("type"),
                "turns": payload.get("num_turns"),
                "cost": payload.get("total_cost_usd"),
                "wall_time_s": payload["benchmark_wall_time_s"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_id", choices=sorted(CASE_HEADINGS))
    parser.add_argument("mode", choices=sorted(MODE_RULES))
    args = parser.parse_args()
    run(args.case_id, args.mode)


if __name__ == "__main__":
    main()
