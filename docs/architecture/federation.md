# 多文档与跨项目联邦

同项目多文档会共同构建关系；跨项目挂接只是只读联合查询，两者不能混为一谈。

## 同项目多文档

通过 `docs.metadata` 为 datasheet、TRM、errata 等声明 type、version、priority 和 chip model。Linker 当前只对同名 register、pin 和 parameter 按 priority 生成 alias/supersedes 关系。

`chip_model` 是同名实体合并的硬边界：EntityResolver 和 FederationLinker 都只在同一显式实例内生成 alias/supersedes；未配置时退回 document ID 的 family 前缀。`supersedes` 目前只表达来源提示，不保证逐字段验证了勘误语义。

## 跨项目只读挂接

```bash
docgraph admin federate add ../another-chip --name chip-b
docgraph admin federate ls
docgraph admin federate rm chip-b
```

目标项目必须已有 `.docgraph/graph.db`。挂接记录保存在当前项目的 `.docgraph/federations.json`。

联合查询行为：

- 构建和写入只影响当前项目。
- search 和统计合并本地/远端结果并按 ID 去重。
- node lookup 先本地、后远端。
- neighbors 返回包含目标节点的单个 store 结果。
- 不复制远端数据库，也不自动创建跨数据库关系。

## 构建边界

完整 `docgraph build` 负责本项目文档集的删除对账；`--doc` 只重建指定文档。远端项目的更新和迁移必须在远端项目中执行。

配置示例见[配置指南](../guides/configuration.md#文档元数据)，同项目连接规则见[知识图谱构建](./knowledge-graph.md)。
