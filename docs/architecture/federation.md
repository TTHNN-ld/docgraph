# 多文档关系

## 同项目多文档

通过 `docs.metadata` 为 datasheet、TRM、errata 等声明 type、version、priority 和 chip model。Linker 当前只对同名 register、pin 和 parameter 按 priority 生成 alias/supersedes 关系。

`chip_model` 是同名实体合并的硬边界：EntityResolver 和 FederationLinker 都只在同一显式实例内生成 alias/supersedes；未配置时退回 document ID 的 family 前缀。`supersedes` 目前只表达来源提示，不保证逐字段验证了勘误语义。

## 构建边界

完整 `docgraph build` 负责项目文档集的删除对账；`--doc` 只重建指定文档。跨项目联合查询目前不属于稳定能力；需要统一检索的文档应放在同一项目中，避免文档 ID、配置、向量模型和游标快照之间产生不明确的边界。

配置示例见[配置指南](../guides/configuration.md#文档元数据)，同项目连接规则见[知识图谱构建](./knowledge-graph.md)。
