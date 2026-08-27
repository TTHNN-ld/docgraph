"""M6 VLM fast-fail 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest


class AlwaysFailProvider:
    name = "fail"
    calls = 0

    def describe(self, image_path: Path, prompt: str, **kwargs):
        self.calls += 1
        raise RuntimeError("vision not supported")


class AlwaysOkProvider:
    name = "ok"
    calls = 0

    def describe(self, image_path: Path, prompt: str, **kwargs):
        from docgraph.llm.client import LLMResponse

        self.calls += 1
        return LLMResponse(text='{"ok": true}', model=kwargs.get("model", "m"))


def test_vlm_client_disables_after_repeated_failures(tmp_path):
    from docgraph.llm.vlm import VLMClient

    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    provider = AlwaysFailProvider()
    client = VLMClient(
        provider,
        model="vision-model",
        cache_dir=tmp_path / "cache",
        max_retries=0,
        disable_after_failures=2,
    )

    with pytest.raises(RuntimeError, match="vision not supported"):
        client.describe(img, "describe")
    assert client.disabled is False

    with pytest.raises(RuntimeError, match="vision not supported"):
        client.describe(img, "describe 2")
    assert client.disabled is True

    # 第三次不再打 provider，快速失败
    before = provider.calls
    with pytest.raises(RuntimeError, match="disabled after repeated failures"):
        client.describe(img, "describe 3")
    assert provider.calls == before


def test_vlm_client_success_resets_failure_count(tmp_path):
    from docgraph.llm.vlm import VLMClient

    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    provider = AlwaysOkProvider()
    client = VLMClient(provider, model="vision-model", cache_dir=tmp_path / "cache")
    resp = client.describe(img, "describe")
    assert resp.text == '{"ok": true}'
    assert client.disabled is False


def test_vlm_client_cache_hit_does_not_call_provider(tmp_path):
    from docgraph.llm.vlm import VLMClient

    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    provider = AlwaysOkProvider()
    client = VLMClient(provider, model="vision-model", cache_dir=tmp_path / "cache")

    r1 = client.describe(img, "same", cache_key_extra="a")
    calls_after_first = provider.calls
    r2 = client.describe(img, "same", cache_key_extra="a")

    assert calls_after_first >= 1
    assert provider.calls == calls_after_first
    assert r2.cache_hit is True
    assert r1.text == r2.text


def test_vlm_cache_is_available_after_provider_has_been_disabled(tmp_path):
    from docgraph.llm.vlm import VLMClient

    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    provider = AlwaysOkProvider()
    client = VLMClient(provider, model="vision-model", cache_dir=tmp_path / "cache")
    client.describe(img, "same", cache_key_extra="a")
    calls_after_first = provider.calls
    client._disabled = True

    cached = client.describe(img, "same", cache_key_extra="a")

    assert cached.cache_hit is True
    assert provider.calls == calls_after_first


def test_vlm_cache_key_includes_prompt(tmp_path):
    from docgraph.llm.vlm import VLMClient

    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    provider = AlwaysOkProvider()
    client = VLMClient(provider, model="vision-model", cache_dir=tmp_path / "cache")

    client.describe(img, "first prompt")
    calls_after_first = provider.calls
    response = client.describe(img, "different prompt")

    assert response.cache_hit is False
    assert provider.calls == calls_after_first + 1


def test_vlm_client_respects_shared_build_budget(tmp_path):
    from docgraph.llm.client import CostTracker
    from docgraph.llm.vlm import VLMClient

    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    tracker = CostTracker()
    tracker.cost_usd = 1.0
    provider = AlwaysOkProvider()
    before = provider.calls
    client = VLMClient(
        provider,
        model="vision-model",
        tracker=tracker,
        budget_usd=0.5,
    )

    with pytest.raises(RuntimeError, match=r"budget .* exhausted"):
        client.describe(img, "describe")
    assert provider.calls == before
