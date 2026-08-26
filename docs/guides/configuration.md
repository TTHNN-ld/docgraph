# 配置指南

DocGraph 可以零项目配置运行。配置只表达项目差异或用户级 provider 偏好；`.docgraph/` 只保存生成物。

## 来源与优先级

Pydantic 默认值先加载，再递归合并：

1. `~/.docgraph/config.yaml`
2. `<project>/docgraph.yaml`

已有环境变量优先于 dotenv；dotenv 依次读取用户和项目目录中的 `.env.local`、`.env`，且不覆盖已存在值。加载器只支持单行 `KEY=VALUE`、引号和 `export` 前缀。

## 默认行为

- 扫描 `docs/` 和 `spec/` 下的 PDF、DOCX、XLSX/XLSM、MD/Markdown。
- PDF 使用 `auto`/`balanced`，可选后端不可用时回退到 PyMuPDF。
- 启用 `section`、`table_entity`；LLM/VLM 关闭。
- embedding 使用本地 `hash`，图和向量分别使用 SQLite、`sqlite_json`。

`docgraph init` 默认只创建 `.docgraph/` 和缺失的用户配置；传入 `--name` 或 `--family` 时才写最小项目配置。

## 最小项目配置

只写需要覆盖的字段：

```yaml
project:
  name: stm32f407-spec
  family: stm32f407

docs:
  include:
    - "docs/**/*.pdf"
    - "docs/**/*.docx"
    - "docs/**/*.xlsx"
    - "docs/**/*.md"
  exclude:
    - "docs/draft/**"
  metadata:
    "docs/reference-manual.pdf":
      type: reference_manual
      version: rev9
      priority: 20
      chip_model: stm32f407

parsers:
  pdf:
    primary: auto
    quality: balanced

extractors:
  enabled: [section, table_entity]
```

`docs.include` 会整体替换默认值，不会自动追加未写出的格式。

## 文档元数据

| 字段 | 用途 |
|---|---|
| `type` | datasheet、reference_manual、trm、errata、app_note、user_guide、protocol、unknown |
| `version` | 文档版本 |
| `priority` | 同名实体的来源优先级，数字越大越高 |
| `chip_model` | 芯片实例标识 |
| `supersedes` | 显式覆盖来源列表，当前仅保存在解析元数据 |

当前 EntityResolver 尚未完整使用显式 `chip_model`，FederationLinker 也未按 `supersedes` 列表做字段级覆盖；多芯片和勘误场景需要核对实际关系。见[联邦机制](../architecture/federation.md)。

## Parser

通用字段包括：

| 字段 | 默认 | 说明 |
|---|---|---|
| `primary` | 按格式 | PDF 可用 `auto` |
| `fallback` | `[]` | 显式后端顺序 |
| `quality` | `balanced` | fast、balanced、accurate |
| `per_page_timeout` | `60` | 后端可选的页级超时提示 |
| `device` | `cpu` | cpu、cuda、mps |
| `ocr_device` | `null` | MinerU OCR device 覆盖 |

临时覆盖：

```bash
docgraph build --quality fast
docgraph build --quality accurate --strict-parsers
docgraph build --install-missing
```

`runtime.dependency_policy` 支持 prompt、install、fallback、error；`runtime.parser_failure` 支持 fallback、error。自动同步只允许维护方声明的 extra。

### MinerU 远程推理

先同步 extra：

```bash
uv sync --extra mineru
```

```yaml
parsers:
  pdf:
    primary: mineru
    fallback: [pymupdf]
    mineru:
      backend: vlm-http-client
      model_server_url: http://gpu-server:30000
      model: MinerU2.5-2509-1.2B
      api_key_env: MINERU_VL_API_KEY
      timeout_seconds: 3600
```

`model_server_url` 是 OpenAI-compatible 模型推理地址，不是文档解析 API。也可使用 `MINERU_MODEL_SERVER_URL`、`MINERU_VL_MODEL_NAME` 和 `MINERU_VL_API_KEY`。

MinerU 不依赖 Docling 或 Marker；核心 PyMuPDF 会保留为兜底。若希望 `auto` 同时选择 Docling 和 MinerU，使用 `uv sync --extra docling --extra mineru`。Marker 与 MinerU 不能安装在同一环境中。

## LLM 与 VLM

先同步 provider：

```bash
uv sync --extra llm
```

推荐放在 `~/.docgraph/config.yaml`：

```yaml
llm:
  enabled: true
  provider: openai_compat
  providers:
    openai_compat:
      api_key: sk-...
      base_url: https://text.example.com/v1
  tiers:
    fast: model-fast
    balanced: model-balanced
    accurate: model-accurate
  vlm:
    provider: openai_compat
    model: vision-model
    api_key: sk-...
    base_url: https://vision.example.com/v1
    figure_limit: 8

cost:
  budget_per_build_usd: 5.0
```

文本 provider 支持 anthropic、openai/openai_compat 及兼容注册名。VLM 凭证独立配置；不要默认复用文本模型密钥。环境变量仍可用于 CI 或临时覆盖。

模型结果默认是需要核验的 L2 candidate。启用远程服务前确认文档允许外发。

## Embedding 与存储

```yaml
embeddings:
  provider: hash       # hash | bge_m3 | openai | openai_compat
  model: text-embedding-3-small
  dim: 1536
  api_key_env: EMBEDDING_API_KEY

storage:
  graph_backend: sqlite
  vector_backend: sqlite_json   # sqlite_json | lancedb
```

`hash` 是零外部服务默认值。远程 embedding 初始化失败会回退到 hash，实际 provider 应从构建日志确认。

## 检查

```bash
docgraph setup
docgraph setup --json
```

`setup` 只检查状态；`docgraph setup parsers` 才会按策略安装推荐后端。精确字段见 [`docgraph/core/config.py`](../../docgraph/core/config.py)。
