from __future__ import annotations

import pytest

from docgraph.core.config import ParserFormatConfig
from docgraph.parsers.base import ParseContext
from docgraph.parsers.mineru_parser import _resolve_device


def test_parser_device_validator_accepts_known_and_rejects_unknown():
    assert ParserFormatConfig(primary="mineru", device="cpu").device == "cpu"
    assert ParserFormatConfig(primary="mineru", device="MPS").device == "mps"  # normalized
    assert ParserFormatConfig(primary="mineru", device="cuda").device == "cuda"
    with pytest.raises(ValueError):
        ParserFormatConfig(primary="mineru", device="tpu")
    assert ParserFormatConfig(primary="mineru").device == "cpu"  # default


def test_resolve_device_defaults_to_cpu():
    # no env, no config option
    assert _resolve_device(ParseContext(doc_id="d")) == "cpu"


def test_resolve_device_reads_config_option():
    ctx = ParseContext(doc_id="d", options={"device": "mps"})
    assert _resolve_device(ctx) == "mps"


def test_resolve_device_env_overrides_config(monkeypatch):
    monkeypatch.setenv("DOCGRAPH_MINERU_DEVICE", "cpu")
    ctx = ParseContext(doc_id="d", options={"device": "mps"})
    assert _resolve_device(ctx) == "cpu"  # env wins


def test_resolve_device_ignores_garbage(monkeypatch):
    monkeypatch.delenv("DOCGRAPH_MINERU_DEVICE", raising=False)
    ctx = ParseContext(doc_id="d", options={"device": "quantum"})
    assert _resolve_device(ctx) == "cpu"  # falls through to default
