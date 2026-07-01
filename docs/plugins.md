# 插件系统

> 对应 DESIGN.md §14。

## 1. 可插拔的边界

DocGraph 把以下边界全部接口化，社区可贡献新实现：

| 边界 | 接口 | 默认实现 |
|---|---|---|
| Parser | `docgraph.parsers.base.Parser` | `pymupdf`, `mineru`, `marker`, ... |
| Extractor | `docgraph.extractors.base.Extractor` | `section`, `table_entity`, `figure`, `glossary` |
| EmbeddingProvider | `docgraph.embeddings.base.EmbeddingProvider` | `bge_m3`, `openai`, ... |
| GraphStore | `docgraph.graph.store.GraphStore` | `sqlite` |
| LLMProvider | `docgraph.llm.base.LLMProvider` | `anthropic`, `openai`, ... |

## 2. 注册方式：entry points

第三方包通过 `pyproject.toml` 注册：

```toml
[project.entry-points."docgraph.parsers"]
mineru = "docgraph_mineru:MinerUParser"

[project.entry-points."docgraph.extractors"]
my_custom = "acme_docgraph.extractors:MyExtractor"

[project.entry-points."docgraph.embeddings"]
bge_m3 = "docgraph.embeddings.bge:BgeM3Encoder"

[project.entry-points."docgraph.stores"]
sqlite = "docgraph.graph.sqlite_store:SQLiteGraphStore"

[project.entry-points."docgraph.llm"]
anthropic = "docgraph.llm.anthropic_provider:AnthropicProvider"
```

`pip install docgraph-acme-extractor` 即可被自动发现。

## 3. 插件管理 CLI

```bash
docgraph plugins ls                    # 列出所有可用插件
docgraph plugins ls --kind=parser      # 按类型过滤
docgraph plugins enable myextractor    # 在 config 中启用
docgraph plugins disable mineru        # 禁用
docgraph plugins info mineru           # 显示插件元信息
```

## 4. 自定义 Extractor 模板

```python
from docgraph.extractors.base import Extractor, ExtractResult, ExtractContext
from docgraph.graph.schema import Node, NodeKind, Edge, EdgeKind, ParsedDoc

class PowerDomainExtractor(Extractor):
    """抽取电源域信息（power domain）。"""

    name = "power_domain"
    kinds = {NodeKind.MODULE}
    requires = {"section"}

    def extract(self, doc: ParsedDoc, ctx: ExtractContext) -> ExtractResult:
        nodes, edges = [], []
        for page in doc.pages:
            for table in page.tables:
                if self._looks_like_power_table(table):
                    # ... 抽取逻辑
                    pass
        return ExtractResult(nodes=nodes, edges=edges)

    def _looks_like_power_table(self, table) -> bool:
        headers = {c.lower() for c in table.headers}
        return "domain" in headers and "voltage" in headers
```

在自己的包里注册：

```toml
[project.entry-points."docgraph.extractors"]
power_domain = "my_pkg.extractors:PowerDomainExtractor"
```

## 5. 稳定性承诺

- Parser / Extractor / EmbeddingProvider / GraphStore / LLMProvider 接口在 1.0 前以 [layered-architecture.md](./layered-architecture.md) 为准；不保留与当前分层契约冲突的旧接口兼容。
- 内部 helper 不承诺
- 破坏性变更必须 deprecation 2 个 minor 版本

## 6. 插件作者建议

- 在 README 写清楚：依赖什么 LLM、成本估算、典型用例
- 提供 `tests/golden/` 样本
- 用 `model_tier` 抽象，让用户选模型
- 错误用 `docgraph.errors.ExtractError`（带 `evidence`）

## 相关文档

- 接口规范 → [parsers.md](./parsers.md) / [extractors.md](./extractors.md)
- 配置 → [configuration.md](./configuration.md)
