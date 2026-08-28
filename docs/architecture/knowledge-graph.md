# 知识图谱构建

L2 由 Extractor 发现实体和结构边，再由 Linker 补充关系、引用和别名。两者只消费统一 L0/L1，不感知具体 Parser，也不能影响 L0/L1 入库。

## Extractor 契约

```python
class Extractor(Protocol):
    name: str
    kinds: set[NodeKind]
    requires: set[str]

    def extract(self, doc: ParsedDoc, ctx: ExtractContext) -> ExtractResult: ...
```

`requires` 决定拓扑执行顺序。结果中的 node/edge 必须携带真实来源、evidence、derivation 和保守的可信状态。

## 内置 Extractor

| 名称 | 作用 | 默认启用 |
|---|---|---:|
| `section` | 从 TOC/heading 构建章节节点 | 是 |
| `table_entity` | schema-guided 表格实体；确定性 normalizer 优先 | 是 |
| `text_entity` | 按强编号模式抽取 requirement/errata | 否 |
| `figure` | 保存图节点，可选 VLM 语义增强 | 否 |
| `glossary` | 术语、缩写和别名 | 否 |

寄存器、管脚、时序等不再各自维护一套文档专用 extractor；它们统一由 schema registry 和 normalizer 表达。

## 候选到实体

```text
L1 chunk + L0 blocks
  → EntityCandidate(table/text/table_image/page_image/figure)
  → schema routing
  → deterministic normalizer
  → optional LLM/VLM fallback
  → provenance + structural validation
  → candidate or fact
```

每个候选都绑定 chunk、block、page、section 以及可用的 text/table/image。普通表格优先使用 cells；图片和自由文本只在结构证据不足时增强召回。

## Schema Registry

内置 schema 覆盖 register/bitfield、pin、memory map、interrupt、signal、interface、timing、clock/reset、requirement、errata，以及部分设计/物理约束。

每个 schema 声明目标类型、提示词、排除词、适用文档类型和最低置信要求。文档类型只控制默认扫描范围：protocol/subsystem spec 仍启用 register、pin、signal、interface、memory map 和 interrupt，不能因为类型判断而关闭常见硬件实体。

新增表型时按以下顺序选择：

1. 扩充已有 schema 的双语表头或排除规则。
2. 增加通用 normalizer，处理明确列语义。
3. 注册新 schema。
4. 只有跨块、跨表或专门推理无法表达时才新增 extractor。

## 确定性优先

结构化表格是高价值硬证据。当前 normalizer 处理常见的 register/bitfield、pin、timing、memory map、interrupt、signal、interface 和 constraint 表型，并使用排除规则避免一张表被重复解释。

无法可靠恢复列结构时不强行物化。LLM/VLM 输出必须通过 Pydantic 和领域约束校验，默认保留为 candidate；模型置信分数本身不能把结果晋升为 fact。

多来源命中同一实体或同一关系时：

- 合并 source IDs、evidence、aliases 和来源列表。
- 同一 `(src, dst, kind)` 关系合并证据并保留较高置信度，不因执行顺序丢失强证据。
- 保留表格 normalizer 已确认的字段。
- 不允许后写入的图像或自由文本推断覆盖确定性值。

## Figure 与正文

FigureExtractor 总是先保存图的 L0 来源、caption、page 和 bbox。VLM 可补充模块、接口、连接、时钟复位和地址区域，但普通流程图不会强行物化为芯片实体。

TextEntityExtractor 只处理带稳定编号的 requirement/errata 模式，避免把任意正文句子升级为事实。

## Linker 顺序

```text
RelationInferLinker
  → LLMIELinker（启用 LLM 时）
  → XRefLinker
  → EntityResolver
  → FederationLinker
```

| Linker | 当前职责 |
|---|---|
| RelationInfer | 章节/module 归属和 memory-map 包含关系 |
| LLM IE | 受约束的 mapped_to、drives、clocks、resets、implements 等候选边 |
| XRef | 解析 Section/Figure/Table/Chapter 引用，记录 unresolved |
| EntityResolver | 规范化同名实体，保留原节点并建立 `alias_of` |
| FederationLinker | 同项目 register/pin/parameter 的 priority 关系 |

`supersedes` 当前只表达来源优先级，不代表逐字段验证了勘误覆盖。当前稳定边界是同项目多文档，见[多文档关系](./federation.md)。

Linker 根据图内容、实现版本、模型策略、`priority` 和 `chip_model` 独立失效。LLM IE 的读取和远程调用先完成；随后在一个短事务中替换完全由 Linker 产生的图项，任一步失败都会保留上一份完整关系结果。各 Linker 通过分页遍历全量节点和 chunk，不使用静默截断的固定总量上限。当前运行的 unresolved 和实体合并审计采用原子覆盖，不累积重复历史；审计文件写入失败会报告 degraded，但不会撤销已经提交的图。

## 失败、审计与质量

- 单个 Extractor 失败只降低 L2，构建继续保留 L0/L1。
- LLM/VLM 必须有缓存、超时、成本记录和可观察降级。
- `docgraph l2 audit` 在不调用模型的情况下检查候选、schema 命中和物化率。
- `docgraph doctor --strict` 检查来源链、可信状态和强结构约束。
- `docgraph l2 eval` 用版本化 golden set 衡量 precision/recall/F1。

构建结果分为 success、degraded 和 failed。Extractor、Linker 或 Embedding 失败会形成可审计的 degraded；自动化需要完整能力时使用 `docgraph build --strict`。

设计背景见 [RFC 0015](../decisions/0015-semantic-kg-hybrid-extraction.md)和 [RFC 0017](../decisions/0017-l2-candidate-fact-trust-model.md)。第三方扩展见[插件开发](../development/plugins.md)。
