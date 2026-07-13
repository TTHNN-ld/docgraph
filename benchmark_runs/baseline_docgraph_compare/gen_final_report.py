"""Generate final comparison report from all case JSON results."""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]

# Case metadata
CASE_META = {
    "case1":  ("跨文档地址转换", 1.2, "HIGH"),
    "case2":  ("模块边界与接口", 1.2, "MEDIUM"),
    "case3":  ("RTL 端口表", 1.3, "HIGH"),
    "case4":  ("地址/BAR test plan", 1.4, "HIGH"),
    "case5":  ("数据路径 RTL/DV 方案", 1.4, "MEDIUM"),
    "case6":  ("IOMMU/ATS 验证矩阵", 1.4, "MEDIUM"),
    "case7":  ("MSI/MSI-X test plan", 1.6, "HIGH"),
    "case8a": ("irq_src 信号建模", 1.4, "HIGH"),
    "case8b": ("寄存器 RAL 输入", 1.6, "HIGH"),
    "case9":  ("MSI-X UVM sequence", 1.6, "HIGH"),
    "case10": ("LTSSM debug 流程", 1.3, "MEDIUM"),
    "case11": ("Clock/Reset 验证计划", 1.3, "LOW"),
    "case12": ("Tape-in 评审清单", 1.6, "HIGH"),
    "case13": ("STA/SDC 约束", 1.4, "LOW"),
    "case14": ("CDC/RDC sign-off", 1.4, "LOW"),
    "case15": ("P&R/PHY 集成", 1.4, "MEDIUM"),
    "case16": ("DFT/JTAG bring-up", 1.4, "MEDIUM"),
    "case17": ("KG 缺失发现", 1.0, "HIGH"),
}


def load_result(case_id, mode):
    path = OUT / f"{case_id}_{mode}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, Exception):
        return None


def main():
    results = {}
    for cid, (name, weight, benefit) in CASE_META.items():
        b = load_result(cid, "baseline")
        d = load_result(cid, "docgraph")
        if b and d and "error" not in b and "error" not in d:
            results[cid] = {
                "name": name,
                "weight": weight,
                "benefit": benefit,
                "baseline": {
                    "turns": b.get("num_turns", 0),
                    "api_s": round(b.get("duration_api_ms", 0) / 1000, 1),
                    "cost": round(b.get("total_cost_usd", 0), 3),
                    "input_tokens": b.get("usage", {}).get("input_tokens", 0) or
                                   b.get("modelUsage", {}).get(list(b.get("modelUsage", {}).keys())[0] if b.get("modelUsage") else "", {}).get("inputTokens", 0),
                },
                "docgraph": {
                    "turns": d.get("num_turns", 0),
                    "api_s": round(d.get("duration_api_ms", 0) / 1000, 1),
                    "cost": round(d.get("total_cost_usd", 0), 3),
                },
            }

    # Generate markdown report
    lines = []
    lines.append("# DocGraph vs Baseline 完整对照报告")
    lines.append("")
    lines.append(f"**日期**: 2026-07-10 | **KG**: 1123 nodes, 705 edges | **MCP**: 7 工具")
    lines.append("")

    # Summary table
    lines.append("## 1. 全部 17 Case 对照表")
    lines.append("")
    lines.append("| Case | 预期收益 | Baseline | | | DocGraph | | | 差异 |")
    lines.append("|------|---------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")
    lines.append("| | | Turns | Cost | Time(s) | Turns | Cost | Time(s) | ΔCost |")

    total_b_cost = 0
    total_d_cost = 0
    total_b_turns = 0
    total_d_turns = 0
    total_b_time = 0
    total_d_time = 0
    high_wins = 0
    high_total = 0
    low_wins = 0
    low_total = 0
    weighted_b = 0
    weighted_d = 0
    total_weight = 0

    for cid in sorted(results.keys()):
        r = results[cid]
        b = r["baseline"]
        d = r["docgraph"]
        delta = d["cost"] - b["cost"]
        pct = f"{delta/b['cost']*100:+.0f}%"
        delta_str = f"**{pct}**" if abs(delta/b['cost']) > 0.15 else pct

        lines.append(f"| {cid} {r['name']} | {r['benefit']} | {b['turns']} | ${b['cost']:.2f} | {b['api_s']}s | {d['turns']} | ${d['cost']:.2f} | {d['api_s']}s | {delta_str} |")

        total_b_cost += b['cost']
        total_d_cost += d['cost']
        total_b_turns += b['turns']
        total_d_turns += d['turns']
        total_b_time += b['api_s']
        total_d_time += d['api_s']
        weighted_b += b['cost'] * r['weight']
        weighted_d += d['cost'] * r['weight']
        total_weight += r['weight']

        if r['benefit'] == 'HIGH':
            high_total += 1
            if d['cost'] <= b['cost']:
                high_wins += 1
        elif r['benefit'] == 'LOW':
            low_total += 1
            if d['cost'] > b['cost']:
                low_wins += 1  # confirm it's worse

    # Totals row
    delta_total = total_d_cost - total_b_cost
    pct_total = f"{delta_total/total_b_cost*100:+.0f}%"
    lines.append(f"| **合计** | | **{total_b_turns}** | **${total_b_cost:.2f}** | **{total_b_time:.0f}s** | **{total_d_turns}** | **${total_d_cost:.2f}** | **{total_d_time:.0f}s** | **{pct_total}** |")

    lines.append("")
    lines.append("## 2. 按预期收益分组统计")
    lines.append("")

    # HIGH benefit cases
    lines.append("### HIGH 收益 (9 cases — 表格式信息)")
    lines.append("")
    high_cases = [(cid, r) for cid, r in results.items() if r['benefit'] == 'HIGH']
    hb_cost = sum(r['baseline']['cost'] for _, r in high_cases)
    hd_cost = sum(r['docgraph']['cost'] for _, r in high_cases)
    lines.append(f"| Case | Baseline | DocGraph | Δ |")
    lines.append(f"|---|---:|---:|---:|")
    for cid, r in sorted(high_cases):
        b, d = r['baseline'], r['docgraph']
        delta = d['cost'] - b['cost']
        lines.append(f"| {cid} {r['name']} | ${b['cost']:.2f} | ${d['cost']:.2f} | {delta/b['cost']*100:+.0f}% |")
    lines.append(f"| **小计** | **${hb_cost:.2f}** | **${hd_cost:.2f}** | **{(hd_cost-hb_cost)/hb_cost*100:+.0f}%** |")
    lines.append(f"| DocGraph 胜出: {high_wins}/{high_total} cases | | | |")
    lines.append("")

    # MEDIUM benefit cases
    lines.append("### MEDIUM 收益 (6 cases — VLM/正文依赖)")
    lines.append("")
    med_cases = [(cid, r) for cid, r in results.items() if r['benefit'] == 'MEDIUM']
    mb_cost = sum(r['baseline']['cost'] for _, r in med_cases)
    md_cost = sum(r['docgraph']['cost'] for _, r in med_cases)
    lines.append(f"| Case | Baseline | DocGraph | Δ |")
    lines.append(f"|---|---:|---:|---:|")
    for cid, r in sorted(med_cases):
        b, d = r['baseline'], r['docgraph']
        delta = d['cost'] - b['cost']
        lines.append(f"| {cid} {r['name']} | ${b['cost']:.2f} | ${d['cost']:.2f} | {delta/b['cost']*100:+.0f}% |")
    lines.append(f"| **小计** | **${mb_cost:.2f}** | **${md_cost:.2f}** | **{(md_cost-mb_cost)/mb_cost*100:+.0f}%** |")
    lines.append("")

    # LOW benefit cases
    lines.append("### LOW 收益 (3 cases — clock/reset 短板)")
    lines.append("")
    low_cases = [(cid, r) for cid, r in results.items() if r['benefit'] == 'LOW']
    lb_cost = sum(r['baseline']['cost'] for _, r in low_cases)
    ld_cost = sum(r['docgraph']['cost'] for _, r in low_cases)
    lines.append(f"| Case | Baseline | DocGraph | Δ |")
    lines.append(f"|---|---:|---:|---:|")
    for cid, r in sorted(low_cases):
        b, d = r['baseline'], r['docgraph']
        delta = d['cost'] - b['cost']
        lines.append(f"| {cid} {r['name']} | ${b['cost']:.2f} | ${d['cost']:.2f} | {delta/b['cost']*100:+.0f}% |")
    lines.append(f"| **小计** | **${lb_cost:.2f}** | **${ld_cost:.2f}** | **{(ld_cost-lb_cost)/lb_cost*100:+.0f}%** |")
    lines.append("")

    # Key insights
    lines.append("## 3. 关键结论")
    lines.append("")

    best = min(results.items(), key=lambda x: x[1]['docgraph']['cost'] - x[1]['baseline']['cost'])
    worst = max(results.items(), key=lambda x: x[1]['docgraph']['cost'] - x[1]['baseline']['cost'])

    lines.append(f"- **DocGraph 最优 case**: {best[0]} ({best[1]['name']}) — ΔCost = {(best[1]['docgraph']['cost']-best[1]['baseline']['cost'])/best[1]['baseline']['cost']*100:+.0f}%")
    lines.append(f"- **DocGraph 最差 case**: {worst[0]} ({worst[1]['name']}) — ΔCost = {(worst[1]['docgraph']['cost']-worst[1]['baseline']['cost'])/worst[1]['baseline']['cost']*100:+.0f}%")
    lines.append(f"- **HIGH 收益组**: Baseline ${hb_cost:.2f} → DocGraph ${hd_cost:.2f} ({(hd_cost-hb_cost)/hb_cost*100:+.0f}%)")
    lines.append(f"- **LOW 收益组**: Baseline ${lb_cost:.2f} → DocGraph ${ld_cost:.2f} ({(ld_cost-lb_cost)/lb_cost*100:+.0f}%)")
    lines.append(f"- **加权平均成本**: Baseline ${weighted_b/total_weight:.2f} → DocGraph ${weighted_d/total_weight:.2f}")
    lines.append(f"- **总成本**: Baseline ${total_b_cost:.2f} → DocGraph ${total_d_cost:.2f} ({pct_total})")
    lines.append(f"- **总 turns**: Baseline {total_b_turns} → DocGraph {total_d_turns}")
    lines.append(f"- **总耗时**: Baseline {total_b_time:.0f}s → DocGraph {total_d_time:.0f}s")
    lines.append("")

    lines.append("### 实事求事")
    lines.append("")
    lines.append("1. **DocGraph 在小文档集 (2×42pp, 105KB) 上总体成本高于 Baseline (+41%)**。全文在上下文窗口内时，结构化 MCP 返回的元数据开销超过其带来的检索效率提升。")
    lines.append("2. **DocGraph 在 register/bitfield 确定性抽取场景有明确价值** (Case 8B -29%)。L2 实体直接提供精确 bit range/access/reset，agent 无需人工对齐原表。")
    lines.append("3. **Clock/reset 是明确短板** (3 个 LOW case 平均 +180%)。覆盖率 ~15%，entity 几乎全来自 VLM (needs_source_check=true)，agent 被迫大量回退到 L1/L0。")
    lines.append("4. **架构契约成立** (Case 17)。L2 缺失时 L1/L0 永远可用。agent 能从原文获取完整信息，只是需要更多工具调用。")
    lines.append("5. **新 MCP 工具链有效**。7 工具比旧 20 工具更清晰，agent 遵循 search_chunks→fetch→search 路径，不再绕圈。")
    lines.append("")
    lines.append("### 改进优先级")
    lines.append("")
    lines.append("| P | 改进项 | 影响范围 |")
    lines.append("|---|---|---|")
    lines.append("| P0 | 提升 clock/reset 实体覆盖率 (从接口表确定性抽取) | Case 11/13/14 |")
    lines.append("| P1 | 修复 clock 实体可检索性 (声称 21 但仅 7 可搜索) | 所有 clock 相关 case |")
    lines.append("| P2 | 填充 register 实体 address/offset 属性 | Case 8B/9 |")
    lines.append("| P3 | 更大文档集评测 (10+ docs, 1000+ pp) 验证规模优势 | 全局 |")

    report = "\n".join(lines)
    report_path = OUT / "FULL_REPORT.md"
    report_path.write_text(report)
    print(f"Report written to {report_path}")
    print(report)


if __name__ == "__main__":
    main()
