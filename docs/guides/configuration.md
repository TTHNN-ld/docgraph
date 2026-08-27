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
- 默认不构建向量，检索使用 FTS5 和 LIKE；图存储使用 SQLite。

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

EntityResolver 使用显式 `chip_model` 隔离实例，并用 `priority` 选择规范节点。FederationLinker 尚未按 `supersedes` 列表做字段级覆盖；勘误场景仍需核对实际关系。见[联邦机制](../architecture/federation.md)。

## Parser

通用字段包括：

| 字段 | 默认 | 说明 |
|---|---|---|
| `primary` | 按格式 | PDF 可用 `auto` |
| `fallback` | `[]` | 显式后端顺序 |
| `quality` | `balanced` | fast、balanced、accurate |
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
      api_key_env: TEXT_LLM_API_KEY
      base_url: https://text.example.com/v1
  tiers:
    fast: model-fast
    balanced: model-balanced
    accurate: model-accurate
  vlm:
    enabled: true
    provider: openai_compat
    model: vision-model
    api_key_env: VLM_API_KEY
    base_url: https://vision.example.com/v1
    figure_limit: 8

cost:
  budget_per_build_usd: 5.0
```

在项目或用户 `.env` 中设置 `TEXT_LLM_API_KEY` 和 `VLM_API_KEY`。文本 provider 支持 anthropic、openai/openai_compat 及兼容注册名。VLM 独立启用并要求自己的 provider、model 和凭证，不复用文本模型配置；因此只启用 VLM 而关闭文本 LLM 也可以工作。`budget_per_build_usd` 是两者共享的构建预算：并发请求按估算费用预留额度，响应后按实际费用结算。它用于阻止新的超预算请求，不是账单系统的精确上限。

模型结果默认是需要核验的 L2 candidate。启用远程服务前确认文档允许外发。

## Embedding 与存储

```yaml
embeddings:
  provider: bge_m3     # none | bge_m3 | openai | openai_compat
  model: BAAI/bge-m3
  dim: 1024
  api_key_env: EMBEDDING_API_KEY

storage:
  graph_backend: sqlite
  vector_backend: sqlite_json   # sqlite_json | lancedb
```

`none` 是默认值，此时 L1 仍可通过 FTS5/LIKE 检索。已有用户配置中的 `provider: hash` 会按配置优先级继续生效，需要关闭时改成 `none`。`bge_m3` 提供本地多语言语义召回，需要 `uv sync --extra embeddings`；`openai`/`openai_compat` 使用远程服务。配置的 provider 不可用时，查询明确降级为文本检索，不会静默改用另一种向量。

`hash` 仍可显式用于测试和离线链路验证，但它只是词项哈希，不是语义模型，也不会作为独立语义召回通道。修改 provider、model、dim、远程 endpoint 或向量后端后再次运行 `uv run docgraph build`，系统会刷新语义不再兼容的向量，并核对每个节点和 chunk 的 ID、内容 hash 与维度；服务返回数量或维度不完整时构建会降级，不接受部分成功。

## 检查

```bash
docgraph setup
docgraph setup --json
```

`setup` 只检查状态；`docgraph setup parsers` 才会按策略安装推荐后端。精确字段见 [`docgraph/core/config.py`](../../docgraph/core/config.py)。
