"""Evaluate the new MCP tool chain against benchmark cases.

Runs L1 discovery (search_chunks) and L2 acceleration (search) for each case,
measuring whether the tool chain returns relevant, self-verifiable evidence.
Does NOT require Claude Code — uses QueryEngine directly.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docgraph.core.config import docgraph_dir, project_root_from_cwd
from docgraph.graph.schema import NodeKind
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.query.engine import QueryEngine


# ── benchmark case definitions (from pcie-agent-benchmark.md) ──

# Each case: (id, weight, [L1_query_terms], expected_pages, [L2_entity_names], notes)
# L1 uses short keyword queries; L2 uses specific entity name lookups
CASES = [
    ("Case 1", 1.2,
     ["System Address Map", "BAR", "Inbound data path", "Outbound", "iATU", "eATU", "ATS", "IOMMU"],
     [16, 18, 19, 20, 21, 23, 25, 27, 33, 35, 36],
     ["System Address Map", "IATU_REGION_CTRL", "BAR"],
     "跨文档地址转换"),

    ("Case 2", 1.2,
     ["Interfaces", "PCIe Subsystem Architecture", "AXI Master", "AXI Slave", "clock", "reset"],
     [13, 15, 16, 17, 18, 19, 21, 22, 23, 24, 29],
     ["PCIe_top", "PHY", "PCIE_CRG", "cfg_clk", "mstr_aclk"],
     "模块边界与接口"),

    ("Case 3", 1.3,
     ["Interfaces", "AXI", "TX", "RX", "PHY", "clock", "PIPE"],
     [13, 29, 30],
     ["AXI Master", "PIPE", "PHY", "cfg_clk"],
     "顶层端口表"),

    ("Case 4", 1.4,
     ["System Address Map", "BAR Space", "Address Map", "iATU", "ReMap"],
     [16, 19, 20, 21, 35, 36],
     ["System Address Map", "BAR", "iATU", "ReMap"],
     "地址空间 test plan"),

    ("Case 5", 1.4,
     ["Inbound data path", "Outbound", "DMA", "NoC", "iATU", "IOMMU"],
     [18, 20, 21, 23, 25, 27, 33, 35],
     ["DMA", "iATU", "IOMMU", "NoC"],
     "数据路径方案"),

    ("Case 6", 1.4,
     ["IOMMU", "ATS", "ATC", "translation", "Enable IOMMU", "Enable ATS"],
     [25, 27, 33, 34, 35],
     ["IOMMU", "ATS"],
     "IOMMU/ATS 验证矩阵"),

    ("Case 7", 1.6,
     ["MSI", "MSI-X", "Legacy Interrupt", "REQ_PCIE", "doorbell", "vector"],
     [24, 25, 26, 27, 28, 34],
     ["MSI", "MSI-X", "INT_NUM", "REQ_PCIE_TRS"],
     "MSI/MSI-X test plan"),

    ("Case 8A", 1.4,
     ["irq_src", "interrupt source", "hot_reset_int", "perst_int", "pll_lost_lock"],
     [25],
     ["hot_reset_int", "perst_int", "irq_src", "trgt_cpl_timeout"],
     "中断源信号建模"),

    ("Case 8B", 1.6,
     ["per_vector_misc", "INT_NUM", "Reg name", "Field", "Msb", "Lsb"],
     [27, 34, 35, 36],
     ["per_vector_misc", "INT_NUM", "freeze_reg", "ltssm_state_reg"],
     "寄存器 RAL 输入"),

    ("Case 9", 1.6,
     ["INT_NUM", "MSI-X doorbell", "per_vector_misc", "mask_bit", "priority", "pf", "vf"],
     [27],
     ["per_vector_misc", "mask_bit", "priority", "pf", "vf", "vfactive", "tc"],
     "MSI-X UVM sequence"),

    ("Case 10", 1.3,
     ["LTSSM", "freeze_reg", "shift_reg", "ltssm_state_reg", "state debug"],
     [35],
     ["freeze_reg", "ltssm_state_reg", "shift_reg"],
     "LTSSM debug 流程"),

    ("Case 11", 1.3,
     ["Clock", "Reset", "CRG", "PLL", "GFM", "DIV", "MUX", "PERST", "Hot Reset"],
     [21, 23],
     ["CRG", "PLL", "GFM", "PERST", "clk_in", "clk_ref"],
     "Clock/reset 验证计划"),

    ("Case 12", 1.6,
     ["Address Map", "IOMMU", "ATS", "MSI", "DMA", "Clock", "Reset", "PHY", "JTAG"],
     list(range(13, 40)),
     ["IOMMU", "ATS", "MSI", "DMA", "JTAG", "PHY", "PERST"],
     "Tape-in 评审清单"),

    ("Case 13", 1.4,
     ["Clock", "CRG", "PLL", "GFM", "DIV", "MUX", "Reset", "PERST"],
     [21, 23],
     ["CRG", "PLL", "GFM", "DIV", "MUX", "PERST"],
     "STA/SDC 约束"),

    ("Case 14", 1.4,
     ["clock domain", "reset domain", "PERST", "hot reset", "warm reset", "cold reset"],
     [13, 21, 23, 24],
     ["PERST", "cfg_clk", "mstr_aclk", "slv_aclk", "cfg_rst_n"],
     "CDC/RDC sign-off"),

    ("Case 15", 1.4,
     ["PHY", "UPCS", "PIPE", "CRG", "NoC", "AXI", "JTAG", "reference clock", "TX", "RX", "lane"],
     [13, 15, 37, 38, 39],
     ["PHY", "UPCS", "PIPE", "CRG", "JTAG"],
     "P&R/PHY 集成"),

    ("Case 16", 1.4,
     ["JTAG", "debug", "LTSSM", "TDR", "isolation", "level shifter", "gating"],
     [35, 37, 38],
     ["JTAG", "TDR", "LTSSM", "freeze_reg"],
     "DFT/JTAG bring-up"),

    ("Case 17", 1.0,
     ["clock", "register", "coverage", "KG", "missing"],
     list(range(13, 40)),
     ["clock", "register", "signal", "module"],
     "KG 缺失发现"),
]


def eval_case(
    qe: QueryEngine,
    case_id: str,
    weight: float,
    l1_queries: list[str],
    expected_pages: list[int],
    l2_names: list[str],
    notes: str,
) -> dict:
    """Evaluate one case against the tool chain using agent-like queries."""
    t0 = time.time()

    # ── L1 discovery (search_chunks) — try multiple short queries ──
    all_chunk_pages: set[int] = set()
    total_hits = 0
    for query in l1_queries:
        hits = qe.search_chunks(query, limit=5)
        total_hits += len(hits)
        for h in hits:
            all_chunk_pages.add(h["page"])
            if h.get("page_start"):
                all_chunk_pages.add(h["page_start"])
            if h.get("page_end"):
                all_chunk_pages.add(h["page_end"])

    page_recall = len(all_chunk_pages & set(expected_pages)) / max(len(expected_pages), 1)
    has_hits = total_hits > 0

    # ── L2 acceleration (search entities by name) ──
    l2_results: dict[str, int] = {}
    for name in l2_names:
        nodes = qe.search(name, limit=5, use_semantic=False)
        l2_results[name] = len(nodes)

    l2_found = sum(1 for v in l2_results.values() if v > 0)
    l2_coverage = l2_found / max(len(l2_names), 1)

    # ── Source quality: fetch first hit, check entity quality ──
    source_quality_ok = True
    vlm_entity_count = 0
    table_entity_count = 0
    if all_chunk_pages:
        try:
            # Find a chunk on the first expected page
            for query in l1_queries[:3]:
                hits = qe.search_chunks(query, limit=3)
                if hits:
                    result = qe.fetch(hits[0]["chunk_id"])
                    entities = result.get("entities", [])
                    for e in entities:
                        sq = e.get("source_quality", {})
                        if sq.get("needs_source_check", False):
                            vlm_entity_count += 1
                        else:
                            table_entity_count += 1
                    break
        except Exception:
            pass

    duration = round(time.time() - t0, 3)

    return {
        "case_id": case_id,
        "weight": weight,
        "notes": notes,
        "l1_queries_used": len(l1_queries),
        "chunk_hits": total_hits,
        "pages_found": sorted(all_chunk_pages & set(expected_pages)),
        "page_recall": round(page_recall, 3),
        "has_hits": has_hits,
        "l2_results": l2_results,
        "l2_coverage": round(l2_coverage, 3),
        "table_entities": table_entity_count,
        "vlm_entities": vlm_entity_count,
        "duration_s": duration,
    }


def main():
    root = project_root_from_cwd()
    store = SQLiteGraphStore(docgraph_dir(root) / "graph.db")
    store.init_schema()
    qe = QueryEngine(store)

    print("=" * 70)
    print("DocGraph MCP Tool Chain Evaluation")
    print(f"KG: {store.count_nodes()} nodes, {store.count_edges()} edges")
    print(f"Docs: {store.list_docs()}")
    print("=" * 70)

    results = []
    for case_def in CASES:
        result = eval_case(qe, *case_def)
        results.append(result)

        # Print inline
        l2_str = " ".join(f"{k}={v}" for k, v in result["l2_results"].items())
        print(f"\n{result['case_id']} (w={result['weight']}): {result['notes']}")
        print(f"  L1 chunks: {result['chunk_hits']} hits, page_recall={result['page_recall']:.0%}")
        print(f"  L2 entities: {l2_str} → coverage={result['l2_coverage']:.0%}")
        print(f"  time: {result['duration_s']}s")

    # ── Summary ──
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    total_weight = sum(r["weight"] for r in results)
    weighted_page_recall = sum(r["page_recall"] * r["weight"] for r in results) / total_weight
    weighted_l2_coverage = sum(r["l2_coverage"] * r["weight"] for r in results) / total_weight
    total_time = sum(r["duration_s"] for r in results)
    cases_with_hits = sum(1 for r in results if r["has_hits"])

    print(f"Cases evaluated:        {len(results)}")
    print(f"Cases with L1 hits:     {cases_with_hits}/{len(results)}")
    print(f"Weighted page recall:   {weighted_page_recall:.1%}")
    print(f"Weighted L2 coverage:   {weighted_l2_coverage:.1%}")
    print(f"Total evaluation time:  {total_time:.1f}s")

    # Identify weak cases
    weak_l1 = [r for r in results if r["page_recall"] < 0.3]
    weak_l2 = [r for r in results if r["l2_coverage"] < 0.5]

    if weak_l1:
        print(f"\nWeak L1 discovery (page_recall < 30%):")
        for r in weak_l1:
            print(f"  {r['case_id']}: {r['page_recall']:.0%} — {r['notes']}")

    if weak_l2:
        print(f"\nWeak L2 coverage (< 50%):")
        for r in weak_l2:
            missing = [k for k, v in r["l2_results"].items() if v == 0]
            print(f"  {r['case_id']}: {r['l2_coverage']:.0%} — missing: {missing}")

    # Save JSON
    output = {
        "kg_stats": {
            "nodes": store.count_nodes(),
            "edges": store.count_edges(),
            "docs": store.list_docs(),
        },
        "summary": {
            "cases": len(results),
            "cases_with_l1_hits": cases_with_hits,
            "weighted_page_recall": weighted_page_recall,
            "weighted_l2_coverage": weighted_l2_coverage,
            "total_time_s": total_time,
        },
        "results": results,
    }

    out_path = Path(__file__).parent / "toolchain_eval.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nResults saved to: {out_path}")


if __name__ == "__main__":
    main()
