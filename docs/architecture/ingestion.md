# 文档导入

导入链路负责发现文件、选择 Parser、生成统一 L0、构建 L1，并维护增量状态。领域实体抽取不属于 Parser。

## 格式与默认能力

| 格式 | 默认 Parser | 核心安装 | 主要保留内容 |
|---|---|---:|---|
| PDF | `auto`，PyMuPDF 兜底 | 是 | 页码、文本、坐标；可用时保留表格与图片 |
| DOCX | `docx` | 是 | 标题、段落、列表、表格 |
| XLSX/XLSM | `xlsx` | 是 | sheet、行列与单元格 |
| MD/Markdown | `markdown` | 是 | 标题、段落、列表、代码和表格语义 |

轻量 DOCX parser 不还原 Word 分页、浮动布局和嵌入图片；XLSX parser 不还原样式、图表和打印版面。这些是显式能力边界，不应由下游推断补齐。

## PDF 后端

| 后端 | 安装 | 适用场景 | 代价或边界 |
|---|---|---|---|
| PyMuPDF | 核心 | 文本型 PDF、快速构建 | 扫描页和复杂表格可能降级 |
| Docling | `--extra docling` | 复杂布局、表格和图片 | 重依赖，首次使用可能下载模型 |
| MinerU | `--extra mineru` | OCR、公式和高保真结构 | 重依赖；支持独立远程推理 |
| Marker | `--extra marker` | 结构化 PDF 转换 | 与 MinerU 的 Pillow 约束互斥 |

`auto` 先分析页数、文本密度、扫描迹象和表格密度，再按 Docling、MinerU、PyMuPDF 组成的候选链尝试可用后端。Marker 不参与自动路由，只在显式配置为 `primary` 时使用。后端不可用或质量检查失败时按配置 fallback；最终成功结果和每次尝试都写入 manifest。`--strict-parsers` 可以禁止静默降级。

MinerU 的 `vlm-http-client`/`hybrid-http-client` 只把模型推理放到远端；DocGraph 仍负责文件级编排、缓存和 `middle.json → ParsedDoc` 归一化。配置示例见[配置指南](../guides/configuration.md#mineru-远程推理)。

## 统一 Parser 契约

```python
class Parser(Protocol):
    name: str
    supports: set[str]

    def can_parse(self, path: Path) -> bool: ...
    def parse(self, path: Path, ctx: ParseContext) -> ParsedDoc: ...
```

跨模块只消费 `ParsedDoc.pages[].blocks`。Parser 必须：

- 生成稳定 block ID、reading order 和真实来源信息。
- 表格优先提供 cells；只有后端确实无法取得时才退化为 HTML、图片或文本。
- 保留实际 parser 名称和版本，暴露缺失依赖与解析错误。
- 将重依赖延迟到实际调用时 import。
- 不返回“无 blocks 的成功结果”掩盖失败。

L0/L1 的字段与回溯要求见[分层数据契约](./data-layers.md)。

## 构建和增量语义

```text
discover files
  → compare source hash + parser identity with manifest
  → skip unchanged success records
  → parse/chunk/extract changed documents
  → atomically replace each successful document
  → refresh linker/vector derivatives
  → reconcile deleted documents on full build
```

- `docgraph build` 构建变化文件并执行完整文档集删除对账。
- `docgraph build --doc <path>` 只重建一个已被 include 命中的文件，不推断其他文件已删除。
- `docgraph build --force` 忽略 manifest 的跳过判断。
- 任一输入文件失败时命令最终返回非零；已成功文件保留。
- 当前增量单位是文件，不是页面、chunk 或 extractor stage。

## Manifest、缓存与 Watch

`.docgraph/manifest.json` 记录来源路径、hash、mtime、size、doc ID、请求/实际 parser、fallback、状态、错误和阶段统计。它是审计与跳过账本，不替代数据库。

| 派生数据 | 默认位置 | 失效依据 |
|---|---|---|
| Parser 中间产物 | `.docgraph/cache/<source-hash>/` | 内容和 parser 行为 |
| LLM/VLM 响应 | `.docgraph/cache/llm/`、`vlm/` | 输入、模型、参数和 prompt 版本 |
| 向量 | `.docgraph/vectors.db` 或 `vectors.lance/` | 内容、provider 和模型 |

缓存都必须可重建，不能充当 L0 权威存储。

`docgraph admin watch` 复用 `docs.include/exclude`，监听所有默认支持格式的新增、修改和删除。删除或重命名会触发完整对账；变化文件串行构建，避免模型调用风暴。

## 新增 Parser

只有新格式或新解析后端才需要 Parser。通过 `docgraph.parsers` entry point 注册，避免修改核心路由。测试至少覆盖：

- 真实最小输入、空文件、损坏文件和缺失依赖。
- 表格证据、reading order、稳定 ID 和来源链。
- fallback、重复构建和失败恢复。
- 代表性文档的 `docgraph build` 与 `docgraph doctor --strict`。

注册方式见[插件开发](../development/plugins.md)。后端选型背景保存在[研究快照](../research/parser-backends.md)，不作为当前行为定义。
