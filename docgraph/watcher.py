"""Watcher —— 文件变更监听 + 增量构建。

当前实现：
- 轮询配置选中的文档文件
- debounce 0.5s
- 排队 + 串行处理（避免 LLM 并发风暴）
- 同一窗口的变化合并为一次增量 build
- 删除文件时触发全量文档集对账
"""

from __future__ import annotations

import time
from pathlib import Path

from docgraph.core.build_lock import BuildLockedError
from docgraph.core.config import (
    SUPPORTED_DOCUMENT_SUFFIXES,
    DocGraphConfig,
    load_config,
    project_root_from_cwd,
)
from docgraph.core.dotenv import autoload_env
from docgraph.core.logger import get_logger
from docgraph.core.pipeline import BuildReport, build, discover_files
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
    """基于轮询的 watch，避免引入平台特定的文件事件依赖。"""
    root = project_root_from_cwd()
    autoload_env(root)
    cfg = load_config(root)
    dg = root / ".docgraph"

    if not dg.is_dir():
        log.error("No .docgraph/ found. Run 'docgraph init' first.")
        return

    store = SQLiteGraphStore(dg / "graph.db")
    queue = DebounceQueue(delay_s=0.5)
    config_snapshot = cfg.model_dump_json()

    try:
        log.info("[watch] Initial build...")
        try:
            initial_report = build(root, cfg, store)
            _log_build_result("initial build", initial_report)
        except BuildLockedError as e:
            queue.add("<initial>")
            log.info(f"[watch] initial build busy; queued for retry: {e}")
        log.info("[watch] Watching for changes...")

        prev_mtimes = _snapshot_mtimes(_watched_files(root, cfg, paths))
        while True:
            # Reload project/user configuration so include/exclude changes alter
            # the watched source set without restarting the process.
            cfg = load_config(root)
            next_config_snapshot = cfg.model_dump_json()
            if next_config_snapshot != config_snapshot:
                queue.add("<configuration>")
                config_snapshot = next_config_snapshot
                log.info("[watch] configuration changed")
            changed = _detect_changes(_watched_files(root, cfg, paths), prev_mtimes)
            if changed:
                for c in changed:
                    queue.add(c)
                log.info(f"[watch] {len(changed)} file(s) changed")

            ready = queue.ready()
            if ready:
                try:
                    log.info(f"[watch] reconciling {len(ready)} changed source(s)")
                    report = build(root, cfg, store)
                    _log_build_result("incremental build", report)
                except BuildLockedError as e:
                    # Another process owns the mutation lock. Keep the events;
                    # otherwise their mtimes have already been acknowledged and
                    # the watcher would silently lose this rebuild.
                    for path in ready:
                        queue.add(path)
                    log.info(f"[watch] build busy; queued for retry: {e}")
                except Exception as e:
                    for path in ready:
                        queue.add(path)
                    log.error(f"[watch] build failed: {e}")

            time.sleep(interval_s)

    except KeyboardInterrupt:
        log.info("[watch] Stopped.")
    finally:
        store.close()


def _walk_watched(dirs: list[Path]) -> list[Path]:
    out: list[Path] = []
    for d in dirs:
        if d.is_dir():
            out.extend(
                f
                for f in sorted(d.rglob("*"))
                if f.is_file() and f.suffix.lower() in SUPPORTED_DOCUMENT_SUFFIXES
            )
        elif d.is_file() and d.suffix.lower() in SUPPORTED_DOCUMENT_SUFFIXES:
            out.append(d)
    return out


def _watched_files(
    root: Path,
    cfg: DocGraphConfig,
    paths: list[str] | None,
) -> list[Path]:
    if paths:
        return _walk_watched([root / path for path in paths])
    return discover_files(root, cfg)


def _detect_changes(files: list[Path], prev: dict[str, float]) -> list[str]:
    changed: list[str] = []
    current: dict[str, float] = {}
    for f in files:
        fp = str(f)
        try:
            cur = f.stat().st_mtime
        except FileNotFoundError:
            continue
        current[fp] = cur
        if fp not in prev or abs(cur - prev[fp]) > 0.001:
            changed.append(fp)
    changed.extend(fp for fp in prev if fp not in current)
    prev.clear()
    prev.update(current)
    return sorted(set(changed))


def _snapshot_mtimes(files: list[Path]) -> dict[str, float]:
    snapshot: dict[str, float] = {}
    for path in files:
        try:
            snapshot[str(path)] = path.stat().st_mtime
        except FileNotFoundError:
            continue
    return snapshot


def _log_build_result(action: str, report: BuildReport) -> None:
    message = (
        f"[watch] {action}: parsed={report.parsed} skipped={report.skipped} "
        f"errors={report.errors} nodes={report.nodes_total} "
        f"edges={report.edges_total} duration={report.duration_s}s"
    )
    if report.errors:
        log.error(message)
    else:
        log.info(message)
