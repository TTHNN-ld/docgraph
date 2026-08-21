from __future__ import annotations

import pytest

from docgraph.core.config import ParserFormatConfig


def test_parser_device_validator_accepts_known_and_rejects_unknown():
    assert ParserFormatConfig(primary="mineru", device="cpu").device == "cpu"
    assert ParserFormatConfig(primary="mineru", device="MPS").device == "mps"  # normalized
    assert ParserFormatConfig(primary="mineru", device="cuda").device == "cuda"
    with pytest.raises(ValueError):
        ParserFormatConfig(primary="mineru", device="tpu")
    assert ParserFormatConfig(primary="mineru").device == "cpu"  # default
def test_mineru_remote_model_config_validates():
    cfg = ParserFormatConfig.model_validate(
        {
            "primary": "mineru",
            "mineru": {
                "backend": "vlm-http-client",
                "model_server_url": "http://gpu-server:30000",
                "model": "MinerU2.5-2509-1.2B",
            },
        }
    )

    assert cfg.mineru.backend == "vlm-http-client"
    assert cfg.mineru.model_server_url == "http://gpu-server:30000"
    assert cfg.mineru.model == "MinerU2.5-2509-1.2B"


def test_mineru_config_rejects_unknown_backend():
    with pytest.raises(ValueError):
        ParserFormatConfig.model_validate(
            {"primary": "mineru", "mineru": {"backend": "sglang-client"}}
        )
