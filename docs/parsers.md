# Parser 层

> 对应 DESIGN.md §7。把任意格式的输入文档归一为统一中间表示 `ParsedDoc`。
> Parser 只负责 L0 高保真版面，不负责抽寄存器/管脚等知识图谱实体。

## 1. 接口

```python
class Parser(Protocol):
    name: str                       # 注册名，e.g. "mineru"
    supports: set[str]              # {".pdf", ".docx", ...}

    def can_parse(self, path: Path) -> bool: ...
    def parse(self, path: Path, ctx: ParseContext) -> ParsedDoc: ...
```

## 2. 统一 IR：`ParsedDoc`

```python
class ParsedDoc(BaseModel):
    doc_id: str
    source_path: str
    pages: list[ParsedPage]
    metadata: DocMetadata           # 标题、版本、作者、日期
    toc: list[TocEntry]
    parser: str
    parser_version: str

class ParsedPage(BaseModel):
    page_no: int
    blocks: list[Block]             # L0 权威入口：段落/标题/表格/图/公式
    quality: PageQuality | None
    rendered_image_path: str | None
```

**关键设计**：所有格式（PDF / DOCX / MD / XLSX）最终归一为 `ParsedDoc`。下游 Extractor 不感知输入格式，也不感知具体 parser（PyMuPDF / MinerU / Docling / Marker）。

`TextBlock` / `ParsedTable` / `ParsedFigure` 只允许作为 parser adapter 内部整理数据的轻量视图；跨模块事实入口是 L0 `blocks`，Extractor 和 L1 chunker 不应依赖这些派生视图。

## 2.1 统一 Block 契约

Parser 后端能力不同，不能假设每个 parser 都能产出相同质量的表格单元格。统一 IR 必须显式表达“当前拿到了什么”，而不是把缺失误写成空表：

```python
class Block(BaseModel):
    id: str
    doc_id: str
    page: int
    kind: BlockKind                 # paragraph / heading / table / figure / ...
    bbox: BBox | None
    text: str | None
    table: TableData | None
    image_path: str | None
    section_path: str | None
    attrs: dict                     # parser/table_source/confidence/...

class TableData(BaseModel):
    headers: list[str]
    rows: list[list[str]]
    html: str | None
    caption: str | None
```

`table_source` 约定：

| 值 | 含义 | 典型来源 | 下游策略 |
|---|---|---|---|
| `cells` | 已有单元格结构 | PyMuPDF `find_tables`、Docling、Excel | 直接转 markdown/LLM schema 抽取 |
| `html` | 有 HTML 表格 | Marker / Docling / 部分 MinerU 配置 | 解析 HTML 后抽取 |
| `text` | 只有表格文本 | OCR 或 markdown 降级 | 文本窗口抽取 |
| `image` | 只有表格裁剪图 | 当前 MinerU 配置 | 表格图片 VLM/OCR/table-recognizer |

所有 parser adapter 必须尽力填充：

- `bbox/page/reading_order`
- `caption`
- `image_path`（表格/图只有图片时尤其关键）
- `attrs.parser`
- `attrs.table_source`（表格块必填）

## 3. 内置 Parser

| Parser | 主要场景 | 优先级 |
|---|---|---|
| `pymupdf` | PDF（轻量、开箱即用、快速预览/兜底） | **P0** 默认内置路径 |
| `mineru` | PDF（中英混排、公式、复杂表格/图） | **P0** 高保真推荐路径 |
| `marker` | PDF（英文为主、速度优先） | P0 备选 |
| `docling` | PDF（双栏、复杂版式） | P1 |
| `vlm` | 扫描版 PDF 单页兜底 | P1 |
| `docx` | Word（python-docx） | P1 |
| `markdown` | Markdown（markdown-it） | P1 |
| `xlsx` | Excel（openpyxl，常用于 pin 表） | P1 |
| `mathpix` | 商用 OCR | P2 |

> **设计要点**：PyMuPDF 作为默认（pip 装即可用），MinerU 作为复杂版面推荐后端（效果更好但安装更重）。用户可在可选项目级 `docgraph.yaml` 中切换。新增 Docling 等 parser 时，只需要写 adapter 归一到 `ParsedDoc/Block`，下游抽取器不应改动。

MinerU 的解析缓存、middle JSON 和导出的图片属于项目生成物，放在 `<project>/.docgraph/cache/`；模型权重属于用户级共享资源，默认放在 `~/.docgraph/mineru-models/`，可用 `DOCGRAPH_MINERU_MODELS_DIR` 覆盖，避免每个项目重复下载模型。

## 4. Parser 选择策略

```yaml
parsers:
  pdf:
    primary: mineru               # 高保真项目可显式开启
    fallback: [pymupdf]           # parser 级失败才降级
    quality: balanced             # fast | balanced | accurate
    per_page_timeout: 60
  docx:
    primary: docx
  xlsx:
    primary: xlsx
  md:
    primary: markdown
```

当前实现分两层：

### 4.1 文件级 parser 选择

1. 按扩展名找 `primary` parser
2. `primary` 初始化/解析失败（异常）→ 按 `fallback` 顺序尝试
3. 如果 primary 能解析，就不会自动切换到 fallback；也就是说**文件级不是多 parser 混用**

`quality` 是面向用户的唯一速度/质量旋钮：

| 档位 | 行为 |
|---|---|
| `fast` | PDF 优先走 PyMuPDF，用于首次导入和快速预览 |
| `balanced` | 按配置 parser 链执行，推荐 MinerU + PyMuPDF fallback |
| `accurate` | 按配置 parser 链执行，保留表格识别等高保真能力 |

日常只需要 `docgraph build`；需要覆盖时使用 `docgraph build --quality fast|balanced|accurate`。

### 4.2 页级质量与 VLM 兜底

PyMuPDFParser 会对每一页做 `PageQuality` 评估：

- `text_chars` / `text_density`：判断扫描页 / 空文本页
- `table_keyword_hits`：Table caption 命中
- `register_keyword_hits`：register / bit assignments / bit description / 寄存器 / 位域
- `pin_keyword_hits`：pin / direction / function / 管脚
- `timing_keyword_hits`：min/typ/max / electrical characteristics / 时序参数
- `figure_caption_hits` + `image_area_ratio`：图重页

若命中：

```text
scan_like_no_text     → 整页渲染 PNG，给 VLM 兜底
register_with_table   → table_entity 可用整页/表格图 VLM 抽寄存器
pin_with_table        → table_entity 可用整页/表格图 VLM 抽管脚
timing_with_table     → table_entity 可用整页/表格图 VLM 抽参数
figure_heavy          → FigureExtractor 可用整页 VLM 描述图
```

触发页会自动渲染为：

```text
.docgraph/cache/<doc_hash>/page_renders/page_XXXX.png
```

然后下游 extractor 根据 `page.quality.needs_vlm` 和 `page.rendered_image_path` 决定是否调用 VLM。这样即使默认仍用 PyMuPDF，也具备**页级智能混合 + VLM 兜底**能力。

### 4.3 Parser 后端归一化

当前推荐链路不是“某个 extractor 绑定某个 parser”，而是：

```text
PyMuPDF / MinerU / Marker / Docling / XLSX
  → parser adapter
  → ParsedDoc + L0 Blocks
  → L1 chunks
  → 通用 Extractor
```

后端差异在 adapter 内部消化：

- PyMuPDF：优先填 `table_source=cells`
- MinerU：当前能稳定提供版面分类、bbox、caption、表格/图片裁剪；若未启用表格识别，则填 `table_source=image`
- MinerU：当前接入 magic-pdf current API；不保留 MinerU 0.x API 分支
- Docling：接入后应优先填 `html/cells`，保留图片作为追溯证据
- XLSX：直接填 `cells`

### 4.4 VLM 调用成本控制

默认 `table_entity` 最多调用 **8 次** VLM 兜底，避免大 PDF 一次性触发几十页视觉调用。可用环境变量覆盖：

```bash
# 全量跑最多 80 个 register-heavy 页面
DOCGRAPH_VLM_PAGE_LIMIT=80 docgraph build --force

# 调试时只跑 2 页
DOCGRAPH_VLM_PAGE_LIMIT=2 docgraph build --force
```

VLM provider 若不支持 vision（例如 DeepSeek 文本模型），第一次失败后会自动 fast-fail 禁用，后续页不再逐页刷错误。

## 5. 缓存

每个文件按 hash 缓存到 `.docgraph/cache/<doc_hash>/`：

```
cache/<doc_hash>/
├── pages.jsonl
├── meta.json
├── tables/
│   ├── table_p12_t0.html
│   └── ...
└── figures/
    ├── fig_p14_f0.png
    └── ...
```

缓存键包含：
- 文件 hash
- parser name
- parser_version（升级 parser 自动失效）

## 6. 写一个新 Parser

```python
from docgraph.parsers.base import Parser, ParsedDoc, ParsedPage
from pathlib import Path

class MyParser(Parser):
    name = "my_parser"
    supports = {".pdf"}

    def can_parse(self, path: Path) -> bool:
        return path.suffix.lower() in self.supports

    def parse(self, path: Path, ctx) -> ParsedDoc:
        # 你的解析逻辑
        return ParsedDoc(
            doc_id=ctx.doc_id,
            source_path=str(path),
            pages=[...],
            metadata=...,
            toc=[...],
            parser=self.name,
            parser_version="0.1",
        )
```

通过 entry points 注册（见 [plugins.md](./plugins.md)）。

## 7. 测试要求

每个 parser 至少要有：
- 单元测试：小规模文档输入 → 期望 ParsedDoc 输出
- 在 `tests/golden/` 放一份代表性 PDF + 期望 JSON（少量节选）
- CI 跑 golden 测试，回归检测

## 相关文档

- 后续阶段 → [extractors.md](./extractors.md)
- 缓存策略 → [incremental.md](./incremental.md)
- 注册自定义 parser → [plugins.md](./plugins.md)
