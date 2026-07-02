"""Embedding 构造工厂 —— 从 config 解析，含 fallback。"""
from __future__ import annotations

from docgraph.core.config import EmbeddingsConfig
from docgraph.core.logger import get_logger
from docgraph.embeddings.base import EmbeddingProvider
from docgraph.embeddings.hash_encoder import BgeM3Encoder, HashEncoder

log = get_logger(__name__)


def build_encoder(cfg: EmbeddingsConfig) -> EmbeddingProvider:
    """从 EmbeddingsConfig 构造 encoder，失败时降级到 HashEncoder。"""
    name = cfg.provider
    try:
        if name == "hash":
            return HashEncoder(dim=cfg.dim or 256)
        if name == "bge_m3":
            return BgeM3Encoder(
                model_name=cfg.model or "BAAI/bge-m3",
                dim=cfg.dim or 1024,
            )
        if name in ("openai", "openai_compat"):
            from docgraph.embeddings.openai_encoder import (
                try_make_openai_embedding,
            )
            enc = try_make_openai_embedding(
                model=cfg.model or "text-embedding-3-small",
                dim=cfg.dim or 1536,
                api_key=cfg.api_key,
                api_key_env=cfg.api_key_env,
                api_key_fallback_env=cfg.api_key_fallback_env,
                base_url=cfg.base_url,
                base_url_env=cfg.base_url_env,
                base_url_fallback_env=cfg.base_url_fallback_env,
            )
            if enc is None:
                log.warning(
                    f"[embed] {name} encoder unavailable (no API key); "
                    f"falling back to hash encoder"
                )
                return HashEncoder(dim=256)
            return enc
    except Exception as e:
        log.warning(f"[embed] {name} init failed ({e}); falling back to hash")
    return HashEncoder(dim=256)
