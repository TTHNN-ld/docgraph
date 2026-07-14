from __future__ import annotations

from types import SimpleNamespace


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
    assert commands[0][1:4] == ["-m", "pip", "install"]
    assert "-e" in commands[0]
    assert commands[0][-1].endswith("[docling]")


def test_unknown_plugin_is_never_auto_installed(monkeypatch):
    from docgraph.core import dependencies

    monkeypatch.setattr(
        dependencies.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pip called")),
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
    from docgraph.core.config import DocGraphConfig

    runtime = DocGraphConfig().runtime
    assert runtime.dependency_policy == "prompt"
    assert runtime.parser_failure == "fallback"
