"""LLM 客户端 —— DocGraph 的统一 LLM 入口。

支持的 provider:
- anthropic     : Claude 系列
- openai        : OpenAI 官方
- openai_compat : 任何 OpenAI 兼容端点（火山方舟、DeepSeek、Together、Groq 等）
- null          : 无 LLM 模式

设计要点：
- Schema-forced JSON 输出（用 prompt + JSON 解析双保险）
- 文件级缓存（call_hash → JSON）
- Token / cost 追踪
- Tier 路由（fast / balanced / accurate → 具体模型名）
"""
from __future__ import annotations

import json
import os
import re
import signal
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from docgraph.core.ids import content_hash

# ---------------------------------------------------------------------------
# 模型价格表（USD per 1M tokens）。粗略估算，社区可 PR 校准。
# ---------------------------------------------------------------------------

_COST_TABLE: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-8": (15.0, 75.0),
    # OpenAI
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4-turbo": (10.0, 30.0),
    # DeepSeek
    "deepseek-chat": (0.27, 1.1),
    "deepseek-reasoner": (0.55, 2.19),
    # 火山方舟 doubao 系列（公开报价，估算）
    "doubao-pro-32k": (0.8, 2.0),
    "doubao-pro-128k": (5.0, 9.0),
    "doubao-1-5-pro-32k": (0.8, 2.0),
    # GLM free vision tier
    "GLM-4.6V-Flash": (0.0, 0.0),
    # 其它未知模型默认中等价位
}


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    rate_in, rate_out = _COST_TABLE.get(model, (1.0, 3.0))
    return (tokens_in / 1_000_000) * rate_in + (tokens_out / 1_000_000) * rate_out


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


@dataclass
class LLMResponse:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    cache_hit: bool = False


# ---------------------------------------------------------------------------
# Provider 接口
# ---------------------------------------------------------------------------


class LLMProvider(Protocol):
    name: str

    def complete(
        self,
        prompt: str,
        *,
        model: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> LLMResponse: ...


class NullLLMProvider:
    name = "null"

    def complete(self, *args, **kwargs) -> LLMResponse:
        raise RuntimeError(
            "LLM is disabled (llm.enabled=false in config). "
            "Set llm.enabled=true and configure provider to use LLM extractors."
        )


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        api_key_env: str = "ANTHROPIC_API_KEY",
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.api_key_env = api_key_env
        self.base_url = base_url
        self.api_key = api_key or os.environ.get(api_key_env)
        self._client = None

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        if not self.api_key:
            raise RuntimeError(
                f"AnthropicProvider needs {self.api_key_env}; set the env var."
            )
        try:
            from anthropic import Anthropic  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "anthropic package not installed. "
                "Install with: pip install 'docgraph[llm]'"
            ) from e
        kwargs = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        self._client = Anthropic(**kwargs)

    def complete(
        self,
        prompt: str,
        *,
        model: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> LLMResponse:
        self._ensure_client()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        msg = self._client.messages.create(**kwargs)
        text = ""
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                text += block.text
        usage = getattr(msg, "usage", None)
        ti = getattr(usage, "input_tokens", 0) if usage else 0
        to = getattr(usage, "output_tokens", 0) if usage else 0
        return LLMResponse(
            text=text, model=model, tokens_in=ti, tokens_out=to,
            cost_usd=estimate_cost(model, ti, to),
        )


# ---------------------------------------------------------------------------
# OpenAI / OpenAI-compatible provider
# ---------------------------------------------------------------------------


class OpenAICompatProvider:
    """OpenAI SDK 兼容 provider —— 用于 OpenAI 官方 / 火山方舟 / DeepSeek /
    Together / Groq / vLLM / Ollama 等所有 OpenAI 兼容端点。
    """

    def __init__(
        self,
        *,
        name: str = "openai",
        api_key_env: str = "OPENAI_API_KEY",
        api_key: str | None = None,
        base_url_env: str | None = "OPENAI_BASE_URL",
        base_url: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self.name = name
        self.api_key_env = api_key_env
        self.api_key = api_key or os.environ.get(api_key_env)
        self.timeout_s = timeout_s
        # 显式 base_url 优先，否则环境变量
        if base_url:
            self.base_url = base_url
        elif base_url_env:
            self.base_url = os.environ.get(base_url_env)
        else:
            self.base_url = None
        self._client = None

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        if not self.api_key:
            raise RuntimeError(
                f"{self.name} provider needs {self.api_key_env}; set the env var."
            )
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "openai package not installed. Install: pip install openai"
            ) from e
        kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        timeout_s = self.timeout_s
        if timeout_s is None:
            raw_timeout = os.environ.get("DOCGRAPH_LLM_TIMEOUT_S", "60")
            try:
                timeout_s = float(raw_timeout)
            except ValueError:
                timeout_s = 60.0
        kwargs["timeout"] = timeout_s
        self._client = OpenAI(**kwargs)

    def complete(
        self,
        prompt: str,
        *,
        model: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        system: str | None = None,
        extra_body: dict | None = None,
    ) -> LLMResponse:
        self._ensure_client()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        # DeepSeek V4 等推理模型支持 extra_body={"enable_thinking": False} 关推理，
        # 大幅降低延迟和 token 消耗（推理不再吃光 max_tokens 导致 content 空）。
        if extra_body:
            kwargs["extra_body"] = extra_body
        resp = self._client.chat.completions.create(**kwargs)
        text = (resp.choices[0].message.content or "") if resp.choices else ""
        usage = getattr(resp, "usage", None)
        ti = getattr(usage, "prompt_tokens", 0) if usage else 0
        to = getattr(usage, "completion_tokens", 0) if usage else 0
        return LLMResponse(
            text=text, model=model, tokens_in=ti, tokens_out=to,
            cost_usd=estimate_cost(model, ti, to),
        )


def make_provider(name: str, **kwargs: Any) -> LLMProvider:
    """provider 工厂。

    name:
      anthropic | openai | openai_compat | null
    """
    if name == "anthropic":
        return AnthropicProvider(**kwargs)
    if name in ("openai", "openai_compat", "volces", "deepseek", "ark"):
        # openai_compat / volces / deepseek 都走 OpenAI SDK
        kwargs.setdefault("name", name)
        return OpenAICompatProvider(**kwargs)
    if name == "null":
        return NullLLMProvider()
    raise ValueError(f"Unknown LLM provider: {name}")


# ---------------------------------------------------------------------------
# Cost tracker
# ---------------------------------------------------------------------------


@dataclass
class CostTracker:
    total_calls: int = 0
    cache_hits: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    by_extractor: dict[str, dict[str, float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def check_budget_exceeded(self, budget_usd: float | None) -> bool:
        if budget_usd is None:
            return False
        with self._lock:
            return self.cost_usd >= budget_usd

    def record(self, resp: LLMResponse, extractor: str = "_") -> None:
        # 并发安全：LLM/VLM 调用并发时多线程会同时 record
        with self._lock:
            self.total_calls += 1
            if resp.cache_hit:
                self.cache_hits += 1
            self.tokens_in += resp.tokens_in
            self.tokens_out += resp.tokens_out
            self.cost_usd += resp.cost_usd
            b = self.by_extractor.setdefault(
                extractor,
                {"calls": 0, "cache_hits": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0},
            )
            b["calls"] += 1
            if resp.cache_hit:
                b["cache_hits"] += 1
            b["tokens_in"] += resp.tokens_in
            b["tokens_out"] += resp.tokens_out
            b["cost_usd"] += resp.cost_usd


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------


class BudgetExceeded(RuntimeError):
    pass


def _llm_timeout_s() -> float:
    raw_timeout = os.environ.get("DOCGRAPH_LLM_TIMEOUT_S", "60")
    try:
        return float(raw_timeout)
    except ValueError:
        return 60.0


@contextmanager
def _llm_deadline(timeout_s: float):
    """Hard deadline for blocking SDK calls (main-thread only).

    Some OpenAI-compatible endpoints can block inside SSL reads despite SDK
    timeout settings. On POSIX/main-thread builds, SIGALRM gives the extractor a
    last-resort escape hatch so L2 failures do not stall L0/L1 ingestion.

    注意：SIGALRM 仅在主线程生效。并发 LLM 调用（map_concurrent via
    ThreadPoolExecutor）的 worker 线程不触发此 deadline，依赖 SDK 层 timeout
    和 DOCGRAPH_LLM_TIMEOUT_S 的顶层超时。
    """
    if timeout_s <= 0 or threading.current_thread() is not threading.main_thread():
        yield
        return

    def _raise_timeout(signum, frame):
        raise TimeoutError(f"LLM request exceeded {timeout_s:.1f}s")

    old_handler = signal.getsignal(signal.SIGALRM)
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout_s)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *old_timer)
        signal.signal(signal.SIGALRM, old_handler)


class LLMClient:
    def __init__(
        self,
        provider: LLMProvider,
        tiers: dict[str, str],
        *,
        cache_dir: Path | None = None,
        tracker: CostTracker | None = None,
        budget_usd: float | None = None,
        prompt_version: str = "v1",
        max_retries: int = 2,
        retry_backoff_s: float = 1.0,
    ) -> None:
        self.provider = provider
        self.tiers = tiers
        self.cache_dir = cache_dir
        self.tracker = tracker or CostTracker()
        self.budget_usd = budget_usd
        self.prompt_version = prompt_version
        self.max_retries = max_retries
        self.retry_backoff_s = retry_backoff_s
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def resolve_model(self, tier: str) -> str:
        return self.tiers.get(tier, tier)

    def _cache_path(self, key: str) -> Path | None:
        if self.cache_dir is None:
            return None
        d = self.cache_dir / key[:2]
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{key}.json"

    def _read_cache(self, key: str) -> dict[str, Any] | None:
        p = self._cache_path(key)
        if p is None or not p.is_file():
            return None
        try:
            return json.loads(p.read_text("utf-8"))
        except Exception:
            return None

    def _write_cache(self, key: str, payload: dict[str, Any]) -> None:
        p = self._cache_path(key)
        if p is None:
            return
        try:
            p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def complete(
        self,
        prompt: str,
        *,
        tier: str = "balanced",
        max_tokens: int = 2048,
        temperature: float = 0.0,
        system: str | None = None,
        extractor: str = "_",
        extra_body: dict | None = None,
    ) -> LLMResponse:
        if self.tracker.check_budget_exceeded(self.budget_usd):
            raise BudgetExceeded(
                f"LLM budget {self.budget_usd:.2f} USD already exhausted "
                f"(spent {self.tracker.cost_usd:.4f})."
            )

        model = self.resolve_model(tier)
        # extra_body 影响推理模式等行为，必须进 cache_key，否则不同 thinking 配置会撞缓存。
        extra_key = ""
        if extra_body:
            extra_key = "|" + json.dumps(extra_body, sort_keys=True, ensure_ascii=False)
        cache_key = content_hash(
            f"{self.prompt_version}|{model}|{temperature}|{system or ''}|{prompt}{extra_key}"
        ).split(":", 1)[-1][:32]

        cached = self._read_cache(cache_key)
        # 不复用空响应缓存（推理模型截断/瞬时空返回不该被缓存毒化后续调用）
        if cached and (cached.get("text", "") or "").strip():
            resp = LLMResponse(
                text=cached.get("text", ""),
                model=model,
                tokens_in=cached.get("tokens_in", 0),
                tokens_out=cached.get("tokens_out", 0),
                cost_usd=0.0,
                cache_hit=True,
            )
            self.tracker.record(resp, extractor=extractor)
            return resp

        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with _llm_deadline(_llm_timeout_s()):
                    resp = self.provider.complete(
                        prompt,
                        model=model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=system,
                        extra_body=extra_body,
                    )
                # 只缓存非空响应，避免空返回毒化缓存
                if (resp.text or "").strip():
                    self._write_cache(
                        cache_key,
                        {
                            "text": resp.text,
                            "tokens_in": resp.tokens_in,
                            "tokens_out": resp.tokens_out,
                            "model": resp.model,
                        },
                    )
                self.tracker.record(resp, extractor=extractor)
                return resp
            except Exception as e:
                last_err = e
                if isinstance(e, TimeoutError):
                    raise
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff_s * (2**attempt))
                    continue
                raise
        raise last_err  # type: ignore

    def json(
        self,
        prompt: str,
        *,
        schema: type,
        tier: str = "balanced",
        max_tokens: int = 2048,
        temperature: float = 0.0,
        system: str | None = None,
        extractor: str = "_",
        require_pydantic: bool = True,
        extra_body: dict | None = None,
    ) -> Any:
        from pydantic import BaseModel, ValidationError

        if require_pydantic and not (
            isinstance(schema, type) and issubclass(schema, BaseModel)
        ):
            raise TypeError("schema must be a Pydantic BaseModel subclass")

        schema_json = schema.model_json_schema() if require_pydantic else {}
        sys_prompt = (
            (system or "")
            + "\n\n你必须返回**唯一一个 JSON 对象**，不要带 markdown 代码块、不要解释文字。"
            + "JSON 必须严格匹配以下 schema：\n"
            + json.dumps(schema_json, ensure_ascii=False)
        )

        last_err: Exception | None = None
        cur_prompt = prompt
        for _attempt in range(self.max_retries + 1):
            resp = self.complete(
                cur_prompt,
                tier=tier,
                max_tokens=max_tokens,
                temperature=temperature,
                system=sys_prompt,
                extractor=extractor,
                extra_body=extra_body,
            )
            try:
                data = _extract_json(resp.text)
                if require_pydantic:
                    return schema.model_validate(data)
                return data
            except (json.JSONDecodeError, ValidationError, ValueError) as e:
                last_err = e
                cur_prompt = (
                    prompt
                    + "\n\n⚠️ 上次输出无法解析为合规 JSON。请重新输出，严格遵守 schema。"
                )
                continue
        raise ValueError(
            f"LLM did not produce valid JSON after {self.max_retries + 1} tries: {last_err}"
        )


# ---------------------------------------------------------------------------
# JSON 提取（兼容 LLM 加 fence / 多余前后文）
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty LLM response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 尝试提取 ```json ... ``` fence（括号计数，支持嵌套）
    fence = _extract_fenced_json(text)
    if fence is not None:
        return fence
    # fallback: 找第一个 { 到最后一个 }（简单的 brace-counting）
    obj = _extract_balanced(text, "{", "}")
    if obj is not None:
        return json.loads(obj)
    raise ValueError("no JSON object found in LLM response")


def _extract_fenced_json(text: str) -> Any | None:
    """从 ```json ... ``` 中提取 JSON，支持嵌套。"""
    m = re.search(r"```(?:json)?\s*", text)
    if not m:
        return None
    start = m.end()
    end = text.find("```", start)
    if end < 0:
        return None
    inner = text[start:end].strip()
    try:
        return json.loads(inner)
    except json.JSONDecodeError:
        # 可能 fence 内有散文前缀，试用 brace-counting
        obj = _extract_balanced(inner, "{", "}")
        if obj is not None:
            return json.loads(obj)
    return None


def _extract_balanced(text: str, opener: str, closer: str) -> str | None:
    """用括号计数提取从第一个 opener 配对的 closer 之间的文本。"""
    start = text.find(opener)
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None
