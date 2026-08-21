from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from docgraph.parsers.base import ParseContext
from docgraph.parsers.mineru_parser import MinerUParser


def _middle_json() -> dict:
    return {
        "pdf_info": [
            {
                "page_idx": 0,
                "preproc_blocks": [
                    {
                        "type": "text",
                        "bbox": [10, 20, 300, 60],
                        "lines": [{"spans": [{"content": "remote MinerU text"}]}],
                    }
                ],
            }
        ]
    }


def test_remote_vlm_backend_invokes_mineru_cli(monkeypatch, tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    calls: list[tuple[list[str], dict[str, str], int]] = []

    monkeypatch.setattr(
        "docgraph.parsers.mineru_parser._mineru_executable",
        lambda: "/opt/bin/mineru",
    )

    def fake_run(command, *, check, capture_output, text, env, timeout):
        assert check is False
        assert capture_output is True
        assert text is True
        output_dir = Path(command[command.index("--output") + 1])
        parse_dir = output_dir / "sample" / "vlm"
        parse_dir.mkdir(parents=True)
        (parse_dir / "sample_middle.json").write_text(
            json.dumps(_middle_json()),
            encoding="utf-8",
        )
        calls.append((command, env, timeout))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("docgraph.parsers.mineru_parser.subprocess.run", fake_run)
    ctx = ParseContext(
        doc_id="doc",
        cache_dir=tmp_path / "cache",
        options={
            "quality": "accurate",
            "mineru": {
                "backend": "vlm-http-client",
                "model_server_url": "http://gpu-server:30000",
                "model": "MinerU2.5-2509-1.2B",
                "api_key": "secret",
                "timeout_seconds": 120,
                "formula": True,
                "table": True,
                "image_analysis": True,
            },
        },
    )

    parsed = MinerUParser().parse(pdf, ctx)

    command, env, timeout = calls[0]
    assert command[command.index("--backend") + 1] == "vlm-http-client"
    assert command[command.index("--url") + 1] == "http://gpu-server:30000"
    assert "secret" not in command
    assert env["MINERU_VL_API_KEY"] == "secret"
    assert env["MINERU_VL_MODEL_NAME"] == "MinerU2.5-2509-1.2B"
    assert timeout == 150
    assert parsed.pages[0].blocks[0].text == "remote MinerU text"
    assert parsed.pages[0].blocks[0].attrs["mineru_backend"] == "vlm-http-client"


def test_remote_backend_requires_model_server_url(monkeypatch, tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.delenv("MINERU_MODEL_SERVER_URL", raising=False)

    ctx = ParseContext(
        doc_id="doc",
        cache_dir=tmp_path / "cache",
        options={"mineru": {"backend": "hybrid-http-client"}},
    )

    with pytest.raises(RuntimeError, match="model_server_url"):
        MinerUParser().parse(pdf, ctx)


def test_remote_backend_reads_server_url_from_environment(monkeypatch, tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setenv("MINERU_MODEL_SERVER_URL", "http://env-server:30000")
    monkeypatch.setattr(
        "docgraph.parsers.mineru_parser._mineru_executable",
        lambda: "/opt/bin/mineru",
    )

    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        output_dir = Path(command[command.index("--output") + 1])
        parse_dir = output_dir / "sample" / "vlm"
        parse_dir.mkdir(parents=True)
        (parse_dir / "sample_middle.json").write_text(
            json.dumps(_middle_json()),
            encoding="utf-8",
        )
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("docgraph.parsers.mineru_parser.subprocess.run", fake_run)
    MinerUParser().parse(
        pdf,
        ParseContext(
            doc_id="doc",
            cache_dir=tmp_path / "cache",
            options={"mineru": {"backend": "vlm-http-client"}},
        ),
    )

    command = commands[0]
    assert command[command.index("--url") + 1] == "http://env-server:30000"
