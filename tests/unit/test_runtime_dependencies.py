from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner


def test_noninteractive_prompt_does_not_install(monkeypatch):
    from docgraph.core import dependencies

    monkeypatch.setattr(dependencies, "_module_available", lambda _module: False)
    monkeypatch.setattr(dependencies, "_is_interactive", lambda: False)

    result = dependencies.ensure_parser_dependency("docling", "prompt")

    assert result.available is False
    assert result.attempted_install is False
    assert "--install-missing" in (result.reason or "")


def test_install_policy_uses_allowlisted_extra(monkeypatch):
    from docgraph.core import dependencies

    availability = iter([False, True])
    commands: list[list[str]] = []
    monkeypatch.setattr(
        dependencies, "_module_available", lambda _module: next(availability)
    )
    monkeypatch.setattr(
        dependencies.subprocess,
        "run",
        lambda command, check: commands.append(command) or SimpleNamespace(returncode=0),
    )

    result = dependencies.ensure_parser_dependency("docling", "install")

    assert result.available is True
    assert result.installed is True
    assert commands[0][:5] == ["uv", "sync", "--locked", "--inexact", "--project"]
    assert commands[0][-2:] == ["--extra", "docling"]


def test_unknown_plugin_is_never_auto_installed(monkeypatch):
    from docgraph.core import dependencies

    monkeypatch.setattr(
        dependencies.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("uv called")),
    )

    result = dependencies.ensure_parser_dependency("third_party_parser", "install")

    assert result.available is True
    assert result.attempted_install is False


def test_scanned_pdf_rejects_low_text_pymupdf_result():
    from docgraph.graph.schema import Block, BlockKind, ParsedDoc, ParsedPage
    from docgraph.parsers.pdf_router import PdfProfile, assess_pdf_parse

    parsed = ParsedDoc(
        doc_id="doc",
        source_path="scan.pdf",
        parser="pymupdf",
        pages=[
            ParsedPage(
                page_no=1,
                blocks=[
                    Block(
                        id="doc#p1#b0",
                        doc_id="doc",
                        page=1,
                        kind=BlockKind.PARAGRAPH,
                        text="tiny",
                    )
                ],
            )
        ],
    )
    profile = PdfProfile(page_count=1, is_probably_scanned=True)

    verdict = assess_pdf_parse(parsed, profile)

    assert verdict.ok is False
    assert "only 4 text characters" in (verdict.reason or "")


def test_runtime_config_defaults_are_safe():
    import yaml

    from docgraph.core.config import (
        DEFAULT_PROJECT_CONFIG_YAML,
        SUPPORTED_DOCUMENT_SUFFIXES,
        DocGraphConfig,
    )

    cfg = DocGraphConfig()
    runtime = cfg.runtime
    assert runtime.dependency_policy == "prompt"
    assert runtime.parser_failure == "fallback"
    assert SUPPORTED_DOCUMENT_SUFFIXES == {
        ".pdf", ".docx", ".xlsx", ".xlsm", ".md", ".markdown"
    }
    assert all(
        any(pattern.endswith(suffix) for pattern in cfg.docs.include)
        for suffix in SUPPORTED_DOCUMENT_SUFFIXES
    )
    template_cfg = DocGraphConfig.model_validate(yaml.safe_load(DEFAULT_PROJECT_CONFIG_YAML))
    assert template_cfg.docs.include == cfg.docs.include


def test_default_user_config_validates():
    import yaml

    from docgraph.core.config import DEFAULT_USER_CONFIG_YAML, DocGraphConfig

    cfg = DocGraphConfig.model_validate(yaml.safe_load(DEFAULT_USER_CONFIG_YAML))

    assert cfg.llm.vlm is not None
    assert cfg.embeddings.provider == "hash"


def test_setup_command_reports_missing_core_parser_as_not_ready(monkeypatch, tmp_path):
    from docgraph.cli import main
    from docgraph.core.dependencies import DependencyResult

    available = {
        "pymupdf": True,
        "docling": False,
        "docx": False,
        "xlsx": False,
        "markdown": False,
        "mineru": False,
        "marker": False,
    }

    def fake_ensure(parser_name, policy):
        assert policy == "fallback"
        return DependencyResult(
            available=available[parser_name],
            reason=None if available[parser_name] else f"{parser_name} missing",
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main, "ensure_parser_dependency", fake_ensure)

    result = CliRunner().invoke(main.app, ["setup", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "NOT_READY"
    assert payload["project"]["initialized"] is False
    assert payload["next_steps"][0]["commands"] == ["docgraph init"]
    assert payload["next_steps"][1]["commands"] == ["docgraph build"]
    assert any(
        row["parser"] == "docx" and row["role"] == "required"
        for row in payload["parsers"]
    )
    assert any(row["parser"] == "docling" and not row["available"] for row in payload["parsers"])


def test_setup_command_reports_llm_and_vlm_configuration(monkeypatch, tmp_path):
    from docgraph.cli import main
    from docgraph.core.config import DocGraphConfig
    from docgraph.core.dependencies import DependencyResult

    cfg = DocGraphConfig.model_validate(
        {
            "llm": {
                "enabled": True,
                "provider": "openai_compat",
                "providers": {
                    "openai_compat": {
                        "api_key_env": "MISSING_TEST_LLM_KEY",
                        "base_url_env": "OPENAI_BASE_URL",
                    }
                },
            }
        }
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MISSING_TEST_LLM_KEY", raising=False)
    monkeypatch.setattr(main, "load_config", lambda _root: cfg)
    monkeypatch.setattr(
        main,
        "ensure_parser_dependency",
        lambda _parser_name, _policy: DependencyResult(available=True),
    )

    result = CliRunner().invoke(main.app, ["setup", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["llm"]["enabled"] is True
    assert payload["llm"]["available"] is False
    assert payload["vlm"]["enabled"] is True
    assert payload["vlm"]["available"] is False
    assert any("export MISSING_TEST_LLM_KEY=..." in step["commands"] for step in payload["next_steps"])
    assert any("export VLM_API_KEY=..." in step["commands"] for step in payload["next_steps"])


def test_setup_command_gives_copyable_embedding_install_command(monkeypatch, tmp_path):
    from docgraph.cli import main
    from docgraph.core.config import DocGraphConfig
    from docgraph.core.dependencies import DependencyResult

    cfg = DocGraphConfig.model_validate({"embeddings": {"provider": "bge_m3"}})

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main, "load_config", lambda _root: cfg)
    monkeypatch.setattr(
        main,
        "ensure_parser_dependency",
        lambda _parser_name, _policy: DependencyResult(available=True),
    )
    monkeypatch.setattr(main.importlib.util, "find_spec", lambda _name: None)

    result = CliRunner().invoke(main.app, ["setup", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    commands = [command for step in payload["next_steps"] for command in step["commands"]]
    assert "uv sync --extra embeddings" in commands


def test_setup_text_output_escapes_extra_install_command(monkeypatch, tmp_path):
    from docgraph.cli import main
    from docgraph.core.config import DocGraphConfig
    from docgraph.core.dependencies import DependencyResult

    cfg = DocGraphConfig.model_validate({"embeddings": {"provider": "bge_m3"}})

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main, "load_config", lambda _root: cfg)
    monkeypatch.setattr(
        main,
        "ensure_parser_dependency",
        lambda _parser_name, _policy: DependencyResult(available=True),
    )
    monkeypatch.setattr(main.importlib.util, "find_spec", lambda _name: None)

    result = CliRunner().invoke(main.app, ["setup"])

    assert result.exit_code == 0
    assert "uv sync --extra embeddings" in result.output
