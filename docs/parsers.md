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

**关键设计**：所有格式（PDF / DOCX / MD / XLSX）最终归一为 `ParsedDoc`。下游 Extractor 不感知输入格式，也不感知具体 parser（PyMuPDF / Docling / MinerU）。

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
| `html` | 有 HTML 表格 | Docling / 部分 MinerU 配置 / 可选 Marker | 解析 HTML 后抽取 |
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
| `pymupdf` | PDF（轻量、开箱即用、预检、快速预览、兜底） | **P0** 基础设施 |
| `docling` | PDF（Word 导出、tagged PDF、born-digital、表格清晰） | **P0** 默认结构 parser |
| `mineru` | PDF（图片密集、扫描/OCR、复杂版面、图片资产保留） | **P0** 高保真/OCR parser |
| `vlm` | 扫描版 PDF 单页兜底 | P1 |
| `docx` | Word（python-docx） | P1 |
| `markdown` | Markdown（markdown-it） | P1 |
| `xlsx` | Excel（openpyxl，常用于 pin 表） | P1 |
| `marker` | PDF（阅读型 Markdown、章节体验） | 可选插件/评测后端 |
| `mathpix` | 商用 OCR | P2 |

> **设计要点**：PDF 默认使用 `auto` 路由。PyMuPDF 先做轻量预检和兜底；Docling 处理可复制文本质量好的 born-digital / Word 导出 PDF；MinerU 处理扫描、图片密集和 OCR 场景。Marker、Unstructured、MarkItDown 等不进入常用主链路，可作为离线评测或插件后端。

MinerU 的解析缓存、middle JSON 和导出的图片属于项目生成物，放在
`<project>/.docgraph/cache/`。使用 `vlm-http-client` / `hybrid-http-client` 时，
VLM 权重只存在于独立模型服务器；DocGraph 所在机器负责文档编排、结果下载和
`ParsedDoc/L0 Blocks` 归一化。

## 4. Parser 选择策略

```yaml
parsers:
  pdf:
    primary: auto                 # auto | docling | mineru | pymupdf
    fallback: []                  # 显式 parser 缺失/不可用时才降级
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

1. PDF 先由 PyMuPDF 生成 `PdfProfile`：页数、文本层、图片密度、表格候选、寄存器/位域关键词密度、tagged/Word 导出特征等。
2. `primary: auto` 根据 profile 和 `quality` 选择 `docling` / `mineru` / `pymupdf`。
3. 显式指定 `primary: docling|mineru|pymupdf` 时，按用户配置优先。
4. parser 未安装时，交互构建按策略询问是否安装；非交互构建默认继续降级。
5. parser 初始化、模型下载或解析失败时，按自动链路或 `fallback` 顺序降级；PDF
   最后补入 PyMuPDF。扫描件若没有抽出足够文本，不会被当成成功结果。
6. 文件级仍然只选择一个主 parser；后续可在 block 级补强表格或图片，但不会默认整篇多 parser 混跑。

`quality` 是面向用户的唯一速度/质量旋钮：

| 档位 | 行为 |
|---|---|
| `fast` | PDF 优先走 PyMuPDF，用于首次导入和快速预览 |
| `balanced` | 自动路由：普通 born-digital / tagged PDF 优先 Docling；扫描、图片密集或寄存器/位域表格密集 PDF 优先 MinerU |
| `accurate` | 自动路由仍优先匹配文档类型；无法判断时偏向 MinerU 的高保真/OCR 路径 |

日常只需要 `docgraph build`；需要覆盖时使用 `docgraph build --quality fast|balanced|accurate`。

依赖准备与故障策略：

```bash
docgraph setup                            # 检查 parser、LLM/VLM、embedding 和回退状态
docgraph setup parsers                    # 安装推荐项：Docling + Office/Markdown
docgraph setup parsers --parser mineru   # 安装 MinerU 3.x 编排客户端
docgraph build --install-missing          # 授权本次构建补装缺失 extra
docgraph build --strict-parsers           # 禁止回退，适合质量门禁
```

`setup` 只检查并给出建议，不修改环境；`setup parsers` 才会安装白名单内的
DocGraph extras。普通用户日常只需 `docgraph build`，缺失重型 parser 时自动
回退并把原因写入 manifest。

每个尝试及失败原因写入 manifest 的 `parser_attempts`；发生回退时同时写入
`requested_parser`、实际 `parser`、`quality_status=degraded` 和 `fallback_reason`。

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
PyMuPDF / Docling / MinerU / XLSX
  → parser adapter
  → ParsedDoc + L0 Blocks
  → L1 chunks
  → 通用 Extractor
```

后端差异在 adapter 内部消化：

- PyMuPDF：优先填 `table_source=cells`
- Docling：优先填 `table_source=cells`，保留 HTML、bbox、图片块和图题作为追溯证据
- MinerU：使用 3.x `middle.json` 提供版面分类、bbox、caption、表格/图片裁剪；若未取得表格结构，则填 `table_source=image`
- MinerU：Parser adapter 始终在 DocGraph 侧完成 L0 归一化；本地 engine 与远程 http-client backend 不改变下游契约
- XLSX：直接填 `cells`

### 4.4 MinerU 远程模型服务

MinerU 把文档解析编排和 VLM 推理解耦。DocGraph 接入的是 OpenAI-compatible
模型服务，而不是文档级 `mineru-api`：

```text
DocGraph → MinerU 3.x orchestration client → OpenAI-compatible model server
                                      └────→ middle.json → ParsedDoc / L0
```

模型服务可由 MinerU 自带的 vLLM 包装器启动：

```bash
mineru-openai-server --engine vllm --host 0.0.0.0 --port 30000
```

也可以使用能够正确加载目标 MinerU VLM、实现兼容协议的 vLLM 或 SGLang
服务。推荐在用户级 `~/.docgraph/config.yaml` 配置服务地址和凭证：

```yaml
parsers:
  pdf:
    mineru:
      backend: vlm-http-client       # 或 hybrid-http-client
      model_server_url: http://gpu-server:30000
      model: MinerU2.5-2509-1.2B
      api_key_env: MINERU_VL_API_KEY
      timeout_seconds: 3600
      formula: true
      table: true
      image_analysis: true
```

选择 `primary: mineru` 或由 `auto` 路由到 MinerU 后，adapter 调用 MinerU 3.x
CLI。`model_server_url` 只用于模型推理；它对应 MinerU CLI 的 `--url`，不是
文档级 `--api-url`。以下环境变量可作为配置兼容路径：

```bash
MINERU_MODEL_SERVER_URL=http://gpu-server:30000
MINERU_VL_MODEL_NAME=MinerU2.5-2509-1.2B
MINERU_VL_API_KEY=...
```

`vlm-http-client` 不要求 DocGraph 机器安装 PyTorch 或保存 VLM 权重。
`hybrid-http-client` 仍会在客户端执行 MinerU 的小模型 pipeline，因此需要安装
相应本地依赖。`pipeline`、`vlm-engine` 和 `hybrid-engine` 是本地执行模式，需按
MinerU 官方安装说明补齐 `pipeline` / `vlm` 依赖。

远程调用失败、超时或未产生 `middle.json` 时，错误进入现有
`parser_attempts`，并按 `runtime.parser_failure` 和 PDF fallback 链处理，不会写入
不完整的 L0/L1。

### 4.5 VLM 调用成本控制

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
