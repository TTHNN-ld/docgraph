# Contributing to DocGraph

DocGraph 使用 Apache 2.0 许可证。提交贡献即表示同意以该许可证发布相关改动。

## 开发环境

```bash
git clone https://github.com/TTHNN-ld/docgraph.git
cd docgraph
uv sync --group dev
```

该命令包含核心依赖、pytest/ruff/mypy 等开发工具，以及 Web 测试依赖；不包含 Docling、MinerU、Marker、LLM 等其他可选能力。需要联合测试时追加相应的 `--extra`。

支持 Python 3.11+。跨模块模型使用 Pydantic v2；CLI 使用 Typer；默认图存储和全文索引使用 SQLite。

## 修改前

1. 阅读 [设计入口](./DESIGN.md)和与改动相关的专题文档。
2. Parser、Block、Chunk、Extractor、Store 或查询链路改动必须符合[分层数据契约](./docs/architecture/data-layers.md)。
3. 重大 API、schema 或跨模块架构变更先从 [RFC 模板](./docs/decisions/0000-template.md)起草方案。
4. 保留工作区中与当前任务无关的改动，不提交 `.env`、密钥、本地数据库、模型或 `.docgraph/` 生成物。

实现与文档不一致时，先判断是实现偏差还是设计变化。实现偏差修代码；确需改变架构时先更新 RFC 和稳定设计，再实现。

## 分层约束

- L0 保留可重建原文语义的证据；表格不能静默退化为空结构。
- L1 chunk 必须有稳定 ID，并通过 `block_ids` 回到 L0。
- L2 是可选增强，失败不能阻断 L0/L1。
- L2 节点必须有 `source_block_ids`、`source_chunk_ids` 和非空 evidence。
- 单文档替换必须原子；完整构建必须清理已删除来源。
- migration 失败必须中止并保留可恢复状态。

## 测试

提交前至少运行：

```bash
uv run pytest tests/ -q
uv run ruff check docgraph/
uv run mypy docgraph/
git diff --check
```

修改 Parser、Chunk、Extractor、Store 或构建流程时，还要：

```bash
uv run docgraph build
uv run docgraph doctor --strict
```

测试应覆盖正常路径、同类异常、恢复路径和真实执行顺序。修 bug 时补充能复现根因的测试，不通过降低数据完整性或吞异常来换取绿灯。

仓库 CI 当前覆盖 Ubuntu/macOS 与 Python 3.11–3.13，运行 pytest、Ruff、mypy 和 CLI smoke；实际定义以 [`.github/workflows/ci.yml`](./.github/workflows/ci.yml) 为准。

## 扩展 Parser 或 Extractor

优先使用现有接口和 Python entry points：

```toml
[project.entry-points."docgraph.parsers"]
my_parser = "my_package.parser:MyParser"

[project.entry-points."docgraph.extractors"]
my_extractor = "my_package.extractor:MyExtractor"
```

新的实体类型优先注册到 schema registry；只有通用 schema 无法表达任务时才增加专用 extractor。重型依赖放到 optional dependency，并在实际使用时 import。

详细边界见[插件开发](./docs/development/plugins.md)、[文档导入](./docs/architecture/ingestion.md)和[知识图谱构建](./docs/architecture/knowledge-graph.md)。

## PR 说明

PR 至少说明：

- 为什么修改以及解决的根因。
- 用户可见行为和兼容性影响。
- 运行过的测试及结果。
- 涉及架构时遵循或修订的设计条款/RFC。

推荐使用小而聚焦的提交和 Conventional Commits 风格，但不要为了格式化而改动无关文件。
