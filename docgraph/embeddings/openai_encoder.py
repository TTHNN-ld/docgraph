"""OpenAI 兼容 embedding provider —— 适用于 OpenAI / DeepSeek（无）/ 火山方舟 / Voyage 等。

DeepSeek 官方暂不提供 embeddings API（截至 2025-06），但火山方舟、阿里云百炼、
OpenAI 等均提供 OpenAI 兼容的 `/embeddings` 端点。

通过环境变量配置：
  EMBEDDING_BASE_URL  (优先)
  OPENAI_BASE_URL     (回退；与 chat completion 共享时)
  EMBEDDING_API_KEY   (优先)
  OPENAI_API_KEY      (回退)
"""

from __future__ import annotations

import os
from typing import Any

from docgraph.core.logger import get_logger

log = get_logger(__name__)


class OpenAIEmbeddingProvider:
    """OpenAI 兼容 embedding 适配器。"""

    name = "openai_compat"

    def __init__(
        self,
        *,
        model: str = "text-embedding-3-small",
        dim: int = 1536,
        api_key: str | None = None,
        api_key_env: str = "EMBEDDING_API_KEY",
        api_key_fallback_env: str = "OPENAI_API_KEY",
        base_url: str | None = None,
        base_url_env: str = "EMBEDDING_BASE_URL",
        base_url_fallback_env: str = "OPENAI_BASE_URL",
    ) -> None:
        self.model = model
        self.dim = dim
        self.api_key: str | None = (
            api_key or os.environ.get(api_key_env) or os.environ.get(api_key_fallback_env)
        )
        if base_url:
            self.base_url: str | None = base_url
        else:
            self.base_url = os.environ.get(base_url_env) or os.environ.get(base_url_fallback_env)
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise RuntimeError("OpenAI embedding requires EMBEDDING_API_KEY or OPENAI_API_KEY env")
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("openai package required") from e
        kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        self._client = OpenAI(**kwargs)
        return self._client

    def encode(self, texts: list[str]) -> list[list[float]]:
        client = self._ensure_client()
        # OpenAI-compatible providers differ on batch limits; Doubao currently
        # accepts at most 10 inputs per embeddings request.
        max_batch = max(1, int(os.environ.get("EMBEDDING_MAX_BATCH", "10")))
        out: list[list[float]] = []
        for i in range(0, len(texts), max_batch):
            batch = [t or " " for t in texts[i : i + max_batch]]  # 空串会被拒
            resp = client.embeddings.create(model=self.model, input=batch)
            for item in resp.data:
                out.append(list(item.embedding))
        return out


def try_make_openai_embedding(**kwargs) -> OpenAIEmbeddingProvider | None:
    """构造但延迟连接；缺少 API key 时返回 None 供上层降级为文本检索。"""
    try:
        enc = OpenAIEmbeddingProvider(**kwargs)
        if not enc.api_key:
            return None
        return enc
    except Exception as e:
        log.warning(f"[embed] OpenAI embedding init failed: {e}")
        return None
