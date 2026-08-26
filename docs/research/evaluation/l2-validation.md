# L2 Spec Validation

> Historical validation snapshot. The referenced PDFs and exact source revision
> are not included in this repository, so the numbers below are not a current or
> independently reproducible product claim. Use `docgraph l2 eval` with a
> versioned golden set for production decisions.

This note records one earlier L2 quality run using local PDFs under `spec/*.pdf`.
It is retained as evaluation history, not as a replacement for a golden benchmark.

## Dataset

- `spec/Arm-Cortex-M7-Processor-Datasheet.pdf`
- `spec/Arm_Cortex-M0+_Processor_Datasheet.pdf`
- `spec/Arm_Cortex-M23_Processor_Datasheet(Arm Cortex-M23 处理器数据表).pdf`
- `spec/Arm_Cortex-R82_Processor_Datasheet.pdf`
- `spec/DDI0275(Arm Cortex-M4 处理器数据表).pdf`
- `spec/DDI0275(ETB11 技术参考手册 r0p1).pdf`
- `spec/LogicTile_Express_for_Cortex-R5_Datasheet.pdf`
- `spec/learn_the_architecture_-_understanding_armv9-a_trace_guide_102856_0100_01_en(学习架构 - 理解 Armv9-A 跟踪指南).pdf`
- `spec/learn_the_architecture_aarch64_memory_management_examples_102416_0201_01_en(学习架构 - AArch64 内存管理示例).pdf`

## Recorded Run

Fast configuration:

- parser: PyMuPDF
- LLM/VLM: disabled
- extractors: `section`, `table_entity`, `figure`, `glossary`

Result:

| Metric | Value |
|---|---:|
| docs | 9 |
| blocks | 3425 |
| chunks | 313 |
| tables | 79 |
| tables with cells | 79 |
| chunks with block ids | 313 |
| L2 nodes | 6 |
| L2 nodes with source blocks/chunks/evidence | 6 |
| L2 structurally valid nodes | 6 |
| build time | about 11 s |

`doctor --strict` passes on this dataset. The produced non-section/non-term L2
nodes are six interface instances from the Cortex-R82 datasheet, with protocol
captured separately, for example `DebugBlock` with `AMBA 4 APB` and `Generic
Interrupt Controller (GIC) Stream interface` with `AMBA 4 AXI4-Stream`.

## Interpretation

L0/L1 pass the production gate on this sample set: tables are preserved with
cells, chunks are addressable, and strict provenance checks pass.

L2 does not yet meet a general production-quality bar for arbitrary PDFs. The
current deterministic fast path is intentionally conservative after tightening
false-positive filters, so it avoids materializing feature-summary rows as
interrupt entities, and it now preserves interface instance names instead of
using protocol names as entities. Recall is still incomplete:

- many table candidates have no matched schema;
- text candidates are detected, but without LLM/VLM they are not materialized
  into structured L2 nodes;
- `l2-audit` reports schema-level materialization rate and warns when a schema
  has matched candidates but no persisted L2 nodes;
- this sample set does not exercise register-heavy or backend constraint-heavy
  tables enough to prove broad recall.

## Required Gate Before Production L2

L2 should be promoted to production only when all of the following are true:

- every enabled schema has a golden set with precision/recall thresholds;
- `l2-audit` reports no high-severity issue and warnings are reviewed by doc
  type;
- schema-specific validators cover all production schemas, not only
  register/bitfield;
- uncertain L2 candidates are stored as reviewable candidates or remain in
  L1/L0, not silently promoted into graph nodes;
- LLM/VLM-assisted extraction has cache, budget, provenance, and deterministic
  post-validation enabled.
