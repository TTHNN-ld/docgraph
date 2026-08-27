"""让 LLMClient 通过多模态模型分析图片。

支持能力：
- 接入 OpenAI 兼容视觉模型（Qwen-VL / GLM-4V / GPT-4o / Doubao Vision 等）
- 保留 Anthropic Claude vision
- 统一 base64 + image_url 两种 payload 编码
- 不同 provider 的 vision payload 格式由 _build_messages 路由
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from docgraph.core.ids import content_hash, file_hash
from docgraph.core.logger import get_logger
from docgraph.llm.client import (
    AnthropicProvider,
    BudgetExceeded,
    CostTracker,
    LLMResponse,
    OpenAICompatProvider,
    _llm_deadline,
    _llm_timeout_s,
    estimate_cost,
)

log = get_logger(__name__)


def _vlm_timeout_s() -> float:
    raw_timeout = os.environ.get("DOCGRAPH_VLM_TIMEOUT_S")
    if raw_timeout:
        try:
            return float(raw_timeout)
        except ValueError:
            pass
    return _llm_timeout_s()


@dataclass
class VLMRequest:
    image_path: Path
    prompt: str
    figure_type: str | None = None


class VLMProvider(Protocol):
    name: str

    def describe(
        self,
        image_path: Path,
        prompt: str,
        *,
        model: str,
        max_tokens: int = 1500,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> LLMResponse: ...


# ---------------------------------------------------------------------------
# Anthropic vision
# ---------------------------------------------------------------------------


class AnthropicVLMProvider:
    """复用 Anthropic SDK 的视觉能力。"""

    name = "anthropic"

    def __init__(
        self,
        api_key_env: str = "ANTHROPIC_API_KEY",
        api_key: str | None = None,
    ) -> None:
        self._inner = AnthropicProvider(api_key_env=api_key_env, api_key=api_key)

    def describe(
        self,
        image_path: Path,
        prompt: str,
        *,
        model: str,
        max_tokens: int = 1500,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> LLMResponse:
        client = self._inner._ensure_client()
        img_bytes = Path(image_path).read_bytes()
        b64 = base64.b64encode(img_bytes).decode("ascii")
        media_type = _guess_media_type(image_path)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }
        if system:
            kwargs["system"] = system
        msg = client.messages.create(**kwargs)
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        usage = getattr(msg, "usage", None)
        ti = getattr(usage, "input_tokens", 0) if usage else 0
        to = getattr(usage, "output_tokens", 0) if usage else 0
        return LLMResponse(
            text=text,
            model=model,
            tokens_in=ti,
            tokens_out=to,
            cost_usd=estimate_cost(model, ti, to),
        )


# ---------------------------------------------------------------------------
# OpenAI compatible vision
# ---------------------------------------------------------------------------


class OpenAICompatVLMProvider:
    """OpenAI 兼容视觉端点 —— Qwen-VL / GLM-4V / GPT-4o / Doubao Vision 等。

    payload 编码：
    - base64 模式（默认）：`data:image/png;base64,XXX` 作为 image_url
    - 大多数 OpenAI 兼容端点支持此格式
    - 默认使用 OpenAI SDK；`DOCGRAPH_VLM_TRANSPORT=http` 可切到内置 HTTP
      fallback，便于诊断 provider SDK 兼容问题。
    """

    def __init__(
        self,
        *,
        name: str = "openai_compat",
        api_key_env: str = "OPENAI_API_KEY",
        api_key: str | None = None,
        base_url_env: str | None = "OPENAI_BASE_URL",
        base_url: str | None = None,
    ) -> None:
        self.name = name
        self.api_key_env = api_key_env
        self.api_key = api_key
        self.base_url_env = base_url_env
        self.base_url = base_url
        self._inner = OpenAICompatProvider(
            name=name,
            api_key_env=api_key_env,
            api_key=api_key,
            base_url_env=base_url_env,
            base_url=base_url,
        )

    def describe(
        self,
        image_path: Path,
        prompt: str,
        *,
        model: str,
        max_tokens: int = 1500,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> LLMResponse:
        img_bytes = Path(image_path).read_bytes()
        b64 = base64.b64encode(img_bytes).decode("ascii")
        media_type = _guess_media_type(image_path)
        data_url = f"data:{media_type};base64,{b64}"

        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        )

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        extra_body = _vlm_extra_body(model)
        if os.environ.get("DOCGRAPH_VLM_TRANSPORT", "sdk").lower() == "http":
            resp = self._http_chat_completion({**payload, **extra_body})
        else:
            resp = self._sdk_chat_completion(payload, extra_body=extra_body)
        choice = (resp.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text = message.get("content") or ""
        usage = resp.get("usage") or {}
        ti = usage.get("prompt_tokens", 0)
        to = usage.get("completion_tokens", 0)
        return LLMResponse(
            text=text,
            model=resp.get("model") or model,
            tokens_in=ti,
            tokens_out=to,
            cost_usd=estimate_cost(model, ti, to),
        )

    def _sdk_chat_completion(
        self,
        payload: dict[str, Any],
        *,
        extra_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._inner._ensure_client()
        kwargs: dict[str, Any] = {**payload, "timeout": _vlm_timeout_s()}
        if extra_body:
            kwargs["extra_body"] = extra_body
        resp = client.chat.completions.create(**kwargs)
        choice = resp.choices[0] if resp.choices else None
        message = getattr(choice, "message", None) if choice else None
        usage = getattr(resp, "usage", None)
        return {
            "model": getattr(resp, "model", None) or payload.get("model"),
            "choices": [
                {
                    "message": {
                        "content": getattr(message, "content", "") if message else "",
                    },
                }
            ],
            "usage": {
                "prompt_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
                "completion_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
            },
        }

    def _http_chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        api_key = self.api_key or os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(f"{self.api_key_env} not set")
        base_url = self.base_url or (
            os.environ.get(self.base_url_env) if self.base_url_env else None
        )
        if not base_url:
            raise RuntimeError(f"{self.base_url_env or 'base_url'} not set")
        endpoint = _chat_completions_url(base_url)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=_vlm_timeout_s()) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"VLM HTTP {e.code}: {detail}") from e


def _chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _vlm_extra_body(model: str) -> dict[str, Any]:
    raw = os.environ.get("DOCGRAPH_VLM_EXTRA_BODY")
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            log.warning("[vlm] DOCGRAPH_VLM_EXTRA_BODY is not valid JSON; ignored")
    if model.lower().startswith("glm-"):
        return {"thinking": {"type": "disabled"}}
    return {}


def _guess_media_type(path: Path) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(path.suffix.lower(), "image/png")


# ---------------------------------------------------------------------------
# VLMClient —— 缓存 + 成本追踪
# ---------------------------------------------------------------------------


class VLMClient:
    def __init__(
        self,
        provider: VLMProvider,
        *,
        model: str,
        cache_dir: Path | None = None,
        tracker=None,
        budget_usd: float | None = None,
        max_retries: int = 1,
        disable_after_failures: int = 2,
    ) -> None:
        self.provider = provider
        self.model = model
        self.cache_dir = cache_dir
        self.tracker = tracker or CostTracker()
        self.budget_usd = budget_usd
        self.max_retries = max_retries
        self.disable_after_failures = disable_after_failures
        self._consecutive_failures = 0
        self._disabled = False
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def disabled(self) -> bool:
        return self._disabled

    def describe(
        self,
        image_path: Path,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1500,
        cache_key_extra: str = "",
        extractor: str = "figure",
    ) -> LLMResponse:
        img_hash = file_hash(image_path).split(":", 1)[-1][:32]
        model_key = _safe_key(self.model)
        provider_name = getattr(self.provider, "name", type(self.provider).__name__)
        provider_endpoint = getattr(self.provider, "base_url", None)
        request_hash = content_hash(
            json.dumps(
                {
                    "provider": provider_name,
                    "endpoint": provider_endpoint,
                    "model": self.model,
                    "prompt": prompt,
                    "system": system,
                    "max_tokens": max_tokens,
                    "extra": cache_key_extra,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        ).split(":", 1)[-1][:32]
        cache_p = (
            self.cache_dir / f"{img_hash}.{model_key}.{request_hash}.json"
            if self.cache_dir
            else None
        )
        if cache_p and cache_p.is_file():
            try:
                cached = json.loads(cache_p.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                cached = None
            if cached and (cached.get("text") or "").strip():
                resp = LLMResponse(
                    text=cached["text"],
                    model=cached.get("model", self.model),
                    tokens_in=cached.get("tokens_in", 0),
                    tokens_out=cached.get("tokens_out", 0),
                    cost_usd=0.0,
                    cache_hit=True,
                )
                self.tracker.record(resp, extractor=extractor)
                return resp

        if self._disabled:
            raise RuntimeError(
                "VLM client disabled after repeated failures. "
                "Provider likely doesn't support vision (e.g. DeepSeek)."
            )

        estimated_tokens_in = max(1, len(prompt) // 4) + 1000
        estimated_usd = estimate_cost(self.model, estimated_tokens_in, max_tokens)
        reservation = self.tracker.reserve(self.budget_usd, estimated_usd)
        if reservation is None:
            raise BudgetExceeded(
                f"Build model budget {self.budget_usd:.2f} USD is exhausted for this request "
                f"(spent {self.tracker.cost_usd:.4f}, reserved {self.tracker.reserved_usd:.4f})."
            )

        last_err: Exception | None = None
        try:
            for _ in range(self.max_retries + 1):
                try:
                    with _llm_deadline(_vlm_timeout_s()):
                        resp = self.provider.describe(
                            image_path,
                            prompt,
                            model=self.model,
                            max_tokens=max_tokens,
                            system=system,
                        )
                    # 成功 → 重置失败计数
                    self._consecutive_failures = 0
                    if cache_p and (resp.text or "").strip():
                        cache_p.write_text(
                            json.dumps(
                                {
                                    "text": resp.text,
                                    "model": resp.model,
                                    "tokens_in": resp.tokens_in,
                                    "tokens_out": resp.tokens_out,
                                },
                                ensure_ascii=False,
                            ),
                            encoding="utf-8",
                        )
                    self.tracker.record(resp, extractor=extractor, reserved_usd=reservation)
                    reservation = 0.0
                    return resp
                except Exception as e:
                    last_err = e
                    continue
        finally:
            self.tracker.release(reservation)
        # 全部重试都失败
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.disable_after_failures:
            self._disabled = True
            log.warning(
                f"[vlm] disabling client after {self._consecutive_failures} "
                f"consecutive failures (last: {str(last_err)[:120]}). "
                f"Provider may not support vision."
            )
        raise last_err  # type: ignore


def _safe_key(s: str) -> str:
    return "".join(c for c in s if c.isalnum() or c in "_-")[:32] or "default"


def make_vlm_provider(name: str, **kwargs) -> VLMProvider:
    """工厂。

    支持的 name：
      anthropic / openai / openai_compat / volces / qwen / glm
    """
    if name == "anthropic":
        return AnthropicVLMProvider(**kwargs)
    if name in ("openai", "openai_compat", "volces", "qwen", "glm", "deepseek"):
        kwargs.setdefault("name", name)
        return OpenAICompatVLMProvider(**kwargs)
    raise ValueError(f"Unknown VLM provider: {name}")
