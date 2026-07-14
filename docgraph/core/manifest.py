"""Manifest —— 增量构建的"账本"。

存放在 `.docgraph/manifest.json`，每个被追踪的文件一条记录。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class StageRecord(BaseModel):
    duration_s: float = 0.0
    ok: bool = True
    error: str | None = None
    nodes: int = 0
    edges: int = 0


class FileRecord(BaseModel):
    path: str
    doc_id: str | None = None
    hash: str | None = None
    mtime: float | None = None
    size: int | None = None
    parser: str | None = None
    requested_parser: str | None = None
    parser_attempts: list[dict[str, Any]] = Field(default_factory=list)
    quality_status: str | None = None
    fallback_reason: str | None = None
    status: str = "pending"  # pending|parsed|extracted|linked|embedded|error
    stage_log: dict[str, StageRecord] = Field(default_factory=dict)
    last_run: str | None = None
    error: str | None = None


class Manifest(BaseModel):
    files: dict[str, FileRecord] = Field(default_factory=dict)
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
    p.write_text(
        json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
