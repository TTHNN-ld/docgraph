"""Watcher —— 文件变更监听 + 增量构建。

M2 实现：
- 用 watchdog 监听 docs/
- debounce 1s
- 排队 + 串行处理（避免 LLM 并发风暴）
- 只触发变化文件的增量 build
"""
from __future__ import annotations

import time
from pathlib import Path

from docgraph.core.config import load_config, project_root_from_cwd
from docgraph.core.logger import get_logger
from docgraph.core.manifest import load_manifest, save_manifest
from docgraph.core.pipeline import build
from docgraph.graph.sqlite_store import SQLiteGraphStore

log = get_logger(__name__)


class DebounceQueue:
    """去抖队列：文件变更后等待静默期再触发。"""

    def __init__(self, delay_s: float = 1.0) -> None:
        self.delay_s = delay_s
        self._files: dict[str, float] = {}
        self._last_process = 0.0

    def add(self, path: str) -> None:
        self._files[path] = time.time()

    def ready(self) -> list[str]:
        if not self._files:
            return []
        now = time.time()
        # 等待 delay_s 没有新变更
        # 同时距离上次处理至少 0.5s 防止高频触发
        ready_list = [
            p
            for p, t in self._files.items()
            if now - t >= self.delay_s and now - self._last_process >= 0.5
        ]
        if ready_list:
            for p in ready_list:
                del self._files[p]
            self._last_process = now
        return ready_list

    def empty(self) -> bool:
        return not self._files


def run_watch_loop(
    paths: list[str] | None = None,
    *,
    interval_s: float = 1.0,
) -> None:
    """基于轮询的简化 watch（M1/M2 先用 polling 代替 watchdog 避免平台依赖）"""
    root = project_root_from_cwd()
    cfg = load_config(root)
    dg = root / ".docgraph"

    if not dg.is_dir():
        log.error("No .docgraph/ found. Run 'docgraph init' first.")
        return

    store = SQLiteGraphStore(dg / "graph.db")
    store.init_schema()
    manifest = load_manifest(root)
    queue = DebounceQueue(delay_s=0.5)

    watch_dirs: list[Path] = []
    if paths:
        watch_dirs = [root / p for p in paths]
    else:
        watch_dirs = [root / "docs", root / "spec"]

    # 初始构建
    log.info("[watch] Initial build...")
    build(root, cfg, store, manifest)
    log.info("[watch] Watching for changes...")

    # 跟踪文件 mtime
    prev_mtimes: dict[str, float] = {}
    for f in _walk_watched(watch_dirs):
        prev_mtimes[str(f)] = f.stat().st_mtime

    try:
        while True:
            changed = _detect_changes(watch_dirs, prev_mtimes)
            if changed:
                for c in changed:
                    queue.add(c)
                log.info(f"[watch] {len(changed)} file(s) changed")

            ready = queue.ready()
            if ready:
                for p in ready:
                    log.info(f"[watch] building: {p}")
                    try:
                        report = build(
                            root, cfg, store, manifest,
                            file_filter=Path(p),
                        )
                        log.info(
                            f"[watch] done: {report.nodes_total} nodes, "
                            f"{report.edges_total} edges, {report.duration_s}s"
                        )
                    except Exception as e:
                        log.error(f"[watch] build failed: {e}")
                save_manifest(root, manifest)

            time.sleep(interval_s)

    except KeyboardInterrupt:
        log.info("[watch] Stopped.")
    finally:
        store.close()


def _walk_watched(dirs: list[Path]) -> list[Path]:
    out: list[Path] = []
    for d in dirs:
        if d.is_dir():
            out.extend(sorted(d.rglob("*")))
        elif d.is_file():
            out.append(d)
    return out


def _detect_changes(
    dirs: list[Path], prev: dict[str, float]
) -> list[str]:
    changed: list[str] = []
    for f in _walk_watched(dirs):
        if not f.is_file() or not f.name.endswith(".pdf"):
            continue
        fp = str(f)
        cur = f.stat().st_mtime
        if fp not in prev or abs(cur - prev[fp]) > 0.001:
            prev[fp] = cur
            changed.append(fp)
    return changed