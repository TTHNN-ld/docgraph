"""Shared linker utilities with deterministic, unbounded corpus traversal."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def write_jsonl_atomic(path: Path, records: list[dict]) -> None:
    """Replace a current-run audit instead of accumulating duplicate history."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
