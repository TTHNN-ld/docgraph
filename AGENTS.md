# AGENTS.md

本文件约定在 DocGraph 仓库中修改代码时需要遵守的边界。它只保留长期有效的工程规则；当前进度和短期任务以 [roadmap](./docs/roadmap.md) 为准。

## 改代码前

先阅读与改动相关的设计文档：

1. [分层架构](./docs/layered-architecture.md)：Parser、Block、Chunk、Extractor、Store 和查询链路的数据契约。
2. [DESIGN.md](./DESIGN.md)：设计文档索引。
3. [docs/](./docs/)：各模块的专项设计。
4. [roadmap](./docs/roadmap.md) 与 [RFC](./docs/rfcs/)：当前重点和已确认的设计决策。

如果实现与设计冲突，不要直接改文档来迁就现有代码。属于实现偏差的，修正代码；确实需要改变架构的，先提交 RFC 并更新设计，再实现。

## 分层契约

- L0 保留可重建原文语义的版面信息。表格保留单元格或等价证据，图、公式、页码、坐标、阅读顺序和章节关系不得无故丢失。
- L1 中的 chunk 必须有稳定 ID，并通过 `block_ids` 回溯到 L0。索引是派生数据，必须能够重建。
- L2 是可选增强。抽取或 LLM/VLM 失败不能影响 L0/L1 入库，也不能成为访问信息的唯一路径。
- L2 节点必须携带 `source_block_ids`、`source_chunk_ids` 和非空 `evidence`。结构化表格证据优先于 VLM 或自由文本推断；多来源命中同一实体时合并证据，不覆盖已有的确定性字段。
- 查询默认走“L1 定位 → L0 取证 → L2 加速”，不把读全文作为常规路径。

## 实现约定

- 支持 Python 3.11+；跨模块数据结构使用 Pydantic v2。
- 新的实体类型优先注册到 schema registry，不为单一文档或表格新建专用正则 extractor。
- Parser、Extractor、Linker 和存储后端通过现有接口或 entry point 扩展，避免在核心流程中增加后端特判。
- Docling、MinerU、Marker、LLM、VLM 和 Web 等重依赖放在 `optional-dependencies` 中，使用时再 import。核心安装必须保持可用。
- 密钥和 provider 配置通过 `.env` 与 `autoload_env` 加载。VLM 使用独立的 `VLM_*` 配置，不默认复用文本 LLM 凭证。
- LLM/VLM 调用需要缓存、成本记录、超时与可观测的降级路径。
- 单文档重建必须原子替换 L0/L1/L2；完整构建需清理已删除文档的图数据、manifest 和向量。任一输入文件失败时，CLI 不得返回成功退出码。
- migration 失败必须中止升级并保留可恢复状态，不得吞异常或提前写入新版本号。

## 改动边界

- 保留工作区中与当前任务无关的改动。不借机重写、回滚或格式化无关文件。
- 修复 bug 时补充能够复现问题的测试。避免为通过测试而降低数据完整性或静默吞掉失败。
- 不提交 `.env`、密钥、本地数据库、模型权重或 `.docgraph/` 下的项目生成物。
- 注释说明约束和原因，不复述代码。正式文档以读者视角陈述稳定事实，不保留对话过程、临时任务划分或阶段性提示语。

## 验证与交付

提交前至少运行：

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check docgraph/
git diff --check
```

改动 Parser、Chunk、Extractor、Store 或构建流程时，再用代表性文档跑一次 `docgraph build`，并执行 `docgraph doctor --strict`。

提交或 PR 说明应包含：改动原因、验证结果，以及涉及架构时所遵循或修订的设计条款。
