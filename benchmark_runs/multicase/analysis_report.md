# PCIe Agent Benchmark Multicase Audit

## Scope

Evaluated four representative cases with Claude Code:

- Case 1: address translation and address space
- Case 2: module boundary and interface checklist
- Case 8: USP interrupt/status RAL input
- Case 11: clock/reset verification plan

Baseline runs were forced to inspect the source PDFs and were forbidden from using DocGraph or benchmark hints. DocGraph runs used the local DocGraph MCP server and were prompted to avoid broad exploration.

## Cost And Interaction Summary

| Case | Mode | Turns | Tools | DocGraph Calls | API s | Input Tokens | Output Tokens | Cache Read | Cost USD | Result Chars |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Case 1 | Baseline | 18 | 17 | 0 | 153.2 | 45,393 | 10,466 | 171,776 | 0.5745 | 20,061 |
| Case 1 | DocGraph | 23 | 22 | 20 | 156.9 | 73,527 | 9,657 | 397,696 | 0.8079 | 18,633 |
| Case 2 | Baseline | 14 | 13 | 0 | 135.4 | 33,778 | 10,826 | 108,032 | 0.4936 | 20,004 |
| Case 2 | DocGraph | 44 | n/a | ~28 self-reported | 175.5 | 111,492 | 11,540 | 519,808 | 1.1059 | 16,473 |
| Case 8 | Baseline | 32 | 25 | 0 | 242.0 | 41,656 | 11,687 | 406,912 | 0.7039 | 16,079 |
| Case 8 | DocGraph | 24 | 23 | 19 | 175.9 | 47,690 | 12,657 | 319,104 | 0.7144 | 15,416 |
| Case 11 | Baseline | 41 | 22 | 0 | 220.3 | 25,759 | 12,131 | 161,024 | 0.5126 | 20,287 |
| Case 11 | DocGraph | 26 | 25 | 21 | 131.9 | 58,070 | 8,940 | 268,160 | 0.6479 | 12,192 |

## Findings

1. DocGraph improves retrieval discipline, but the current MCP interface is too low-level for Claude Code.
   The agent repeatedly chains `context/search/search_chunks/fetch/sources`, often exceeding the intended call budget. Case 1 used 20 DocGraph calls and Case 11 used 21.

2. `docgraph_context` returns too much context for task execution.
   It helps quality, but it inflates input tokens and cache reads. This is visible in all DocGraph runs, especially Case 2.

3. Register-oriented retrieval is not reliable enough.
   In Case 8, `docgraph_search("USP", kind="register")` and `docgraph_register("USP")` did not return the expected object. Claude Code then fell back to chunk/table searching, using 19 DocGraph calls plus file reads.

4. Clock/reset entity coverage is insufficient for clock/reset tasks.
   Case 11 still required repeated chunk searches for `clock`, `reset`, `PERST`, `CRG`, `PLL`, `GFM`, `DIV`, and `MUX`. The benchmark document already warns that clock coverage is below 50%.

5. Baseline is strong on small PDFs because full PDF extraction is cheap enough.
   For 42-page PDFs, Claude Code can often read broad page ranges and produce a good answer. DocGraph efficiency benefits will be clearer on larger corpora or when baseline page budget is constrained.

6. Prompt-level call limits are not enforceable.
   Even when instructed to use at most 8-10 DocGraph calls, Claude Code exceeded the limit when the first tools did not return an answer-ready evidence package.

## Benchmark Design Issues

1. The document path in the benchmark says `case/...pdf`, but this repository uses `spec/...pdf`.

2. Case 8 is likely mis-specified.
   The benchmark expects `USP` register/bitfield evidence on Spec p.25, including bit numbers, access, reset, and RAL strategy. The source PDF p.25 contains the `irq_src` interrupt source table with signal name, width, and description. It does not define a complete register with offset/access/reset/bit positions. The MSI-X `INT_NUM` fields on p.27 do have access/reset/field information and are more suitable for RAL/UVM sequence evaluation.

3. Several expected signal names do not appear verbatim in the PDF.
   `mstr_clk`, `slv_clk`, and `dbi_clk` were not found by exact text search in `PCIE Subsystem Spec_v3.21.pdf`, while `core_clk` appears. Case scoring should allow spec-local names such as `mstr_aclk` and `slv_aclk`, and should not require absent aliases.

4. Figure identifiers in the benchmark mix synthetic and source-native names.
   Examples such as `figure_p21` do not appear in the PDF text, while `Figure 4-2` does. Scoring should use source-native names plus page numbers, with synthetic IDs treated as optional DocGraph-internal references.

5. Baseline rules do not specify a page budget.
   Without a page budget, Claude Code reasonably reads the full 42-page source. That makes small-document cases measure answer generation more than retrieval efficiency.

## Unified Optimization Plan

1. Add a task-level evidence package MCP tool.

   Proposed tool:

   ```text
   docgraph_evidence_pack(task, doc_filter, focus, limits)
   ```

   It should return an answer-ready package:

   - relevant sections with pages
   - relevant chunks and original L0 blocks
   - table rows when tables are central to the task
   - figure captions and VLM summaries
   - normalized entities grouped by module/interface/clock/reset/register/bitfield/requirement
   - coverage warnings when target entity coverage is weak

2. Add specialized evidence-pack routes.

   - `boundary_interface_pack` for Cases 2, 3, 15
   - `address_translation_pack` for Cases 1, 4, 5, 6
   - `interrupt_register_pack` for Cases 7, 8, 9, 10, 16
   - `clock_reset_pack` for Cases 11, 13, 14

3. Improve retrieval ranking.

   - Deduplicate repeated sections and chunks.
   - Cap snippets by token budget.
   - Prefer source-native section/table/figure matches over broad semantic hits.
   - Penalize TOC/list pages unless the task asks for navigation.

4. Improve schema and aliases.

   - Add aliases for `mstr_aclk`, `slv_aclk`, `cfg_clk`, `core_clk`, `pipe_rx_clk`, `PERST#`, `power_on_rst_n`.
   - Distinguish interrupt source signals from register/bitfield nodes.
   - Add a first-class `interrupt_source` or `status_signal` type instead of forcing `irq_src` rows into register semantics.
   - Ensure register nodes carry offset/access/reset only when the source table actually provides those fields.

5. Add MCP-side call-budget support.

   The MCP layer should expose fewer high-level tools and return complete evidence in one or two calls. Prompt-only call limits are insufficient.

6. Revise the benchmark.

   - Fix the input path from `case/` to `spec/` or define a stable fixture path.
   - Split Case 8 into:
     - Case 8A: interrupt source/status signal modeling from `irq_src` table.
     - Case 8B: RAL input for registers that actually include fields/access/reset.
   - Keep Case 9 for `INT_NUM` / MSI-X doorbell fields.
   - Add page budgets to Baseline modes if retrieval efficiency is the target.
   - Score “correctly reports missing register semantics” as valid behavior when the PDF lacks register fields.

## Expected Outcome After Optimization

| Metric | Current DocGraph | Target |
|---|---:|---:|
| DocGraph calls per case | 19-28 | 1-3 |
| Turns per case | 23-44 | 6-12 |
| Input tokens | 47k-111k | 20k-45k |
| Cache read tokens | 268k-520k | below 150k |
| Cost | often above Baseline | near or below Baseline |
| Quality | generally usable | usable with stronger evidence and fewer omissions |
