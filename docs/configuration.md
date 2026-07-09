# 配置参考

> 对应 DESIGN.md §15。DocGraph 配置分为用户级和项目级：用户级在
> `~/.docgraph/`。项目级 `docgraph.yaml` 是可选覆盖文件；没有它时使用内置
> 默认项目配置。项目内 `.docgraph/` 是纯生成目录，只保存图谱数据库、缓存、
> manifest 和日志。

## 配置加载顺序

DocGraph 按顺序合并配置，后者覆盖前者：

1. 内置默认值
2. `~/.docgraph/config.yaml`：用户级模型、embedding、VLM、成本偏好
3. `<project>/docgraph.yaml`：可选，项目级文档范围、芯片 family、parser/extractor 策略

API key、base URL 和模型名建议直接写在 `~/.docgraph/config.yaml`。环境变量和
`.env` 仍保留为兼容路径，按“已存在环境变量优先”的方式加载：

1. shell 已设置的环境变量
2. `~/.docgraph/.env.local`
3. `~/.docgraph/.env`
4. 项目根 `.env.local`
5. 项目根 `.env`

## 项目级覆盖：`docgraph.yaml`（可选）

普通项目可以不创建 `docgraph.yaml`。默认会扫描 `docs/**/*.pdf` 和
`spec/**/*.pdf`，PDF 使用自动路由：PyMuPDF 先做轻量预检，born-digital /
Word 导出 PDF 优先 Docling，扫描或图片密集 PDF 优先 MinerU，最后由
PyMuPDF 兜底。其他行为使用内置 parser/extractor/storage 默认值。

需要覆盖项目行为时再创建：

```yaml
project:
  name: stm32f407-spec
  family: stm32f407                # 联邦关键
  description: STM32F407 系列文档集

docs:
  include: ["docs/**/*.pdf", "docs/**/*.docx", "docs/**/*.md", "docs/**/*.xlsx"]
  exclude: ["docs/draft/**", "docs/_archive/**"]

  # 显式声明每份文档的元数据（也可让 DocGraph 自动嗅探）
  metadata:
    "docs/datasheet.pdf":
      type: datasheet
      version: rev9
      priority: 10
      chip_model: stm32f407          # 芯片型号/IP 实例，用于跨文档消歧（缺省由文件名推断）
    "docs/errata.pdf":
      type: errata
      version: rev3
      priority: 100
      chip_model: stm32f407
      supersedes: ["docs/datasheet.pdf", "docs/reference-manual.pdf"]

parsers:
  pdf:
    primary: auto                    # auto | docling | mineru | pymupdf
    fallback: []
    quality: balanced               # fast | balanced | accurate
    per_page_timeout: 60
  docx: { primary: docx }
  xlsx: { primary: xlsx }
  md:   { primary: markdown }

extractors:
  enabled:
    - section
    - table_entity
    - figure
    - glossary
  table_entity:
    schema_strict: true
    retry_on_fail: 2
    model_tier: balanced
  figure:
    model_tier: accurate

storage:
  graph_backend: sqlite
  vector_backend: sqlite_json   # sqlite_json | lancedb

logging:
  level: info
```

MinerU 模型权重默认复用 `~/.docgraph/mineru-models/`，不会随每个项目重复下载；项目内 `.docgraph/cache/` 只保存该项目的解析中间产物和图片缓存。

## 用户级示例：`~/.docgraph/config.yaml`

```yaml
llm:
  enabled: true
  provider: openai_compat
  providers:
    openai_compat:
      api_key: sk-...
      base_url: https://api.deepseek.com/v1
  tiers:
    fast: deepseek-chat
    balanced: deepseek-chat
    accurate: deepseek-chat
  vlm:
    provider: openai_compat
    model: GLM-4.6V-Flash
    api_key: sk-...
    base_url: https://open.bigmodel.cn/api/paas/v4

embeddings:
  provider: openai_compat
  model: doubao-embedding-vision
  dim: 1024
  api_key: sk-...
  base_url: https://ark.cn-beijing.volces.com/api/v3

cost:
  budget_per_build_usd: 5.0
  vlm_max_calls_per_doc: 500
```

## 字段说明

### `project`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | str | ✓ | 项目名 |
| `family` | str | ✓ | 芯片族标识，联邦合并的关键 |
| `description` | str | ✗ | 描述 |

### `docs`

| 字段 | 说明 |
|---|---|
| `include` | glob 模式列表 |
| `exclude` | glob 模式列表（优先级高于 include） |
| `metadata` | 路径 → 元数据，覆盖自动嗅探结果。每项可含 `type` / `version` / `priority` / `chip_model` / `supersedes` |

### `parsers`

每种格式有 `primary` + `fallback` + 选项。fallback 是链式降级，按顺序尝试。

PDF 支持 `quality` 档位：

| 档位 | 用途 | 行为 |
|---|---|---|
| `fast` | 首次导入、快速预览 | PDF 优先走轻量 PyMuPDF，保留 L0/L1 可回溯结构 |
| `balanced` | 日常构建 | 自动路由：born-digital / tagged PDF 优先 Docling，扫描或图片密集 PDF 优先 MinerU |
| `accurate` | 复杂版面复核 | 自动路由仍匹配文档类型；无法判断时偏向 MinerU 的高保真/OCR 路径 |

日常只需要 `docgraph build`。需要显式覆盖时使用 `docgraph build --quality fast|balanced|accurate`；质量检查统一使用 `docgraph doctor`。

### `extractors.enabled`

启用哪些 extractor。**顺序无关**，由 `requires` 拓扑排序。

### `extractors.<name>`

每个 extractor 的专属选项。常见：
- `model_tier`：`fast` / `balanced` / `accurate`
- `retry_on_fail`：LLM 失败重试次数
- `schema_strict`：是否严格 schema 校验

### `linker`

- `llm_for_low_confidence`：低置信对的 LLM 兜底
- `min_edge_confidence`：写入图谱的最低置信
- `alias_normalize`：别名归一规则

### `cost`

成本守门，避免失控的 LLM 调用。

### `llm`

- `enabled`：控制 LLM/VLM 增强。关闭时 L0/L1 仍完整构建。
- `providers`：文本 LLM provider 配置。每个 provider 支持 `api_key` / `base_url` 直写，也支持 `api_key_env` / `base_url_env` 从环境变量读取。
- `tiers`：tier → 具体模型映射
- `vlm`：独立视觉模型配置，支持 `provider` / `model` / `api_key` / `base_url`。为空时回退到文本 LLM provider 和 `vlm_model` / `tiers.accurate`。
- `vlm_model`：旧式视觉模型名字段，保留兼容；新配置优先使用 `llm.vlm.model`。

VLM 也可以使用独立环境变量作为兼容路径，避免和文本 LLM 共用 provider：

```bash
VLM_API_KEY=...
VLM_BASE_URL=https://...
VLM_MODEL_NAME=...
```

`FigureExtractor` 会根据文档上下文自动选择芯片图 prompt 或通用图 prompt。芯片图会抽取 modules / signals / interfaces / clocks_resets / address_regions / connections；通用图只增强 `FIGURE` 节点。

## API key 管理

- 推荐写入 `~/.docgraph/config.yaml` 的 `api_key` / `base_url` 字段，便于像 Claude Code 一样由用户级配置统一管理。
- 环境变量、`~/.docgraph/.env` / `.env.local` 仍可用于 CI、临时覆盖或不希望密钥出现在 YAML 的场景。
- 项目根 `.env` / `.env.local` 仅用于临时项目覆盖。
- **绝不写入项目 `docgraph.yaml` 或 `.docgraph/`**

### `~/.docgraph/config.yaml` 用法

在用户目录放 `~/.docgraph/config.yaml`：

```yaml
llm:
  enabled: true
  provider: openai_compat
  providers:
    openai_compat:
      api_key: sk-...
      base_url: https://api.deepseek.com/v1
```

### OpenAI 兼容 provider 配置（`~/.docgraph/config.yaml`）

```yaml
llm:
  enabled: true
  provider: openai_compat       # 任意 OpenAI 兼容端点
  providers:
    openai_compat:
      api_key: sk-...
      base_url: https://ark.cn-beijing.volces.com/api/v3
  tiers:
    fast: doubao-1-5-pro-32k
    balanced: doubao-1-5-pro-32k
    accurate: doubao-1-5-pro-32k
cost:
  budget_per_build_usd: 5.0
```

支持的 provider 名（都走同一段代码，只是注册名不同便于区分）：
`anthropic` / `openai` / `openai_compat` / `volces` / `deepseek` / `null`

## 离线 / 低成本构建

当前不提供额外的离线兼容命令。离线或低成本构建通过配置完成：

```yaml
llm:
  enabled: false
embeddings:
  provider: hash
```

行为：
- L0/L1 仍完整构建，表格/图/章节/chunk 和回溯链全部入库。
- L2 的 LLM/VLM 增强不产出新实体；已解析的 L0/L1 信息不受影响。
- 需要检查质量时统一运行 `docgraph doctor`。
