"""Embedding provider construction and query-time degradation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docgraph.core.config import EmbeddingsConfig, StorageConfig
from docgraph.core.logger import get_logger
from docgraph.embeddings.base import EmbeddingProvider
from docgraph.embeddings.hash_encoder import BgeM3Encoder, HashEncoder
from docgraph.embeddings.vector_factory import build_vector_store

log = get_logger(__name__)


def embeddings_enabled(cfg: EmbeddingsConfig) -> bool:
    return (cfg.provider or "none").strip().lower() not in {"none", "off", "disabled"}


def build_encoder(cfg: EmbeddingsConfig) -> EmbeddingProvider:
    """Build exactly the configured provider; never silently change semantics."""
    name = (cfg.provider or "none").strip().lower()
    if not embeddings_enabled(cfg):
        raise RuntimeError("embedding is disabled")
    if name == "hash":
        return HashEncoder(dim=cfg.dim or 256)
    if name == "bge_m3":
        return BgeM3Encoder(
            model_name=cfg.model or "BAAI/bge-m3",
            dim=cfg.dim or 1024,
        )
    if name in ("openai", "openai_compat"):
        from docgraph.embeddings.openai_encoder import try_make_openai_embedding

        encoder = try_make_openai_embedding(
            model=cfg.model or "text-embedding-3-small",
            dim=cfg.dim or 1536,
            api_key=cfg.api_key,
            api_key_env=cfg.api_key_env,
            api_key_fallback_env=cfg.api_key_fallback_env,
            base_url=cfg.base_url,
            base_url_env=cfg.base_url_env,
            base_url_fallback_env=cfg.base_url_fallback_env,
        )
        if encoder is None:
            raise RuntimeError(f"{name} embedding requires an API key")
        return encoder
    raise ValueError(f"unknown embedding provider: {name}")


def open_query_embeddings(
    embedding_cfg: EmbeddingsConfig,
    storage_cfg: StorageConfig,
    docgraph_dir: Path,
) -> tuple[Any | None, EmbeddingProvider | None]:
    """Open query-time vector components, degrading explicitly to lexical search."""
    if not embeddings_enabled(embedding_cfg):
        return None, None
    vector_store = None
    try:
        vector_store = build_vector_store(storage_cfg, docgraph_dir, create=False)
        if vector_store is None:
            return None, None
        vector_store.init_schema()
        return vector_store, build_encoder(embedding_cfg)
    except Exception as exc:
        if vector_store is not None:
            vector_store.close()
        log.warning(f"[embed] semantic retrieval unavailable: {exc}; using text retrieval")
        return None, None
