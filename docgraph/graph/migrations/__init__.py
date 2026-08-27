"""Schema migration —— SQLite 数据库结构演进。

设计：
- 每次破坏性 schema 变更注册一个 Migration（function 形式）
- `schema_versions` 表记录全局 + 各组件的版本
- run_migrations() 比较目标版本与现状，按序应用
- 升级前自动备份 graph.db → graph.db.bak.<ts>
- 失败回滚到 backup

版本按顺序升级；历史 migration 一经发布只修复执行错误，不改变既有语义。
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from docgraph.core.logger import get_logger

log = get_logger(__name__)


@dataclass
class Migration:
    """单次 schema 升级。"""

    version: int  # target version (1, 2, 3, ...)
    description: str
    upgrade: Callable[[sqlite3.Connection], None]
    component: str = "global"  # 默认 global；将来可分组件


# ---------------------------------------------------------------------------
# Migrations 注册表（按版本号递增）
# ---------------------------------------------------------------------------


def _migration_001_baseline(conn: sqlite3.Connection) -> None:
    """v1 baseline —— 由 SQLiteGraphStore.init_schema 完成实际 DDL，
    这里只把版本号写入。"""
    # SQLiteGraphStore.init_schema 已经创建好所有表，此 migration 只是登记
    conn.execute(
        "INSERT OR REPLACE INTO schema_versions(component, version, applied_at) "
        "VALUES ('global', 1, datetime('now'))"
    )


def _migration_002_l0_l1(conn: sqlite3.Connection) -> None:
    """v2: 增加 L0 blocks、chunk 来源和全文索引。

    对全新 db，store.init_schema 已经建好这些；本 migration 处理"从 v1 升级"的旧 db。
    """
    # blocks 表（IF NOT EXISTS 安全）
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS blocks (
      id            TEXT PRIMARY KEY,
      doc_id        TEXT NOT NULL,
      page          INTEGER,
      kind          TEXT NOT NULL,
      reading_order INTEGER,
      bbox          TEXT,
      text          TEXT,
      table_json    TEXT,
      image_path    TEXT,
      latex         TEXT,
      section_path  TEXT,
      heading_level INTEGER,
      attrs         TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_blocks_doc_page ON blocks(doc_id, page);
    CREATE INDEX IF NOT EXISTS idx_blocks_kind ON blocks(kind);
    """)
    # 给旧 chunks 表补 block_ids 列（ALTER 幂等检查）
    cols = {r[1] for r in conn.execute("PRAGMA table_info(chunks)").fetchall()}
    if "block_ids" not in cols:
        conn.execute("ALTER TABLE chunks ADD COLUMN block_ids TEXT")
    # FTS5 全文表
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
        "chunk_id UNINDEXED, text, tokenize='unicode61')"
    )
    # 索引
    conn.executescript("""
    CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
    CREATE INDEX IF NOT EXISTS idx_chunks_section ON chunks(section_id);
    """)
    conn.execute(
        "INSERT OR REPLACE INTO schema_versions(component, version, applied_at) "
        "VALUES ('global', 2, datetime('now'))"
    )


def _migration_003_l1_chunk_metadata(conn: sqlite3.Connection) -> None:
    """v3: strengthen L1 chunks with stable section binding and page ranges."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(chunks)").fetchall()}
    additions = {
        "page_start": "INTEGER",
        "page_end": "INTEGER",
        "section_node_id": "TEXT",
        "source_hash": "TEXT",
        "chunk_type": "TEXT",
    }
    for col, typ in additions.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE chunks ADD COLUMN {col} {typ}")
    conn.executescript("""
    CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
    CREATE INDEX IF NOT EXISTS idx_chunks_section ON chunks(section_id);
    CREATE INDEX IF NOT EXISTS idx_chunks_section_node ON chunks(section_node_id);
    UPDATE chunks SET page_start = COALESCE(page_start, page);
    UPDATE chunks SET page_end = COALESCE(page_end, page);
    UPDATE chunks SET source_hash = COALESCE(source_hash, hash);
    UPDATE chunks SET chunk_type = COALESCE(chunk_type, json_extract(attrs, '$.chunk_type'), 'section');
    """)
    conn.execute(
        "INSERT OR REPLACE INTO schema_versions(component, version, applied_at) "
        "VALUES ('global', 3, datetime('now'))"
    )


def _migration_004_node_evidence(conn: sqlite3.Connection) -> None:
    """v4: make Node evidence first-class, matching ADR-008."""
    has_nodes = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='nodes'"
    ).fetchone()
    if not has_nodes:
        conn.execute(
            "INSERT OR REPLACE INTO schema_versions(component, version, applied_at) "
            "VALUES ('global', 4, datetime('now'))"
        )
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(nodes)").fetchall()}
    if "evidence" not in cols:
        conn.execute("ALTER TABLE nodes ADD COLUMN evidence TEXT")
    conn.execute("""
        UPDATE nodes
        SET evidence = json_object('chunk_ids', json_array(), 'pages', json_array(),
                                   'bboxes', json_array(), 'extractor', 'unknown',
                                   'raw_snippet', NULL)
        WHERE evidence IS NULL OR evidence = ''
    """)
    conn.execute(
        "INSERT OR REPLACE INTO schema_versions(component, version, applied_at) "
        "VALUES ('global', 4, datetime('now'))"
    )


MIGRATIONS: list[Migration] = [
    Migration(
        version=1,
        description="Baseline graph schema",
        upgrade=_migration_001_baseline,
    ),
    Migration(
        version=2,
        description="L0 blocks, chunk sources, and full-text index",
        upgrade=_migration_002_l0_l1,
    ),
    Migration(
        version=3,
        description="L1 chunks: page ranges, chunk_type, section_node_id",
        upgrade=_migration_003_l1_chunk_metadata,
    ),
    Migration(
        version=4,
        description="Node evidence column",
        upgrade=_migration_004_node_evidence,
    ),
]

CURRENT_VERSION = max(m.version for m in MIGRATIONS) if MIGRATIONS else 0


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------


def current_db_version(db_path: Path) -> int:
    """读取当前 db 的版本号；表不存在视为 0。"""
    if not db_path.is_file():
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        try:
            row = conn.execute(
                "SELECT version FROM schema_versions WHERE component = 'global'"
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row[0]) if row else 0
    finally:
        conn.close()


def needs_migration(db_path: Path) -> bool:
    return current_db_version(db_path) < CURRENT_VERSION


def backup_db(db_path: Path) -> Path | None:
    """备份 db。返回备份文件路径；db 不存在时返回 None。"""
    if not db_path.is_file():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = db_path.with_suffix(db_path.suffix + f".bak.{ts}")
    shutil.copy2(db_path, bak)
    return bak


def run_migrations(db_path: Path, *, dry_run: bool = False) -> list[int]:
    """运行所有需要的 migration。

    Returns:
        本次应用的 migration 版本号列表。
    """
    cur_ver = current_db_version(db_path)
    if cur_ver >= CURRENT_VERSION:
        log.info(f"[migrate] db at v{cur_ver}, target v{CURRENT_VERSION} — up to date")
        return []

    pending = [m for m in MIGRATIONS if m.version > cur_ver]
    pending.sort(key=lambda m: m.version)

    if dry_run:
        log.info(f"[migrate] would apply: {[m.version for m in pending]}")
        return [m.version for m in pending]

    bak = backup_db(db_path)
    if bak:
        log.info(f"[migrate] backed up → {bak}")

    applied: list[int] = []
    conn = sqlite3.connect(str(db_path))
    try:
        for m in pending:
            log.info(f"[migrate] applying v{m.version}: {m.description}")
            try:
                m.upgrade(conn)
                conn.commit()
                applied.append(m.version)
            except Exception as e:
                log.error(f"[migrate] FAILED v{m.version}: {e}")
                conn.rollback()
                # 还原备份
                if bak:
                    conn.close()
                    shutil.copy2(bak, db_path)
                    log.warning(f"[migrate] restored from {bak}")
                raise
        log.info(f"[migrate] done. v{cur_ver} → v{CURRENT_VERSION}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return applied
