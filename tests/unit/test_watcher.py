from __future__ import annotations

from pathlib import Path

from docgraph.core.config import DocGraphConfig
from docgraph.watcher import _detect_changes, _snapshot_mtimes, _walk_watched, _watched_files


def test_walk_watched_accepts_every_core_document_format(tmp_path: Path) -> None:
    expected = {
        tmp_path / "a.pdf",
        tmp_path / "b.docx",
        tmp_path / "c.xlsx",
        tmp_path / "d.xlsm",
        tmp_path / "e.md",
        tmp_path / "f.markdown",
    }
    for path in expected:
        path.write_bytes(b"fixture")
    (tmp_path / "ignored.txt").write_text("ignored", encoding="utf-8")

    assert set(_walk_watched([tmp_path])) == expected


def test_default_watched_files_follow_configured_discovery(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "spec.docx"
    source.write_bytes(b"fixture")

    assert _watched_files(tmp_path, DocGraphConfig(), None) == [source.resolve()]


def test_detect_changes_reports_removed_sources(tmp_path: Path) -> None:
    source = tmp_path / "spec.md"
    source.write_text("content", encoding="utf-8")
    previous = {str(source): source.stat().st_mtime}
    source.unlink()

    changed = _detect_changes([], previous)

    assert changed == [str(source)]
    assert previous == {}


def test_snapshot_mtimes_ignores_files_removed_during_scan(tmp_path: Path) -> None:
    present = tmp_path / "present.md"
    missing = tmp_path / "missing.md"
    present.write_text("content", encoding="utf-8")

    snapshot = _snapshot_mtimes([present, missing])

    assert snapshot == {str(present): present.stat().st_mtime}
