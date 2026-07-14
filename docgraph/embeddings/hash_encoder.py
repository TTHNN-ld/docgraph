"""HashEncoder —— 零依赖的最小可用嵌入。

用于：
- 离线场景 / CI / 单元测试
- 用户没装 sentence-transformers 时也能跑通整条流水线

方法：
- 用滚动 hash 把 token 落到固定维度的 bucket
- L2 normalize
- **不是真正的语义嵌入**——只能粗略匹配相同/相似词，但能验证 store + retrieval 联通。
"""
from __future__ import annotations

import hashlib
import math
import os
import re

from docgraph.embeddings.base import EmbeddingProvider

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _pick_device() -> str:
    """Choose the torch device for bge-m3 inference.

    Priority: explicit DOCGRAPH_BGE_DEVICE env > mps (Apple Silicon) > cuda > cpu.
    On a CPU-only build this stays on cpu; the env override lets users force a
    device if autodetection misbehaves.
    """
    forced = os.environ.get("DOCGRAPH_BGE_DEVICE", "").strip().lower()
    if forced:
        return forced
    try:
        import torch  # type: ignore

        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


class HashEncoder:
    name = "hash"

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim
        self.model = f"hash-{dim}"

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [self._encode_one(t) for t in texts]

    def _encode_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in _TOKEN_RE.findall((text or "").lower()):
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest()[:8], 16)
            sign = 1 if (h & 1) else -1
            vec[h % self.dim] += sign * 1.0
        # L2 normalize
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class BgeM3Encoder:
    """bge-m3 适配（按需 import sentence-transformers）。"""
    name = "bge_m3"

    def __init__(self, model_name: str = "BAAI/bge-m3", dim: int = 1024) -> None:
        self.model = model_name
        self.dim = dim
        self._model = None

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "sentence-transformers required. "
                "Install with: pip install 'docgraph[embeddings]'"
            ) from e
        self._model = SentenceTransformer(self.model, device=_pick_device())

    def encode(self, texts: list[str]) -> list[list[float]]:
        self._ensure_model()
        vecs = self._model.encode(  # type: ignore
            texts, normalize_embeddings=True, show_progress_bar=False
        )
        return [v.tolist() for v in vecs]


def make_encoder(name: str, **kwargs) -> EmbeddingProvider:
    if name == "hash":
        return HashEncoder(**kwargs)
    if name == "bge_m3":
        return BgeM3Encoder(**kwargs)
    if name in ("openai", "openai_compat"):
        from docgraph.embeddings.openai_encoder import OpenAIEmbeddingProvider

        return OpenAIEmbeddingProvider(**kwargs)
    raise ValueError(f"Unknown embedding provider: {name}")
