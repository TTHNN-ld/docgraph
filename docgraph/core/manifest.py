"""Manifest —— 增量构建的"账本"。

存放在 `.docgraph/manifest.json`，每个被追踪的文件一条记录。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class StageRecord(BaseModel):
    duration_s: float = 0.0
    ok: bool = True
    error: str | None = None
    nodes: int = 0
    edges: int = 0
    cost_usd: float = 0.0


class DerivedStageRecord(BaseModel):
    """State for a corpus-wide, reproducible derived stage."""

    fingerprint: str | None = None
    status: str = "pending"  # pending|ok|degraded|error
    last_run: str | None = None
    error: str | None = None
    items: int = 0
    cost_usd: float = 0.0


class BuildRunRecord(BaseModel):
    status: str = "success"  # success|degraded|failed
    started_at: str | None = None
    completed_at: str | None = None
    files_total: int = 0
    files_failed: int = 0
    warnings: list[dict[str, str]] = Field(default_factory=list)
    cost_usd: float = 0.0


class FileRecord(BaseModel):
    path: str
    doc_id: str | None = None
    hash: str | None = None
    indexed_hash: str | None = None
    build_fingerprint: str | None = None
    mtime: float | None = None
    size: int | None = None
    parser: str | None = None
    parser_version: str | None = None
    requested_parser: str | None = None
    parser_attempts: list[dict[str, Any]] = Field(default_factory=list)
    quality_status: str | None = None
    fallback_reason: str | None = None
    status: str = "pending"  # pending|parsed|extracted|error
    stage_log: dict[str, StageRecord] = Field(default_factory=dict)
    last_run: str | None = None
    last_success: str | None = None
    error: str | None = None
    warnings: list[dict[str, str]] = Field(default_factory=list)


class Manifest(BaseModel):
    files: dict[str, FileRecord] = Field(default_factory=dict)
    derived: dict[str, DerivedStageRecord] = Field(default_factory=dict)
    last_build: BuildRunRecord | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


def manifest_path(root: Path) -> Path:
    return root / ".docgraph" / "manifest.json"


def load_manifest(root: Path) -> Manifest:
    p = manifest_path(root)
    if not p.is_file():
        return Manifest()
    return Manifest.model_validate(json.loads(p.read_text("utf-8")))


def save_manifest(root: Path, manifest: Manifest) -> None:
    p = manifest_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=p.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(p)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
