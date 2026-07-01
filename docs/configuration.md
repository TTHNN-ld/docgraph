# 配置参考

> 对应 DESIGN.md §15。`.docgraph/config.yaml` 的完整字段说明。

## 完整示例

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
    primary: mineru
    fallback: [marker, pymupdf]
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

embeddings:
  provider: bge_m3
  dim: 1024
  chunk_size: 512
  chunk_overlap: 64

linker:
  llm_for_low_confidence: claude-haiku-4-5-20251001
  min_edge_confidence: 0.5
  alias_normalize:
    case_insensitive: true
    strip_prefixes: ["REG_", "BIT_"]

storage:
  graph_backend: sqlite
  vector_backend: sqlite_json   # sqlite_json | lancedb

logging:
  level: info
  file: .docgraph/logs/docgraph.log

cost:
  budget_per_build_usd: 5.0        # 超预算自动暂停，等用户确认
  vlm_max_calls_per_doc: 500

llm:
  enabled: true
  provider: anthropic
  providers:
    anthropic:
      api_key_env: ANTHROPIC_API_KEY
      base_url: null
  tiers:
    fast: claude-haiku-4-5-20251001
    balanced: claude-sonnet-4-6
    accurate: claude-opus-4-8
  vlm_model: claude-sonnet-4-6       # 可选；不设置时使用 accurate tier
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
| `balanced` | 默认生产路径 | 按配置 parser 链执行，推荐 MinerU + PyMuPDF fallback |
| `accurate` | 复杂版面复核 | 按配置 parser 链执行，保留表格识别等高保真能力 |

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
- `providers`：API 配置
- `tiers`：tier → 具体模型映射
- `vlm_model`：视觉模型名；为空时使用 `tiers.accurate`

VLM 也可以使用独立环境变量，避免和文本 LLM 共用 provider：

```bash
VLM_API_KEY=...
VLM_BASE_URL=https://...
VLM_MODEL_NAME=...
```

`FigureExtractor` 会根据文档上下文自动选择芯片图 prompt 或通用图 prompt。芯片图会抽取 modules / signals / interfaces / clocks_resets / address_regions / connections；通用图只增强 `FIGURE` 节点。

## API key 管理

- 优先环境变量（`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` 等）
- 其次 `.env` / `.env.local`（项目根，DocGraph 启动时自动加载）
- 再次 `~/.docgraph/credentials`
- **绝不写入项目 `config.yaml`**

### `.env` 用法

在项目根放 `.env`（git 应当 ignore）：

```bash
# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# 或者 OpenAI 兼容（火山方舟、DeepSeek、Together、Groq、vLLM、Ollama 等）
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.deepseek.com/v1
```

### OpenAI 兼容 provider 配置

```yaml
llm:
  enabled: true
  provider: openai_compat       # 任意 OpenAI 兼容端点
  providers:
    openai_compat:
      api_key_env: OPENAI_API_KEY
      base_url_env: OPENAI_BASE_URL
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
