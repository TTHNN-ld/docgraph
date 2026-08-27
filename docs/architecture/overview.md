# 架构总览

DocGraph 是面向芯片规格书的本地知识底座，不是通用文档协作或权限系统。它把不同格式归一为同一证据模型，再派生检索索引和领域实体。

## 核心原则

- 原文证据优先于抽取结果。
- 默认安装可完成常用格式的 L0/L1 构建；模型和重型 PDF 后端按需启用。
- 索引、缓存和 L2 都是可重建派生数据。
- 失败必须可见、可恢复；不得用空结果伪装成功。
- Parser、Extractor、Linker、Store 通过稳定接口扩展，核心流程不为单个后端写特判。

## 构建链路

```text
PDF / DOCX / XLSX / Markdown
  → discover + source/build fingerprint
  → Parser → ParsedDoc / L0 Blocks
  → Chunker → L1 Chunks + FTS
  → Extractor → L2 Nodes/Edges
  → atomic per-document L0/L1/L2 replacement
  → Linker → atomic relations/aliases replacement
  → optional vector index
  → manifest outcome
```

每个文件是一个错误和事务边界。完整构建还会清理已经离开 `docs.include` 的文档；局部构建只替换指定文档。Linker 与向量是拥有独立失效条件的全局派生阶段，状态模型见 [RFC 0020](../decisions/0020-stage-aware-index-build.md)。

## 查询链路

```text
Agent task
  → L1 规模判断
  ├─ 预算内：按稳定顺序提供完整 L1
  └─ 超预算：全文检索，可选融合语义候选定位 L1
       → 按 block_ids 读取 L0 证据
       → 按需附加 L2 实体和关系
```

MCP 不总结或改写 L1，也不把检索候选声明为完整语料。详细契约见[检索架构](./retrieval.md)。

## 模块边界

| 模块 | 负责 | 不负责 |
|---|---|---|
| Parser | 格式读取、版面证据、统一 IR | 领域实体判断 |
| Chunker | 稳定 L1、章节/表/图切块、回溯链 | 改写原文 |
| Extractor | 从统一 IR 发现 L2 候选和事实 | 决定 L0/L1 是否入库 |
| Linker | 关系推断、引用、别名与同项目优先级 | 跨库物理合并 |
| Store | 原子替换、迁移和查询原语 | 业务推理 |
| Query/MCP | 检索、预算、分页、证据获取 | 替 Agent 下结论 |

## 配置与生成物

| 位置 | 内容 |
|---|---|
| `~/.docgraph/config.yaml` | 用户级 provider 和默认偏好 |
| `<project>/docgraph.yaml` | 可选项目差异 |
| `<project>/.docgraph/` | 数据库、manifest、缓存、向量和审计产物 |

默认扫描 `docs/` 与 `spec/` 下的 PDF、DOCX、XLSX/XLSM 和 Markdown。完整字段见[配置指南](../guides/configuration.md)。

## 延伸阅读

- [分层数据契约](./data-layers.md)
- [文档导入](./ingestion.md)
- [知识图谱构建](./knowledge-graph.md)
- [项目边界与下一步](../project/roadmap.md)
