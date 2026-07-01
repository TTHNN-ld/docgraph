"""配置加载与默认值。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class ProjectConfig(BaseModel):
    name: str = "docgraph-project"
    family: str = "default"
    description: str = ""


class ParserFormatConfig(BaseModel):
    primary: str
    fallback: list[str] = Field(default_factory=list)
    quality: str = "balanced"
    per_page_timeout: int = 60
    page_failure_strategy: str = "skip"

    @field_validator("quality")
    @classmethod
    def validate_quality(cls, value: str) -> str:
        normalized = (value or "balanced").strip().lower()
        if normalized not in {"fast", "balanced", "accurate"}:
            raise ValueError("parser quality must be one of: fast, balanced, accurate")
        return normalized


class ParsersConfig(BaseModel):
    pdf: ParserFormatConfig = ParserFormatConfig(primary="pymupdf")
    docx: ParserFormatConfig = ParserFormatConfig(primary="docx")
    xlsx: ParserFormatConfig = ParserFormatConfig(primary="xlsx")
    md: ParserFormatConfig = ParserFormatConfig(primary="markdown")


class ExtractorEntry(BaseModel):
    model_tier: str = "balanced"
    retry_on_fail: int = 2
    schema_strict: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)


class ExtractorsConfig(BaseModel):
    """已启用的 extractor 列表 + 每个 extractor 的专属选项。"""
    enabled: list[str] = Field(default_factory=lambda: ["section", "table_entity"])
    options: dict[str, ExtractorEntry] = Field(default_factory=dict)

    def get_entry(self, name: str) -> ExtractorEntry:
        return self.options.get(name) or ExtractorEntry()


class LLMTiers(BaseModel):
    """tier → 具体模型名。

    可以指定任意模型名（不止 Claude）；调用时按 provider 路由。
    """
    fast: str = "claude-haiku-4-5-20251001"
    balanced: str = "claude-sonnet-4-6"
    accurate: str = "claude-opus-4-8"


class LLMProviderConfig(BaseModel):
    """provider 配置。

    对 OpenAI 兼容：
      api_key_env: OPENAI_API_KEY
      base_url_env: OPENAI_BASE_URL  (会被 .env 覆盖)
      base_url:     显式覆盖（优先级最高）
    """
    api_key_env: str = "ANTHROPIC_API_KEY"
    base_url_env: str | None = None
    base_url: str | None = None


class LLMConfig(BaseModel):
    """LLM 总配置。

    provider 可选值：anthropic / openai / openai_compat / volces / deepseek / qwen / glm / null

    OpenAI 兼容场景（如火山方舟、DeepSeek、Together、Groq、vLLM、Ollama）：
        llm:
          enabled: true
          provider: openai_compat
          providers:
            openai_compat:
              api_key_env: OPENAI_API_KEY
              base_url_env: OPENAI_BASE_URL
          tiers:
            fast: doubao-1-5-pro-32k
            balanced: doubao-1-5-pro-32k
            accurate: doubao-1-5-pro-32k
          vlm_model: qwen-vl-max  # 视觉模型可选；不设则用 accurate
    """
    enabled: bool = False
    provider: str = "anthropic"
    providers: dict[str, LLMProviderConfig] = Field(
        default_factory=lambda: {
            "anthropic": LLMProviderConfig(api_key_env="ANTHROPIC_API_KEY"),
            "openai_compat": LLMProviderConfig(
                api_key_env="OPENAI_API_KEY",
                base_url_env="OPENAI_BASE_URL",
            ),
        }
    )
    tiers: LLMTiers = Field(default_factory=LLMTiers)
    vlm_model: str | None = None


class StorageConfig(BaseModel):
    graph_backend: str = "sqlite"
    vector_backend: str = "sqlite_json"


class EmbeddingsConfig(BaseModel):
    """Embedding provider 配置。

    provider 可选值：hash / bge_m3 / openai_compat / openai
    """
    provider: str = "hash"
    model: str | None = None        # bge_m3 默认 "BAAI/bge-m3"；openai 默认 "text-embedding-3-small"
    dim: int | None = None          # 不设则用 provider 默认
    api_key_env: str = "EMBEDDING_API_KEY"
    api_key_fallback_env: str = "OPENAI_API_KEY"
    base_url_env: str = "EMBEDDING_BASE_URL"
    base_url_fallback_env: str = "OPENAI_BASE_URL"


class DocsConfig(BaseModel):
    include: list[str] = Field(
        default_factory=lambda: ["docs/**/*.pdf", "spec/**/*.pdf"]
    )
    exclude: list[str] = Field(default_factory=list)
    metadata: dict[str, dict[str, Any]] = Field(default_factory=dict)


class LoggingConfig(BaseModel):
    level: str = "info"
    file: str | None = None


class CostConfig(BaseModel):
    budget_per_build_usd: float = 5.0
    vlm_max_calls_per_doc: int = 500


class DocGraphConfig(BaseModel):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    docs: DocsConfig = Field(default_factory=DocsConfig)
    parsers: ParsersConfig = Field(default_factory=ParsersConfig)
    extractors: ExtractorsConfig = Field(default_factory=ExtractorsConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embeddings: EmbeddingsConfig = Field(default_factory=EmbeddingsConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    cost: CostConfig = Field(default_factory=CostConfig)


DEFAULT_CONFIG_YAML = """\
project:
  name: my-chip-spec
  family: default
  description: ""

docs:
  include:
    - "docs/**/*.pdf"
    - "spec/**/*.pdf"
  exclude: []

parsers:
  pdf:
    primary: pymupdf
    fallback: []
    quality: balanced  # fast | balanced | accurate

extractors:
  enabled:
    - section
    - table_entity
    - glossary
    - figure

llm:
  enabled: false        # 改 true 即启用；同时设置 .env 中的 API key
  provider: anthropic   # anthropic | openai_compat | openai | null
  providers:
    anthropic:
      api_key_env: ANTHROPIC_API_KEY
    openai_compat:
      api_key_env: OPENAI_API_KEY
      base_url_env: OPENAI_BASE_URL
  tiers:
    fast: claude-haiku-4-5-20251001
    balanced: claude-sonnet-4-6
    accurate: claude-opus-4-8
  # vlm_model: claude-sonnet-4-6   # 可选 vision 模型（不设则用 accurate）

embeddings:
  provider: hash        # hash | bge_m3 | openai_compat | openai
  # model: text-embedding-3-small
  # dim: 1536

storage:
  graph_backend: sqlite
  vector_backend: sqlite_json  # sqlite_json | lancedb

cost:
  budget_per_build_usd: 5.0

logging:
  level: info
"""


def project_root_from_cwd(cwd: Path | None = None) -> Path:
    cwd = cwd or Path.cwd()
    cur = cwd.resolve()
    for p in [cur, *cur.parents]:
        if (p / ".docgraph").is_dir():
            return p
    return cur


def docgraph_dir(root: Path) -> Path:
    return root / ".docgraph"


def config_path(root: Path) -> Path:
    return docgraph_dir(root) / "config.yaml"


def load_config(root: Path) -> DocGraphConfig:
    cfg_path = config_path(root)
    if not cfg_path.is_file():
        return DocGraphConfig()
    with cfg_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return DocGraphConfig.model_validate(data)


def write_default_config(root: Path, overwrite: bool = False) -> Path:
    cfg_path = config_path(root)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    if cfg_path.exists() and not overwrite:
        return cfg_path
    cfg_path.write_text(DEFAULT_CONFIG_YAML, encoding="utf-8")
    return cfg_path
