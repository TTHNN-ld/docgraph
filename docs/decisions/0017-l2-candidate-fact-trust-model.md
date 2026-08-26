# RFC: L2 候选与事实可信状态模型

- **ID**: 0017
- **状态**: Accepted
- **作者**: @ld
- **起草日期**: 2026-07-15
- **关联**: ADR-008（evidence 非空）、ADR-011（三层架构）、ADR-015（混合抽取）

## 摘要

L2 采用“宽松发现候选、严格确认事实”的双层可信模型。Extractor 可以保留字段不完整或来自 LLM/VLM 的结果，但只有来源可回溯、方法可验证且通过约束校验的确定性结果才能标记为 `fact`。未通过事实门禁的结果不会丢弃，而是保留为 `candidate` 并记录结构化 validation issue。

## 动机

单一强约束会降低召回率，尤其无法适应表头变体、跨页表、OCR 噪声和散落在正文中的工程语义；单一宽松图谱又无法区分可直接用于 RTL/DV 生成的硬事实与模型推断。现有 `extraction_confidence`、`source` 和 `status=pending` 语义分散，不能形成统一门禁，也无法让质量审计判断错误晋升。

## 详细设计

每个 L2 node/edge 在 `attrs` 中携带：

```text
l2_status: document_entity | candidate | fact | needs_review | conflict | rejected
derivation:
  method: deterministic | llm_inferred | vlm_inferred | merged | manual
  extractor: string
  confidence: exact | high | medium | low
  verified: bool
validation_issues:
  - code: string
    message: string
    severity: error | warning | info
    field: string | null
```

状态语义：

- `document_entity` 表示 section、figure 等原文组织节点，不宣称领域事实。
- `candidate` 表示可查询、可回溯但尚不能作为权威结论的抽取结果。
- `fact` 表示通过事实门禁的结构化事实。
- `needs_review`、`conflict`、`rejected` 保留校验和多来源合并的处理结果，不静默删除证据。

事实门禁至少要求：

1. derivation 为 `deterministic` 或 `manual`，且 `verified=true`。
2. evidence extractor 非空，并具有有效的 `source_block_ids` 与 `source_chunk_ids`。
3. validation issue 中不存在 error。
4. 强结构实体满足类型约束，例如 bitfield 的 register 引用和位范围合法，memory map 具有地址定位字段。

LLM/VLM 输出无论置信度多高都不能直接成为 fact；它们必须通过独立的确定性校验、人工确认或后续多源验证。确定性表格 normalizer 的合格输出可以直接晋升。其他现有 extractor 和第三方插件输出默认保守归类为 candidate，避免兼容逻辑误造事实。

## 存储与兼容性

可信元数据保存在开放的 `attrs` 中，不增加 SQLite 列，也不改变 GraphStore 接口。读取旧节点时按类型保守补为 `candidate` 或 `document_entity`；重建文档后会持久化完整元数据。质量审计对缺少元数据的旧库给出 warning，对不满足门禁却标记为 fact 的节点给出 error。

## 备选方案

1. **所有抽取结果都使用强 schema 拒绝不完整项**：拒绝。召回损失不可见，无法支持后续复核和补全。
2. **只使用浮点 confidence**：拒绝。模型自报置信度不能表达来源方法、验证状态和失败原因。
3. **为可信状态增加数据库列**：暂不采用。会扩大存储迁移和插件接口影响，当前开放 attrs 已能提供稳定契约。

## 迁移路径

- 新构建的节点和边统一写入可信元数据。
- 旧库可继续读取；doctor 报告旧元数据 warning，不阻断 L0/L1 使用。
- 后续 validator 可逐类扩展事实门禁，并通过 `validation_issues` 保持可解释性。
