"""M2.5 测试：.env 加载 + OpenAI 兼容 provider 工厂。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# .env 加载
# ---------------------------------------------------------------------------


def test_load_env_file_basic(monkeypatch):
    from docgraph.core.dotenv import load_env_file

    monkeypatch.delenv("DG_TEST_A", raising=False)
    monkeypatch.delenv("DG_TEST_B", raising=False)
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".env") as f:
        f.write(
            "# comment\n"
            "DG_TEST_A=hello\n"
            "export DG_TEST_B='world'\n"
            'DG_TEST_C="quoted value"\n'
        )
        path = f.name
    try:
        loaded = load_env_file(path)
        assert os.environ["DG_TEST_A"] == "hello"
        assert os.environ["DG_TEST_B"] == "world"
        assert os.environ["DG_TEST_C"] == "quoted value"
        assert "DG_TEST_A" in loaded
    finally:
        os.unlink(path)
        monkeypatch.delenv("DG_TEST_A", raising=False)
        monkeypatch.delenv("DG_TEST_B", raising=False)
        monkeypatch.delenv("DG_TEST_C", raising=False)


def test_load_env_does_not_override(monkeypatch):
    from docgraph.core.dotenv import load_env_file

    monkeypatch.setenv("DG_TEST_EXISTING", "preset")
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".env") as f:
        f.write("DG_TEST_EXISTING=from-file\n")
        path = f.name
    try:
        load_env_file(path, override=False)
        assert os.environ["DG_TEST_EXISTING"] == "preset"
        load_env_file(path, override=True)
        assert os.environ["DG_TEST_EXISTING"] == "from-file"
    finally:
        os.unlink(path)
        monkeypatch.delenv("DG_TEST_EXISTING", raising=False)


def test_autoload_env_walks_up(monkeypatch, tmp_path):
    from docgraph.core.dotenv import autoload_env

    monkeypatch.delenv("DG_TEST_AUTOLOAD", raising=False)
    (tmp_path / ".env").write_text("DG_TEST_AUTOLOAD=found\n", encoding="utf-8")
    # pyproject 作为根锚
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    sub = tmp_path / "nested" / "deeper"
    sub.mkdir(parents=True)

    autoload_env(start=sub)
    assert os.environ["DG_TEST_AUTOLOAD"] == "found"
    monkeypatch.delenv("DG_TEST_AUTOLOAD", raising=False)


def test_autoload_env_loads_user_docgraph_env(monkeypatch, tmp_path):
    from docgraph.core.dotenv import autoload_env

    home = tmp_path / "home"
    project = tmp_path / "project"
    (home / ".docgraph").mkdir(parents=True)
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("DG_TEST_USER_ENV", raising=False)
    (home / ".docgraph" / ".env").write_text("DG_TEST_USER_ENV=from-user\n", encoding="utf-8")
    (project / ".docgraph").mkdir()

    autoload_env(start=project)

    assert os.environ["DG_TEST_USER_ENV"] == "from-user"
    monkeypatch.delenv("DG_TEST_USER_ENV", raising=False)


# ---------------------------------------------------------------------------
# Provider 工厂
# ---------------------------------------------------------------------------


def test_provider_factory_openai_compat(monkeypatch):
    from docgraph.llm.client import OpenAICompatProvider, make_provider

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    p = make_provider(
        "openai_compat",
        api_key_env="OPENAI_API_KEY",
        base_url_env="OPENAI_BASE_URL",
    )
    assert isinstance(p, OpenAICompatProvider)
    assert p.api_key == "test-key"
    assert p.base_url == "https://example.com/v1"


def test_provider_factory_explicit_base_url(monkeypatch):
    from docgraph.llm.client import make_provider

    monkeypatch.setenv("OPENAI_API_KEY", "k")
    p = make_provider(
        "openai_compat",
        api_key_env="OPENAI_API_KEY",
        base_url="https://override.example/v1",
    )
    assert p.base_url == "https://override.example/v1"


def test_provider_factory_explicit_api_key_without_env(monkeypatch):
    from docgraph.llm.client import make_provider

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    p = make_provider(
        "openai_compat",
        api_key_env="OPENAI_API_KEY",
        api_key="config-key",
        base_url="https://config.example/v1",
    )
    assert p.api_key == "config-key"
    assert p.base_url == "https://config.example/v1"


def test_provider_factory_unknown():
    from docgraph.llm.client import make_provider

    with pytest.raises(ValueError):
        make_provider("does-not-exist")


def test_anthropic_provider_missing_key(monkeypatch):
    from docgraph.llm.client import AnthropicProvider

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p = AnthropicProvider()
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        p._ensure_client()


def test_openai_provider_missing_key(monkeypatch):
    from docgraph.llm.client import OpenAICompatProvider

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    p = OpenAICompatProvider(api_key_env="OPENAI_API_KEY", base_url_env="OPENAI_BASE_URL")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        p._ensure_client()


# ---------------------------------------------------------------------------
# 成本估算
# ---------------------------------------------------------------------------


def test_cost_estimate_known_model():
    from docgraph.llm.client import estimate_cost

    # claude-sonnet-4-6: 3 / 15 per 1M
    c = estimate_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
    assert abs(c - 18.0) < 1e-6


def test_cost_estimate_unknown_model_uses_default():
    from docgraph.llm.client import estimate_cost

    # unknown 用默认值 (1.0, 3.0)
    c = estimate_cost("totally-unknown-model", 1_000_000, 1_000_000)
    assert abs(c - 4.0) < 1e-6


# ---------------------------------------------------------------------------
# Config 升级
# ---------------------------------------------------------------------------


def test_config_supports_openai_compat():
    """LLMConfig 默认 providers 含 openai_compat。"""
    from docgraph.core.config import DocGraphConfig

    cfg = DocGraphConfig()
    assert "openai_compat" in cfg.llm.providers
    pc = cfg.llm.providers["openai_compat"]
    assert pc.api_key_env == "OPENAI_API_KEY"
    assert pc.base_url_env == "OPENAI_BASE_URL"


def test_config_yaml_round_trip(tmp_path):
    """yaml 可解析 openai_compat 配置。"""
    import yaml
    from docgraph.core.config import DocGraphConfig

    yaml_str = """
project:
  name: t
  family: t
llm:
  enabled: true
  provider: openai_compat
  providers:
    openai_compat:
      api_key: config-key
      api_key_env: MY_KEY
      base_url: https://config.example/v1
      base_url_env: MY_URL
  vlm:
    provider: openai_compat
    model: GLM-4.6V-Flash
    api_key: vlm-key
    base_url: https://vlm.example/v1
  tiers:
    fast: model-fast
    balanced: model-bal
    accurate: model-acc
embeddings:
  provider: openai_compat
  api_key: embed-key
  base_url: https://embed.example/v1
"""
    data = yaml.safe_load(yaml_str)
    cfg = DocGraphConfig.model_validate(data)
    assert cfg.llm.enabled is True
    assert cfg.llm.provider == "openai_compat"
    assert cfg.llm.providers["openai_compat"].api_key == "config-key"
    assert cfg.llm.providers["openai_compat"].api_key_env == "MY_KEY"
    assert cfg.llm.providers["openai_compat"].base_url == "https://config.example/v1"
    assert cfg.llm.vlm.model == "GLM-4.6V-Flash"
    assert cfg.llm.vlm.api_key == "vlm-key"
    assert cfg.llm.tiers.fast == "model-fast"
    assert cfg.embeddings.api_key == "embed-key"
    assert cfg.embeddings.base_url == "https://embed.example/v1"


def test_load_config_merges_user_and_project_configs(tmp_path, monkeypatch):
    from docgraph.core.config import load_config

    home = tmp_path / "home"
    project = tmp_path / "project"
    (home / ".docgraph").mkdir(parents=True)
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    (home / ".docgraph" / "config.yaml").write_text(
        """
llm:
  enabled: true
  provider: openai_compat
  providers:
    openai_compat:
      api_key_env: USER_KEY
embeddings:
  provider: openai_compat
  model: embed-model
""",
        encoding="utf-8",
    )
    (project / "docgraph.yaml").write_text(
        """
project:
  name: demo
  family: chip
docs:
  include:
    - "spec/**/*.pdf"
""",
        encoding="utf-8",
    )

    cfg = load_config(project)

    assert cfg.project.family == "chip"
    assert cfg.docs.include == ["spec/**/*.pdf"]
    assert cfg.llm.enabled is True
    assert cfg.llm.provider == "openai_compat"
    assert cfg.llm.providers["openai_compat"].api_key_env == "USER_KEY"
    assert cfg.embeddings.model == "embed-model"


def test_openai_embedding_uses_config_api_key_without_env(monkeypatch):
    from docgraph.embeddings.openai_encoder import OpenAIEmbeddingProvider

    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    enc = OpenAIEmbeddingProvider(
        model="embed",
        dim=8,
        api_key="embed-config-key",
        base_url="https://embed.example/v1",
    )

    assert enc.api_key == "embed-config-key"
    assert enc.base_url == "https://embed.example/v1"


def test_init_does_not_create_project_config_by_default(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from docgraph.cli.main import app

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    runner = CliRunner()

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert not (project / "docgraph.yaml").exists()
    assert (project / ".docgraph/graph.db").exists()
    assert (home / ".docgraph" / "config.yaml").exists()


def test_init_creates_minimal_project_config_when_project_metadata_is_given(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from docgraph.cli.main import app

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    runner = CliRunner()

    result = runner.invoke(app, ["init", "--family", "chip"])

    assert result.exit_code == 0
    assert (project / "docgraph.yaml").read_text("utf-8") == "project:\n  family: chip\n"
    assert (project / ".docgraph/graph.db").exists()
    assert (home / ".docgraph" / "config.yaml").exists()
