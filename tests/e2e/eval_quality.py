"""答案质量评估: LLM-as-Judge 对 DG 和 Base 的回答打分。

对每个 case，将两个回答匿名后交给 LLM 评分，维度:
  - 准确性: 回答中的事实是否有依据、数值是否正确
  - 完整性: 是否覆盖了问题的所有方面
  - 可操作性: 是否给出具体的名称/地址/数值供工程师直接使用
  - 可追溯性: 是否引用章节/页码便于验证

运行: python tests/e2e/eval_quality.py
输出: .docgraph/benchmark_results/quality_scores.json
"""

from __future__ import annotations

import json, os, sys, time
from pathlib import Path

_project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_project_root))

from docgraph.core.config import project_root_from_cwd, load_config
from docgraph.core.dotenv import autoload_env
from pydantic import BaseModel, Field

from docgraph.llm.client import LLMClient, CostTracker, make_provider
from rich.console import Console
from rich.table import Table


class DimensionScore(BaseModel):
    accuracy: int = Field(ge=1, le=5, description="准确性 1-5")
    completeness: int = Field(ge=1, le=5, description="完整性 1-5")
    actionability: int = Field(ge=1, le=5, description="可操作性 1-5")
    traceability: int = Field(ge=1, le=5, description="可追溯性 1-5")
    notes: str = ""


class JudgeResult(BaseModel):
    answer_a: DimensionScore
    answer_b: DimensionScore
    comparison: str = Field(description="A 更好 / B 更好 / 相当")

console = Console(highlight=False)

JUDGE_SYSTEM = """你是一个芯片设计文档质量评审专家。你的任务是评估两个 AI 助手对同一个问题的回答质量。

你需要从以下四个维度打分（1-5 分）：

1. **准确性 (accuracy)**: 回答中的事实是否与文档一致，数字/名称是否正确。有事实错误扣分。
2. **完整性 (completeness)**: 是否覆盖了问题的所有关键方面。遗漏重要信息扣分。
3. **可操作性 (actionability)**: 是否给出具体的数值、地址、名称、信号名等可直接使用的信息。
4. **可追溯性 (traceability)**: 是否标注了章节号、页码、信号名等便于验证的引用。

评分标准：
- 5 = 优秀，几乎无可挑剔
- 4 = 良好，有少量遗漏或模糊之处
- 3 = 一般，回答了部分问题但不够完整或不够具体
- 2 = 较差，回答模糊或存在明显遗漏
- 1 = 很差，几乎没有有效信息或存在严重错误

请输出 JSON 格式：
{
  "answer_a": {"accuracy": 4, "completeness": 3, "actionability": 5, "traceability": 4, "notes": "简评"},
  "answer_b": {"accuracy": 3, "completeness": 4, "actionability": 2, "traceability": 2, "notes": "简评"},
  "comparison": "A 更好 / B 更好 / 相当"
}

注意：
- A 和 B 的顺序是随机的，请客观评价，不要偏向某一方。
- notes 请用中文简要说明扣分原因（1-2 句话）。
"""

JUDGE_PROMPT = """问题：
{question}

回答 A：
{answer_a}

回答 B：
{answer_b}

请按 JSON 格式输出评分。"""


def load_answers() -> list[dict]:
    """从 benchmark 结果中加载所有回答。"""
    results_dir = _project_root / ".docgraph" / "benchmark_results"
    questions = [
        "FTB 模块的功能是什么？它有哪些关键设计参数？请从文档中找出具体的技术细节。",
        "sbpctl 寄存器的地址是多少？它有哪些位域？分别控制什么？",
        "BPU 的 s1/s2/s3 三级流水各自负责什么工作？什么情况会导致流水线冲刷？",
        "BPU 到 FTQ 的接口有哪些握手信号？信号的时序关系是怎样的？",
        "BPU 分支预测错误时的恢复流程是什么？有哪些重定向类型？",
        "ICache 有哪些可配置参数？各自的默认值和约束是什么？",
        "TAGE 预测器在什么情况下准确率会下降？有哪些已知的性能瓶颈？",
        "信号 'pc' 在 BPU 中是怎么产生和传递的？经过哪些模块？",
        "RAS 预测器的持久化队列有什么优点？它能解决什么问题？",
        "一条指令在香山处理器中从取指到写回的完整流水线路径是怎样的？各阶段的关键模块和可能出现的异常有哪些？",
    ]
    titles = ["模块定位","寄存器配置","时序理解","接口协议","异常处理","配置参数","性能限制","信号追踪","架构差异","综合理解"]

    cases = []
    for i in range(1, 11):
        case = {"id": i, "title": titles[i-1], "question": questions[i-1]}
        for mode in ['docgraph', 'base']:
            fn = results_dir / f"case{i}_{mode}.json"
            try:
                d = json.loads(fn.read_text())
                case[mode + '_answer'] = d.get('result', '') or '(无回答)'
                case[mode + '_error'] = d.get('is_error', False)
            except Exception:
                case[mode + '_answer'] = '(无回答)'
                case[mode + '_error'] = True
        cases.append(case)
    return cases


def evaluate_case(case: dict, llm: LLMClient, tracker: CostTracker) -> dict:
    """对一个 case 的两个回答进行评分。"""
    import random
    # 随机化顺序避免位置偏见
    if random.random() < 0.5:
        answer_a = case['docgraph_answer']
        answer_b = case['base_answer']
        a_is_dg = True
    else:
        answer_a = case['base_answer']
        answer_b = case['docgraph_answer']
        a_is_dg = False

    # 截断过长回答（DeepSeek 上下文有限）
    max_len = 2000
    answer_a = answer_a[:max_len]
    answer_b = answer_b[:max_len]

    prompt = JUDGE_PROMPT.format(
        question=case['question'],
        answer_a=answer_a,
        answer_b=answer_b,
    )

    try:
        result = llm.json(
            prompt, schema=JudgeResult,
            max_tokens=2048, temperature=0.0,
            system=JUDGE_SYSTEM, extractor="quality_judge",
            extra_body={"enable_thinking": False},
        )
    except Exception as e:
        console.print(f"  [red]Judge error: {e}[/red]")
        result = JudgeResult(
            answer_a=DimensionScore(accuracy=1, completeness=1, actionability=1, traceability=1, notes=str(e)),
            answer_b=DimensionScore(accuracy=1, completeness=1, actionability=1, traceability=1, notes=str(e)),
            comparison="error",
        )

    # 还原身份
    if a_is_dg:
        return {
            "docgraph": {
                "accuracy": result.answer_a.accuracy,
                "completeness": result.answer_a.completeness,
                "actionability": result.answer_a.actionability,
                "traceability": result.answer_a.traceability,
                "notes": result.answer_a.notes,
            },
            "base": {
                "accuracy": result.answer_b.accuracy,
                "completeness": result.answer_b.completeness,
                "actionability": result.answer_b.actionability,
                "traceability": result.answer_b.traceability,
                "notes": result.answer_b.notes,
            },
            "comparison": result.comparison,
            "dg_is_a": True,
        }
    else:
        return {
            "docgraph": {
                "accuracy": result.answer_b.accuracy,
                "completeness": result.answer_b.completeness,
                "actionability": result.answer_b.actionability,
                "traceability": result.answer_b.traceability,
                "notes": result.answer_b.notes,
            },
            "base": {
                "accuracy": result.answer_a.accuracy,
                "completeness": result.answer_a.completeness,
                "actionability": result.answer_a.actionability,
                "traceability": result.answer_a.traceability,
                "notes": result.answer_a.notes,
            },
            "comparison": result.comparison,
            "dg_is_a": False,
        }


def main():
    console.print("[bold]答案质量评估 — LLM-as-Judge[/bold]\n")

    root = project_root_from_cwd()
    autoload_env(root)
    cfg = load_config(root)

    # 初始化 LLM
    provider_name = cfg.llm.provider
    provider_cfg = cfg.llm.providers.get(provider_name)
    if provider_cfg is None:
        from docgraph.core.config import LLMProviderConfig
        provider_cfg = LLMProviderConfig(api_key_env="OPENAI_API_KEY", base_url_env="OPENAI_BASE_URL")
    api_key = provider_cfg.api_key or os.environ.get(provider_cfg.api_key_env)
    if not api_key:
        console.print("[red]LLM API key not configured[/red]")
        sys.exit(1)
    kwargs = {"api_key_env": provider_cfg.api_key_env, "api_key": provider_cfg.api_key}
    if provider_name in ("openai", "openai_compat", "volces", "deepseek"):
        kwargs["base_url_env"] = provider_cfg.base_url_env
        kwargs["base_url"] = provider_cfg.base_url
    provider = make_provider(provider_name, **kwargs)
    tracker = CostTracker()
    llm = LLMClient(
        provider,
        tiers={"fast": cfg.llm.tiers.fast, "balanced": cfg.llm.tiers.balanced, "accurate": cfg.llm.tiers.accurate},
        tracker=tracker,
    )

    cases = load_answers()
    results = []

    for i, case in enumerate(cases):
        console.print(f"[{i+1}/10] {case['title']}...", end=" ")
        if case.get('docgraph_error') and case.get('base_error'):
            console.print("[yellow]both failed, skip[/yellow]")
            results.append({"id": case["id"], "title": case["title"], "skipped": True})
            continue

        result = evaluate_case(case, llm, tracker)
        result["id"] = case["id"]
        result["title"] = case["title"]
        result["question"] = case["question"]
        results.append(result)

        dg = result["docgraph"]
        bs = result["base"]
        dg_avg = sum(dg[k] for k in ["accuracy","completeness","actionability","traceability"]) / 4
        bs_avg = sum(bs[k] for k in ["accuracy","completeness","actionability","traceability"]) / 4
        winner = "DG" if dg_avg > bs_avg else ("Base" if bs_avg > dg_avg else "Tie")
        console.print(f"[dim]DG={dg_avg:.1f} Base={bs_avg:.1f} → {winner}[/dim]")

    # ── 汇总 ──
    console.print("\n[bold]质量评分汇总[/bold]")
    tbl = Table(show_header=True, header_style="bold")
    for col in ("Case", "DG 准确", "Base 准确", "DG 完整", "Base 完整", "DG 可操作", "Base 可操作", "DG 可追溯", "Base 可追溯", "DG 均分", "Base 均分", "胜出"):
        tbl.add_column(col)

    dg_all = {"accuracy":[],"completeness":[],"actionability":[],"traceability":[]}
    bs_all = {"accuracy":[],"completeness":[],"actionability":[],"traceability":[]}
    valid = 0
    for r in results:
        if r.get("skipped"):
            continue
        valid += 1
        dg = r["docgraph"]; bs = r["base"]
        for k in dg_all:
            dg_all[k].append(dg[k]); bs_all[k].append(bs[k])
        dg_avg = sum(dg[k] for k in dg_all) / 4
        bs_avg = sum(bs[k] for k in bs_all) / 4
        winner = "DG" if dg_avg > bs_avg else ("Base" if bs_avg > dg_avg else "=")
        tbl.add_row(
            r["title"],
            str(dg["accuracy"]), str(bs["accuracy"]),
            str(dg["completeness"]), str(bs["completeness"]),
            str(dg["actionability"]), str(bs["actionability"]),
            str(dg["traceability"]), str(bs["traceability"]),
            f"{dg_avg:.1f}", f"{bs_avg:.1f}",
            winner,
        )

    console.print(tbl)
    console.print(f"\nDG 总均分: {sum(sum(v) for v in dg_all.values())/(4*valid):.1f}  |  "
                   f"Base 总均分: {sum(sum(v) for v in bs_all.values())/(4*valid):.1f}  |  "
                   f"评估成本: ${tracker.cost_usd:.4f}")

    # 保存
    out = _project_root / ".docgraph" / "benchmark_results" / "quality_scores.json"
    out.write_text(json.dumps({
        "dg_avg": {k: sum(v)/valid for k,v in dg_all.items()},
        "bs_avg": {k: sum(v)/valid for k,v in bs_all.items()},
        "dg_total": sum(sum(v) for v in dg_all.values())/(4*valid),
        "bs_total": sum(sum(v) for v in bs_all.values())/(4*valid),
        "cases": results,
    }, ensure_ascii=False, indent=2), "utf-8")
    console.print(f"\n[green]结果已保存: {out}[/green]")


if __name__ == "__main__":
    main()
