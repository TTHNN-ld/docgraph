"""Embedding provider construction and query-time degradation."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from docgraph.core.config import DocGraphConfig, EmbeddingsConfig, StorageConfig, docgraph_dir
from docgraph.core.logger import get_logger
from docgraph.core.manifest import load_manifest
from docgraph.embeddings.base import EmbeddingProvider
from docgraph.embeddings.hash_encoder import BgeM3Encoder, HashEncoder
from docgraph.embeddings.indexer import desired_chunk_hashes, desired_node_hashes
from docgraph.embeddings.vector_factory import build_vector_store
from docgraph.graph.sqlite_store import SQLiteGraphStore

log = get_logger(__name__)
EMBEDDING_PIPELINE_VERSION = "2"


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


def expected_embedding_model(cfg: EmbeddingsConfig) -> str:
    provider = (cfg.provider or "hash").strip().lower()
    if provider == "hash":
        return f"hash-{cfg.dim or 256}"
    if provider == "bge_m3":
        return cfg.model or "BAAI/bge-m3"
    if provider in {"openai", "openai_compat"}:
        return cfg.model or "text-embedding-3-small"
    return provider


def embedding_fingerprint(
    embedding_cfg: EmbeddingsConfig,
    storage_cfg: StorageConfig,
) -> str:
    """Identify every non-secret setting that changes vector semantics."""
    provider = (embedding_cfg.provider or "none").strip().lower()
    endpoint = embedding_cfg.base_url
    if endpoint is None:
        endpoint = os.environ.get(embedding_cfg.base_url_env) or os.environ.get(
            embedding_cfg.base_url_fallback_env
        )
    payload = json.dumps(
        {
            "pipeline": EMBEDDING_PIPELINE_VERSION,
            "provider": provider,
            "model": expected_embedding_model(embedding_cfg),
            "dim": embedding_cfg.dim,
            "endpoint": endpoint if provider in {"openai", "openai_compat"} else None,
            "vector_backend": storage_cfg.vector_backend,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


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


def open_ready_query_embeddings(
    cfg: DocGraphConfig,
    root: Path,
    store: SQLiteGraphStore,
) -> tuple[Any | None, EmbeddingProvider | None, str | None]:
    """Open semantic retrieval only for a complete index built by this configuration."""
    if not embeddings_enabled(cfg.embeddings):
        return None, None, None

    state = load_manifest(root).derived.get("embedding")
    expected_fingerprint = embedding_fingerprint(cfg.embeddings, cfg.storage)
    if state is None:
        return (
            None,
            None,
            "Semantic retrieval is unavailable: run docgraph build to create vectors.",
        )
    if state.status != "ok":
        detail = f" ({state.error})" if state.error else ""
        return (
            None,
            None,
            f"Semantic retrieval is unavailable: embedding build {state.status}{detail}.",
        )
    if state.fingerprint != expected_fingerprint:
        return (
            None,
            None,
            "Semantic retrieval is unavailable: embedding configuration changed; run docgraph build.",
        )

    vstore, encoder = open_query_embeddings(cfg.embeddings, cfg.storage, docgraph_dir(root))
    if vstore is None or encoder is None:
        return None, None, "Semantic retrieval is unavailable: vector backend could not be opened."
    model = expected_embedding_model(cfg.embeddings)
    try:
        complete = vstore.stored_node_hashes(model) == desired_node_hashes(
            store
        ) and vstore.stored_item_hashes("chunk", model) == desired_chunk_hashes(store)
    except Exception as exc:
        vstore.close()
        return None, None, f"Semantic retrieval is unavailable: vector validation failed ({exc})."
    if not complete:
        vstore.close()
        return (
            None,
            None,
            "Semantic retrieval is unavailable: vector index is incomplete or stale; run docgraph build.",
        )
    return vstore, encoder, None
