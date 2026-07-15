"""配置加载与默认值。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

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
    device: str = "cpu"
    ocr_device: str | None = None

    @field_validator("quality")
    @classmethod
    def validate_quality(cls, value: str) -> str:
        normalized = (value or "balanced").strip().lower()
        if normalized not in {"fast", "balanced", "accurate"}:
            raise ValueError("parser quality must be one of: fast, balanced, accurate")
        return normalized

    @field_validator("device")
    @classmethod
    def validate_device(cls, value: str) -> str:
        # torch device for model-based parsers (MinerU/Marker). pymupdf/docx ignore it.
        # Only torch backends; rapid_table (onnx) always stays on CPU regardless.
        normalized = (value or "cpu").strip().lower()
        if normalized not in {"cpu", "cuda", "mps"}:
            raise ValueError("parser device must be one of: cpu, cuda, mps")
        return normalized

    @field_validator("ocr_device")
    @classmethod
    def validate_ocr_device(cls, value: str | None) -> str | None:
        # MinerU OCR device override (paddleocr2pytorch). None -> follow `device`.
        # Useful on Apple Silicon: layout wins on mps, OCR is faster on cpu.
        if value is None:
            return None
        normalized = str(value).strip().lower()
        if normalized not in {"cpu", "cuda", "mps"}:
            raise ValueError("parser ocr_device must be one of: cpu, cuda, mps, or null")
        return normalized


class ParsersConfig(BaseModel):
    pdf: ParserFormatConfig = ParserFormatConfig(primary="auto")
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
      api_key:     直接写在 ~/.docgraph/config.yaml 中（优先级最高）
      base_url:    直接写在 ~/.docgraph/config.yaml 中（优先级最高）
      api_key_env: OPENAI_API_KEY    (兼容环境变量/.env)
      base_url_env: OPENAI_BASE_URL  (兼容环境变量/.env)
    """
    api_key: str | None = None
    api_key_env: str = "ANTHROPIC_API_KEY"
    base_url_env: str | None = None
    base_url: str | None = None


class VLMConfig(BaseModel):
    """独立 VLM 配置。

    允许文本 LLM 与视觉模型使用不同 provider/model。直接配置值优先，
    环境变量仍作为兼容兜底。

    figure_limit: 每文档最多送多少张图给 VLM 做语义增强 (默认 8, 见
    FigureExtractor.DEFAULT_VLM_FIGURE_LIMIT). 设大一点 (如 200) 即近似 "全量".
    DOCGRAPH_VLM_FIGURE_LIMIT 环境变量仍可单次覆盖本值.
    """
    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    api_key_env: str = "VLM_API_KEY"
    base_url: str | None = None
    base_url_env: str = "VLM_BASE_URL"
    figure_limit: int | None = None


class LLMConfig(BaseModel):
    """LLM 总配置。

    provider 可选值：anthropic / openai / openai_compat / volces / deepseek / qwen / glm / null

    OpenAI 兼容场景（如火山方舟、DeepSeek、Together、Groq、vLLM、Ollama）：
        llm:
          enabled: true
          provider: openai_compat
          providers:
            openai_compat:
              api_key: sk-...
              base_url: https://...
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
    vlm: VLMConfig = Field(default_factory=VLMConfig)


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
    api_key: str | None = None
    api_key_env: str = "EMBEDDING_API_KEY"
    api_key_fallback_env: str = "OPENAI_API_KEY"
    base_url: str | None = None
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


class RuntimeConfig(BaseModel):
    """Runtime behavior for optional parsers and parser failures."""

    dependency_policy: Literal["prompt", "install", "fallback", "error"] = "prompt"
    parser_failure: Literal["fallback", "error"] = "fallback"


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
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)


DEFAULT_PROJECT_CONFIG_YAML = """\
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
    primary: auto     # auto | docling | mineru | pymupdf
    fallback: []
    quality: balanced  # fast | balanced | accurate

extractors:
  enabled:
    - section
    - table_entity
    - glossary
    - figure

storage:
  graph_backend: sqlite
  vector_backend: sqlite_json  # sqlite_json | lancedb

cost:
  budget_per_build_usd: 5.0

runtime:
  dependency_policy: prompt  # prompt | install | fallback | error
  parser_failure: fallback   # fallback | error

logging:
  level: info
"""


DEFAULT_USER_CONFIG_YAML = """\
llm:
  enabled: false        # 改 true 即启用；api_key 可直接写在本文件
  provider: anthropic   # anthropic | openai_compat | openai | null
  providers:
    anthropic:
      # api_key: sk-ant-...
      api_key_env: ANTHROPIC_API_KEY
    openai_compat:
      # api_key: sk-...
      # base_url: https://api.example.com/v1
      api_key_env: OPENAI_API_KEY
      base_url_env: OPENAI_BASE_URL
  tiers:
    fast: claude-haiku-4-5-20251001
    balanced: claude-sonnet-4-6
    accurate: claude-opus-4-8
  vlm: {}
    # provider: openai_compat
    # model: GLM-4.6V-Flash
    # api_key: sk-...
    # base_url: https://api.example.com/v1
    # figure_limit: 8   # 每文档送 VLM 的图数上限; 调大 (如 200) 近似全量. 环境变量 DOCGRAPH_VLM_FIGURE_LIMIT 可覆盖

embeddings:
  provider: hash        # hash | bge_m3 | openai_compat | openai
  # model: text-embedding-3-small
  # dim: 1536
  # api_key: sk-...
  # base_url: https://api.example.com/v1

# Optional: override the automatic PDF router.
# parsers:
#   pdf:
#     primary: mineru
#     fallback: [docling, pymupdf]
#     quality: balanced
"""


DEFAULT_CONFIG_YAML = DEFAULT_PROJECT_CONFIG_YAML


def project_root_from_cwd(cwd: Path | None = None) -> Path:
    cwd = cwd or Path.cwd()
    cur = cwd.resolve()
    for p in [cur, *cur.parents]:
        if (p / ".docgraph").is_dir():
            return p
    return cur


def docgraph_dir(root: Path) -> Path:
    return root / ".docgraph"


def user_docgraph_dir() -> Path:
    return Path.home() / ".docgraph"


def user_config_path() -> Path:
    return user_docgraph_dir() / "config.yaml"


def project_config_path(root: Path) -> Path:
    return root / "docgraph.yaml"


def config_path(root: Path) -> Path:
    return project_config_path(root)


def load_config(root: Path) -> DocGraphConfig:
    data: dict[str, Any] = {}
    for cfg_path in (user_config_path(), project_config_path(root)):
        if not cfg_path.is_file():
            continue
        with cfg_path.open("r", encoding="utf-8") as f:
            data = _deep_merge(data, yaml.safe_load(f) or {})
    return DocGraphConfig.model_validate(data)


def write_default_config(root: Path, overwrite: bool = False) -> Path:
    cfg_path = project_config_path(root)
    if cfg_path.exists() and not overwrite:
        return cfg_path
    cfg_path.write_text(DEFAULT_PROJECT_CONFIG_YAML, encoding="utf-8")
    return cfg_path


def write_default_user_config(overwrite: bool = False) -> Path:
    cfg_path = user_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    if cfg_path.exists() and not overwrite:
        return cfg_path
    cfg_path.write_text(DEFAULT_USER_CONFIG_YAML, encoding="utf-8")
    return cfg_path


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out
