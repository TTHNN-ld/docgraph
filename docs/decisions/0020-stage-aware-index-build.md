# RFC: 分阶段失效与可恢复索引构建

- **ID**: 0020
- **状态**: Accepted
- **作者**: @ld
- **起草日期**: 2026-08-27
- **关联**: RFC 0015、RFC 0019

## 摘要

索引构建区分逐文档权威数据和全局派生数据。文件是否重建由内容、文档元数据、Parser、Chunker、Extractor 与模型语义配置共同决定；Linker 和 Embedding 使用独立状态。构建结果明确分为 `success`、`degraded` 和 `failed`。

## 动机

只比较源文件 hash 无法识别 Parser、质量档、Extractor、模型或元数据变化。原 Linker 又依赖“本轮新增节点数”触发，失败后可能无法重试。可选阶段只写 warning，还会让自动化把部分完成误认为成功。

这些问题的根因是把来源变化、逐文档处理和全局派生计算混成了一个布尔状态。

## 设计

```text
BuildPlan
  → acquire one project build lock
  → SourceSnapshot + document fingerprint
  → Parse / Chunk / Extract
  → atomic L0/L1/L2 replacement per document
  → Linker fingerprint + atomic derived-graph replacement
  → Embedding completeness check
  → atomic manifest + build outcome
```

### 文档状态

- 同一项目的 schema、数据库、向量和 manifest 变更由一个非阻塞进程锁串行化；并发构建直接失败，不等待或交错写入。
- `hash` 表示最近尝试读取的来源；`indexed_hash` 表示当前数据库对应的最后成功来源。
- `build_fingerprint` 包含影响 L0/L1/L2 的实现版本和语义配置，不保存密钥。
- 失败保留上一份可用数据库结果，并记录本次错误；下一次构建继续重试。
- 当前执行粒度仍是文件。阶段指纹用于正确失效，不引入页面级缓存或新的工作流框架。

### 全局派生状态

- Linker 根据图内容、Linker 版本、模型策略和文档优先级计算独立指纹。
- Linker 只替换完全由 Linker 产生的节点和边；与 Extractor 共享证据的图项继续保留。
- LLM IE 的候选扫描和远程调用在写事务前完成；确定性的关系写入与派生图替换在一个短事务中提交，失败回滚到上一次完整结果。
- 审计文件在图事务提交后写入。写入失败会形成 degraded warning，不把已经提交的图伪装成回滚。
- Embedding 比较 provider、model、维度、服务端点、向量后端、完整 ID 集合和内容 hash；语义配置变化会全量刷新，缺失或陈旧项触发恢复。

### 完成条件

- `failed`：任一来源文件未完成 L0/L1 原子提交。
- `degraded`：L0/L1 可用，但 Parser fallback、Extractor、Linker、Embedding 或已启用的模型能力降级。
- `success`：本次需要执行的阶段全部完成。

普通构建允许 `degraded` 返回成功，便于保留 L0/L1；`docgraph build --strict` 将 degraded 作为自动化失败。最近构建和派生阶段状态写入 manifest，并由 `docgraph_documents` 暴露给 Agent。

### 模型与配置

- VLM 必须显式启用并使用独立 provider、model 和凭证，不回退到文本 LLM 配置。
- `budget_per_build_usd` 由文本 LLM 与 VLM 共享。请求在发出前按估算成本原子预留额度，缓存命中不占新额度；实际费用在响应后结算。
- 已启用但当前不可用的 LLM/VLM 将记录 degraded，下一次构建继续尝试，不把降级结果当作永久成功缓存。
- 不保留没有执行效果的配置字段；执行策略默认值属于实现，不转嫁给项目配置。

## 取舍

暂不实现页面级或单 Extractor 增量。文件级原子替换更容易验证，阶段级失效已经解决配置漂移和失败恢复问题。Linker 共享证据仍使用现有单边模型；只有完全由 Linker 拥有的图项才会自动替换，避免误删确定性证据。

## 验证

- 未改文件在配置和实现未变时跳过，Parser/Extractor/模型语义配置变化时重建。
- 显式无效 `--doc` 返回失败。
- Linker 重建删除陈旧派生边，任一 Linker 失败时事务回滚。
- Extractor、Linker 和 Embedding 降级进入 manifest、CLI 与 MCP 状态。
- 向量部分写入、内容变化和模型切换仍能自动恢复。
- 同项目并发构建快速失败；watcher 保留锁冲突期间的变更并稍后合并重试。
