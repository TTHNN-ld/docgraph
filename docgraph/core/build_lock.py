"""Project-wide build serialization using only the Python standard library."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


class BuildLockedError(RuntimeError):
    """Raised when another process already owns the project build lock."""


@contextmanager
def project_build_lock(root: Path) -> Iterator[None]:
    """Fail fast when another process is mutating the same project index."""
    path = root / ".docgraph" / "build.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        _try_lock(handle)
    except Exception:
        handle.close()
        raise
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\n".encode())
        handle.flush()
        yield
    finally:
        _unlock(handle)
        handle.close()


def _try_lock(handle: BinaryIO) -> None:
    try:
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            import msvcrt

            handle.seek(0)
            if not handle.read(1):
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as exc:
        raise BuildLockedError(
            "Another DocGraph build is already running for this project."
        ) from exc


def _unlock(handle: BinaryIO) -> None:
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
