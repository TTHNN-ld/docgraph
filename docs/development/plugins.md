# 插件开发

DocGraph 通过 Python entry points 发现 Parser、Extractor、Embedding 和部分后端。插件必须遵守 L0/L1/L2 契约，不应依赖内部 helper 的稳定性。

## 扩展点

| Entry point group | 状态 | 用途 |
|---|---|---|
| `docgraph.parsers` | 可执行 | Parser class |
| `docgraph.extractors` | 可执行 | Extractor class |
| `docgraph.embeddings` | 可执行 | Embedding provider class |
| `docgraph.stores` | 发现/展示 | 主构建链仍需核对 factory 支持 |
| `docgraph.llm` | 发现/展示 | Provider factory 仍有内置路由 |

发现 entry point 不等于后端已经完整接入执行路径。

## 包结构

```text
docgraph-power-domain/
├── pyproject.toml
├── src/docgraph_power_domain/extractor.py
└── tests/test_extractor.py
```

```toml
[project]
name = "docgraph-power-domain"
version = "0.1.0"
dependencies = ["docgraph-core"]

[project.entry-points."docgraph.extractors"]
power_domain = "docgraph_power_domain.extractor:PowerDomainExtractor"
```

使用 uv 管理插件项目并安装到当前环境：

```bash
uv sync
uv run docgraph admin plugins ls
uv run docgraph admin plugins info power_domain
```

## Parser 要求

Parser 提供 `name`、`supports`、`can_parse()` 和 `parse()`，输出统一 `ParsedDoc`：

- 表格提供 cells、HTML 或图片等真实证据并标记来源类型。
- block ID、页码、reading order 和来源尽量稳定。
- 缺失依赖和解析失败抛出可观察错误。
- 重依赖延迟 import，不增加核心启动成本。

完整契约见[文档导入](../architecture/ingestion.md)。

## Extractor 要求

新增实体表型优先扩展 schema registry；只有跨块、跨表或专门推理无法表达时才新增 extractor。

```python
from docgraph.extractors.base import ExtractContext
from docgraph.graph.schema import ExtractResult, NodeKind, ParsedDoc


class PowerDomainExtractor:
    name = "power_domain"
    kinds = {NodeKind.POWER_DOMAIN}
    requires = {"section"}

    def extract(self, doc: ParsedDoc, ctx: ExtractContext) -> ExtractResult:
        # 读取 page.blocks；返回带 source IDs、evidence 和可信状态的结果。
        return ExtractResult()
```

启用：

```yaml
extractors:
  enabled: [section, table_entity, power_domain]
```

`NodeKind` 和 `EdgeKind` 当前是固定 enum，不能只通过 entry point 动态扩展枚举。节点精确字段以 [`docgraph/graph/schema.py`](../../docgraph/graph/schema.py) 为准。

## 测试与兼容性

插件至少覆盖：

- 正常样例、无匹配输入、空/损坏输入和缺失依赖。
- 重复构建不产生重复节点或边。
- source IDs 和 evidence 指向真实 L0/L1。
- 模型超时、无效输出和预算耗尽只降级 L2。
- 失败路径可观察且可恢复。

稳定边界以[分层数据契约](../architecture/data-layers.md)和本页列出的 entry point 为准；内部 constructor/helper 不属于兼容承诺。插件还必须声明模型下载、外部服务、成本和数据外发行为。
