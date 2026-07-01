"""Vector store backend factory."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from docgraph.core.config import StorageConfig
from docgraph.embeddings.vector_store import VectorStore


def build_vector_store(cfg: StorageConfig, docgraph_dir: Path, *, create: bool = True) -> Any | None:
    """Construct the configured vector store.

    `sqlite_json` is the no-dependency local backend. `lancedb` is optional and
    selected through configuration.
    """
    backend = (cfg.vector_backend or "sqlite_json").lower()
    if backend in {"sqlite", "sqlite_json", "sqlite_vec"}:
        path = docgraph_dir / "vectors.db"
        if not create and not path.exists():
            return None
        return VectorStore(path)
    if backend == "lancedb":
        path = docgraph_dir / "vectors.lance"
        if not create and not path.exists():
            return None
        from docgraph.embeddings.lancedb_store import LanceDBVectorStore

        return LanceDBVectorStore(path)
    raise ValueError(f"Unknown vector backend: {cfg.vector_backend}")
