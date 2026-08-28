"""使用 SQLite 普通表和 Python 端余弦计算的轻量向量存储。

之所以不直接上 sqlite-vec：
- sqlite-vec 在 Python 3.13 上的发行尚不稳定，强依赖会增加项目门槛
- 核心安装保持零原生扩展依赖，适合本地中小规模索引
- 通过 VectorStore 接口隔离，将来切 sqlite-vec / faiss 都不影响业务层
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path


class VectorStore:
    """轻量向量存储。

    `vec_nodes` 保存节点向量，`vec_items(namespace, item_id, ...)` 保存 chunk
    等通用对象。底层是本地 SQLite + O(N) 余弦；更大规模可切换到已有的
    LanceDB 后端，而不影响查询层。
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            # WAL 在某些场景（旧进程持有 shm/wal、网络盘）会 disk I/O error；
            # 向量库可重建，所以失败时退回 DELETE journal mode。
            try:
                self._conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.OperationalError:
                self._conn.execute("PRAGMA journal_mode = DELETE")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def init_schema(self) -> None:
        try:
            c = self._connect()
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS vec_nodes (
                  node_id TEXT PRIMARY KEY,
                  model   TEXT NOT NULL,
                  dim     INTEGER NOT NULL,
                  vector  TEXT NOT NULL,
                  content_hash TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_vec_model ON vec_nodes(model);
                CREATE TABLE IF NOT EXISTS vec_items (
                  namespace TEXT NOT NULL,
                  item_id   TEXT NOT NULL,
                  model     TEXT NOT NULL,
                  dim       INTEGER NOT NULL,
                  vector    TEXT NOT NULL,
                  content_hash TEXT,
                  PRIMARY KEY(namespace, item_id)
                );
                CREATE INDEX IF NOT EXISTS idx_vec_items_ns_model
                  ON vec_items(namespace, model);
                """
            )
            self._ensure_content_hash_columns(c)
            c.commit()
        except sqlite3.OperationalError as e:
            # 向量库是派生数据；如果 WAL/shm 损坏，直接重建。
            if "disk I/O error" in str(e) or "database disk image" in str(e):
                self._reset_db_files()
                c = self._connect()
                c.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS vec_nodes (
                      node_id TEXT PRIMARY KEY,
                      model   TEXT NOT NULL,
                      dim     INTEGER NOT NULL,
                      vector  TEXT NOT NULL,
                      content_hash TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_vec_model ON vec_nodes(model);
                    CREATE TABLE IF NOT EXISTS vec_items (
                      namespace TEXT NOT NULL,
                      item_id   TEXT NOT NULL,
                      model     TEXT NOT NULL,
                      dim       INTEGER NOT NULL,
                      vector    TEXT NOT NULL,
                      content_hash TEXT,
                      PRIMARY KEY(namespace, item_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_vec_items_ns_model
                      ON vec_items(namespace, model);
                    """
                )
                self._ensure_content_hash_columns(c)
                c.commit()
            else:
                raise

    def _reset_db_files(self) -> None:
        """删除向量 DB 及 WAL/SHM；下一次 init_schema 会重建。"""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        for p in [
            self.db_path,
            self.db_path.with_name(self.db_path.name + "-wal"),
            self.db_path.with_name(self.db_path.name + "-shm"),
        ]:
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass

    @staticmethod
    def _ensure_content_hash_columns(conn: sqlite3.Connection) -> None:
        for table in ("vec_nodes", "vec_items"):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "content_hash" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN content_hash TEXT")

    def upsert(
        self,
        node_id: str,
        model: str,
        vector: list[float],
        content_hash: str | None = None,
    ) -> None:
        self.upsert_many([(node_id, model, vector, content_hash)])

    def upsert_many(
        self,
        entries: list[tuple[str, str, list[float], str | None]],
    ) -> None:
        if not entries:
            return
        c = self._connect()
        c.executemany(
            """
            INSERT INTO vec_nodes (node_id, model, dim, vector, content_hash)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
              model = excluded.model,
              dim   = excluded.dim,
              vector = excluded.vector,
              content_hash = excluded.content_hash
            """,
            [
                (node_id, model, len(vector), json.dumps(vector), content_hash)
                for node_id, model, vector, content_hash in entries
            ],
        )
        c.commit()

    def upsert_item(
        self,
        namespace: str,
        item_id: str,
        model: str,
        vector: list[float],
        content_hash: str | None = None,
    ) -> None:
        self.upsert_items_many(namespace, [(item_id, model, vector, content_hash)])

    def upsert_items_many(
        self,
        namespace: str,
        entries: list[tuple[str, str, list[float], str | None]],
    ) -> None:
        if not entries:
            return
        c = self._connect()
        c.executemany(
            """
            INSERT INTO vec_items (namespace, item_id, model, dim, vector, content_hash)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(namespace, item_id) DO UPDATE SET
              model = excluded.model,
              dim   = excluded.dim,
              vector = excluded.vector,
              content_hash = excluded.content_hash
            """,
            [
                (namespace, item_id, model, len(vector), json.dumps(vector), content_hash)
                for item_id, model, vector, content_hash in entries
            ],
        )
        c.commit()

    def delete(self, node_id: str) -> None:
        c = self._connect()
        c.execute("DELETE FROM vec_nodes WHERE node_id = ?", (node_id,))
        c.commit()

    def delete_by_doc(self, doc_ids: list[str], graph_db: Path) -> int:
        """删除指定文档当前对应的 node/chunk 向量。"""
        c = self._connect()
        if not doc_ids:
            return 0
        gconn = sqlite3.connect(str(graph_db))
        try:
            placeholders = ",".join("?" * len(doc_ids))
            node_ids = {
                row[0]
                for row in gconn.execute(
                    f"SELECT id FROM nodes WHERE doc_id IN ({placeholders})", doc_ids
                ).fetchall()
            }
            chunk_ids = {
                row[0]
                for row in gconn.execute(
                    f"SELECT id FROM chunks WHERE doc_id IN ({placeholders})", doc_ids
                ).fetchall()
            }
        finally:
            gconn.close()
        for nid in node_ids:
            c.execute("DELETE FROM vec_nodes WHERE node_id = ?", (nid,))
        for chunk_id in chunk_ids:
            c.execute(
                "DELETE FROM vec_items WHERE namespace = 'chunk' AND item_id = ?",
                (chunk_id,),
            )
        c.commit()
        return len(node_ids) + len(chunk_ids)

    def stored_node_hashes(self, model: str) -> dict[str, str | None]:
        c = self._connect()
        return {
            row["node_id"]: row["content_hash"]
            for row in c.execute(
                "SELECT node_id, content_hash FROM vec_nodes WHERE model = ?", (model,)
            ).fetchall()
        }

    def stored_item_hashes(self, namespace: str, model: str) -> dict[str, str | None]:
        c = self._connect()
        return {
            row["item_id"]: row["content_hash"]
            for row in c.execute(
                "SELECT item_id, content_hash FROM vec_items WHERE namespace = ? AND model = ?",
                (namespace, model),
            ).fetchall()
        }

    def prune(self, node_ids: set[str], namespace: str, item_ids: set[str]) -> int:
        c = self._connect()
        stale_nodes = {
            row["node_id"] for row in c.execute("SELECT node_id FROM vec_nodes").fetchall()
        } - node_ids
        stale_items = {
            row["item_id"]
            for row in c.execute(
                "SELECT item_id FROM vec_items WHERE namespace = ?", (namespace,)
            ).fetchall()
        } - item_ids
        c.executemany("DELETE FROM vec_nodes WHERE node_id = ?", [(item,) for item in stale_nodes])
        c.executemany(
            "DELETE FROM vec_items WHERE namespace = ? AND item_id = ?",
            [(namespace, item) for item in stale_items],
        )
        c.commit()
        return len(stale_nodes) + len(stale_items)

    def all_for_model(self, model: str) -> list[tuple[str, list[float]]]:
        c = self._connect()
        rows = c.execute(
            "SELECT node_id, vector FROM vec_nodes WHERE model = ?",
            (model,),
        ).fetchall()
        return [(r["node_id"], json.loads(r["vector"])) for r in rows]

    def all_items_for_model(self, namespace: str, model: str) -> list[tuple[str, list[float]]]:
        c = self._connect()
        rows = c.execute(
            "SELECT item_id, vector FROM vec_items WHERE namespace = ? AND model = ?",
            (namespace, model),
        ).fetchall()
        return [(r["item_id"], json.loads(r["vector"])) for r in rows]

    def count(self) -> int:
        c = self._connect()
        node_count = int(c.execute("SELECT COUNT(*) AS c FROM vec_nodes").fetchone()["c"])
        item_count = int(c.execute("SELECT COUNT(*) AS c FROM vec_items").fetchone()["c"])
        return node_count + item_count

    def count_items(self, namespace: str | None = None) -> int:
        c = self._connect()
        if namespace is None:
            row = c.execute("SELECT COUNT(*) AS c FROM vec_items").fetchone()
        else:
            row = c.execute(
                "SELECT COUNT(*) AS c FROM vec_items WHERE namespace = ?",
                (namespace,),
            ).fetchone()
        return int(row["c"])

    def search(
        self,
        query_vec: list[float],
        model: str,
        top_k: int = 10,
        allowed_ids: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        """O(N) cosine similarity。N < 100k 内表现可接受。"""
        rows = self.all_for_model(model)
        if allowed_ids is not None:
            rows = [(node_id, vector) for node_id, vector in rows if node_id in allowed_ids]
        if not rows:
            return []
        results: list[tuple[str, float]] = []
        for nid, v in rows:
            sim = _cosine(query_vec, v)
            results.append((nid, sim))
        results.sort(key=lambda kv: kv[1], reverse=True)
        return results[:top_k]

    def search_items(
        self,
        namespace: str,
        query_vec: list[float],
        model: str,
        top_k: int = 10,
        allowed_ids: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        rows = self.all_items_for_model(namespace, model)
        if allowed_ids is not None:
            rows = [(item_id, vector) for item_id, vector in rows if item_id in allowed_ids]
        if not rows:
            return []
        results: list[tuple[str, float]] = []
        for item_id, v in rows:
            sim = _cosine(query_vec, v)
            results.append((item_id, sim))
        results.sort(key=lambda kv: kv[1], reverse=True)
        return results[:top_k]

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    s = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=False):
        s += x * y
        na += x * x
        nb += y * y
    denom = math.sqrt(na) * math.sqrt(nb)
    if denom == 0:
        return 0.0
    return s / denom
