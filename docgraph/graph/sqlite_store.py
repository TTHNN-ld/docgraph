"""SQLite 实现 —— DocGraph 的默认存储后端。

设计要点：
- 一个文件搞定图 + 索引；后期再加 sqlite-vec 装向量。
- 所有 JSON 字段用 TEXT，进出走 json 模块。
- 用 UPSERT (ON CONFLICT) 实现幂等。
- 邻居/路径用递归 CTE。
"""
from __future__ import annotations

import json
import sqlite3
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from docgraph.graph.schema import (
    BBox,
    Block,
    BlockKind,
    Edge,
    EdgeKind,
    Evidence,
    Location,
    Node,
    NodeKind,
    TableData,
)
from docgraph.graph.store import NodeQuery, Subgraph

# ---------------------------------------------------------------------------
# Migration 表（最小启动）
# ---------------------------------------------------------------------------

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_versions (
  component  TEXT PRIMARY KEY,
  version    INTEGER NOT NULL,
  applied_at TEXT
);

CREATE TABLE IF NOT EXISTS nodes (
  id              TEXT PRIMARY KEY,
  kind            TEXT NOT NULL,
  name            TEXT NOT NULL,
  qualified_name  TEXT,
  doc_id          TEXT NOT NULL,
  page            INTEGER,
  bbox            TEXT,
  section_path    TEXT,
  evidence        TEXT,
  attrs           TEXT,
  summary         TEXT,
  embedding_id    INTEGER,
  hash            TEXT,
  schema_version  INTEGER,
  created_at      TEXT,
  updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_nodes_kind_name      ON nodes(kind, name);
CREATE INDEX IF NOT EXISTS idx_nodes_qualified_name ON nodes(qualified_name);
CREATE INDEX IF NOT EXISTS idx_nodes_doc            ON nodes(doc_id);

CREATE TABLE IF NOT EXISTS aliases (
  alias    TEXT NOT NULL,
  node_id  TEXT NOT NULL,
  PRIMARY KEY (alias, node_id),
  FOREIGN KEY(node_id) REFERENCES nodes(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_aliases_alias ON aliases(alias);

CREATE TABLE IF NOT EXISTS edges (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  src         TEXT NOT NULL,
  dst         TEXT NOT NULL,
  kind        TEXT NOT NULL,
  confidence  REAL,
  evidence    TEXT,
  attrs       TEXT,
  created_at  TEXT,
  schema_version INTEGER,
  FOREIGN KEY(src) REFERENCES nodes(id) ON DELETE CASCADE,
  FOREIGN KEY(dst) REFERENCES nodes(id) ON DELETE CASCADE,
  UNIQUE(src, dst, kind)
);
CREATE INDEX IF NOT EXISTS idx_edges_src       ON edges(src, kind);
CREATE INDEX IF NOT EXISTS idx_edges_dst       ON edges(dst, kind);
CREATE INDEX IF NOT EXISTS idx_edges_kind_conf ON edges(kind, confidence);

CREATE TABLE IF NOT EXISTS chunks (
  id          TEXT PRIMARY KEY,
  doc_id      TEXT NOT NULL,
  page        INTEGER,
  page_start  INTEGER,
  page_end    INTEGER,
  section_id  TEXT,
  section_node_id TEXT,
  text        TEXT NOT NULL,
  hash        TEXT,
  source_hash TEXT,
  block_ids   TEXT,
  chunk_type  TEXT,
  attrs       TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_section ON chunks(section_id);

-- L1 全文检索（FTS5）
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  chunk_id UNINDEXED, text, tokenize='unicode61'
);

-- L0 版面块（layered-architecture.md §3.1）
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

CREATE TABLE IF NOT EXISTS manifest (
  path       TEXT PRIMARY KEY,
  doc_id     TEXT,
  hash       TEXT,
  mtime      REAL,
  size       INTEGER,
  parser     TEXT,
  status     TEXT,
  stage_log  TEXT,
  last_run   TEXT
);
"""


# ---------------------------------------------------------------------------
# 实现
# ---------------------------------------------------------------------------


class SQLiteGraphStore:
    """SQLite 实现的 GraphStore。"""

    CURRENT_VERSION = 4

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    # ------- lifecycle -------

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            # check_same_thread=False 让 FastAPI 多线程读安全（SQLite 本身在
            # 默认 serialized 模式下是线程安全的，写入由 _conn.execute 串行处理）
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def init_schema(self) -> None:
        from docgraph.graph.schema import _utcnow  # type: ignore

        conn = self._connect()
        conn.executescript(_SCHEMA_V1)
        # 跑 migration（处理旧 v1 db 升级：blocks 表、chunks.block_ids、FTS）
        try:
            from docgraph.graph.migrations import current_db_version, run_migrations
            from docgraph.graph.migrations import CURRENT_VERSION
            if current_db_version(self.path) < CURRENT_VERSION:
                conn.commit()
                run_migrations(self.path)
        except Exception as e:
            # migration 失败不阻塞 init（最坏情况是缺新列，会在写入时报错）
            pass
        # 记录版本
        conn.execute(
            "INSERT OR REPLACE INTO schema_versions(component, version, applied_at) "
            "VALUES (?, ?, ?)",
            ("global", self.CURRENT_VERSION, _utcnow()),
        )
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # ------- node -------

    def upsert_node(self, node: Node) -> None:
        from docgraph.graph.schema import _utcnow  # type: ignore

        conn = self._connect()
        bbox_json = (
            json.dumps(node.location.bbox.model_dump()) if node.location.bbox else None
        )
        conn.execute(
            """
            INSERT INTO nodes (
              id, kind, name, qualified_name, doc_id, page, bbox, section_path,
              evidence, attrs, summary, embedding_id, hash, schema_version, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              kind=excluded.kind,
              name=excluded.name,
              qualified_name=excluded.qualified_name,
              doc_id=excluded.doc_id,
              page=excluded.page,
              bbox=excluded.bbox,
              section_path=excluded.section_path,
              evidence=excluded.evidence,
              attrs=excluded.attrs,
              summary=excluded.summary,
              embedding_id=excluded.embedding_id,
              hash=excluded.hash,
              schema_version=excluded.schema_version,
              updated_at=excluded.updated_at
            """,
            (
                node.id,
                node.kind.value,
                node.name,
                node.qualified_name,
                node.doc_id,
                node.location.page,
                bbox_json,
                node.location.section_path,
                json.dumps(node.evidence.model_dump(), ensure_ascii=False),
                json.dumps(node.attrs, ensure_ascii=False),
                node.summary,
                node.embedding_id,
                node.hash,
                node.schema_version,
                node.created_at,
                _utcnow(),
            ),
        )

        # 别名维护
        conn.execute("DELETE FROM aliases WHERE node_id = ?", (node.id,))
        for alias in node.aliases:
            conn.execute(
                "INSERT OR IGNORE INTO aliases(alias, node_id) VALUES (?, ?)",
                (alias, node.id),
            )
        conn.commit()

    def get_node(self, id: str) -> Node | None:
        conn = self._connect()
        row = conn.execute("SELECT * FROM nodes WHERE id = ?", (id,)).fetchone()
        if row is None:
            return None
        return self._row_to_node(row)

    def delete_node(self, id: str) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM nodes WHERE id = ?", (id,))

    def search_nodes(self, query: NodeQuery) -> list[Node]:
        conn = self._connect()
        clauses: list[str] = []
        params: list = []

        if query.name is not None:
            clauses.append("name = ?")
            params.append(query.name)
        if query.kind is not None:
            clauses.append("kind = ?")
            params.append(query.kind.value)
        if query.doc_id is not None:
            clauses.append("doc_id = ?")
            params.append(query.doc_id)
        if query.qualified_name is not None:
            clauses.append("qualified_name = ?")
            params.append(query.qualified_name)
        if query.alias is not None:
            clauses.append(
                "id IN (SELECT node_id FROM aliases WHERE alias = ?)"
            )
            params.append(query.alias)
        if query.fuzzy is not None:
            clauses.append("(name LIKE ? OR qualified_name LIKE ?)")
            params.extend([f"%{query.fuzzy}%", f"%{query.fuzzy}%"])

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM nodes {where} ORDER BY name LIMIT ? OFFSET ?"
        params.extend([query.limit, query.offset])
        rows = conn.execute(sql, params).fetchall()
        return [self._row_to_node(r) for r in rows]

    # ------- edge -------

    def upsert_edge(self, edge: Edge) -> None:
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO edges (src, dst, kind, confidence, evidence, attrs,
                               created_at, schema_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(src, dst, kind) DO UPDATE SET
              confidence = excluded.confidence,
              evidence   = excluded.evidence,
              attrs      = excluded.attrs
            """,
            (
                edge.src,
                edge.dst,
                edge.kind.value,
                edge.confidence,
                json.dumps(edge.evidence.model_dump(), ensure_ascii=False),
                json.dumps(edge.attrs, ensure_ascii=False),
                edge.created_at,
                edge.schema_version,
            ),
        )
        conn.commit()

    def neighbors(
        self,
        id: str,
        edge_kinds: list[EdgeKind] | None = None,
        depth: int = 1,
        limit: int = 50,
    ) -> Subgraph:
        """简单 BFS 实现（小图够用）。生产可换成递归 CTE。"""
        if depth < 1:
            return Subgraph()
        kind_filter = [k.value for k in edge_kinds] if edge_kinds else None

        conn = self._connect()
        visited_nodes: dict[str, Node] = {}
        edges_collected: list[Edge] = []
        queue: deque[tuple[str, int]] = deque([(id, 0)])
        seen: set[str] = {id}

        while queue and len(visited_nodes) < limit:
            cur_id, cur_depth = queue.popleft()
            node = self.get_node(cur_id)
            if node is None:
                continue
            visited_nodes[cur_id] = node
            if cur_depth >= depth:
                continue

            # 出边
            sql = "SELECT * FROM edges WHERE src = ?"
            params: list = [cur_id]
            if kind_filter:
                placeholders = ",".join("?" * len(kind_filter))
                sql += f" AND kind IN ({placeholders})"
                params.extend(kind_filter)
            for row in conn.execute(sql, params).fetchall():
                edges_collected.append(self._row_to_edge(row))
                if row["dst"] not in seen:
                    seen.add(row["dst"])
                    queue.append((row["dst"], cur_depth + 1))

            # 入边
            sql = "SELECT * FROM edges WHERE dst = ?"
            params = [cur_id]
            if kind_filter:
                placeholders = ",".join("?" * len(kind_filter))
                sql += f" AND kind IN ({placeholders})"
                params.extend(kind_filter)
            for row in conn.execute(sql, params).fetchall():
                edges_collected.append(self._row_to_edge(row))
                if row["src"] not in seen:
                    seen.add(row["src"])
                    queue.append((row["src"], cur_depth + 1))

        return Subgraph(
            nodes=list(visited_nodes.values()),
            edges=edges_collected,
        )

    # ------- doc-level -------

    def delete_doc(self, doc_id: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "DELETE FROM chunks_fts WHERE chunk_id IN "
                "(SELECT id FROM chunks WHERE doc_id = ?)",
                (doc_id,),
            )
            conn.execute(
                "DELETE FROM chunks_fts WHERE chunk_id LIKE ?",
                (f"{doc_id}#%",),
            )
            conn.execute("DELETE FROM nodes WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM blocks WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            # 注意：FOREIGN KEY ON DELETE CASCADE 已经清掉了 edges 和 aliases

    # ------- L0 blocks -------

    def upsert_blocks(self, blocks: list[Block]) -> None:
        """批量写入 L0 版面块。"""
        if not blocks:
            return
        conn = self._connect()
        conn.executemany(
            """
            INSERT INTO blocks (id, doc_id, page, kind, reading_order, bbox,
                                text, table_json, image_path, latex,
                                section_path, heading_level, attrs)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              kind=excluded.kind, reading_order=excluded.reading_order,
              bbox=excluded.bbox, text=excluded.text,
              table_json=excluded.table_json, image_path=excluded.image_path,
              latex=excluded.latex, section_path=excluded.section_path,
              heading_level=excluded.heading_level, attrs=excluded.attrs
            """,
            [
                (
                    b.id, b.doc_id, b.page, b.kind.value, b.reading_order,
                    json.dumps(b.bbox.model_dump()) if b.bbox else None,
                    b.text,
                    json.dumps(b.table.model_dump(), ensure_ascii=False) if b.table else None,
                    b.image_path, b.latex, b.section_path, b.heading_level,
                    json.dumps(b.attrs, ensure_ascii=False),
                )
                for b in blocks
            ],
        )
        conn.commit()

    def get_block(self, block_id: str) -> Block | None:
        conn = self._connect()
        row = conn.execute("SELECT * FROM blocks WHERE id = ?", (block_id,)).fetchone()
        return self._row_to_block(row) if row else None

    def get_blocks(self, block_ids: list[str]) -> list[Block]:
        if not block_ids:
            return []
        conn = self._connect()
        ph = ",".join("?" * len(block_ids))
        rows = conn.execute(
            f"SELECT * FROM blocks WHERE id IN ({ph})", block_ids
        ).fetchall()
        by_id = {r["id"]: self._row_to_block(r) for r in rows}
        return [by_id[bid] for bid in block_ids if bid in by_id]

    def blocks_for_page(self, doc_id: str, page: int) -> list[Block]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM blocks WHERE doc_id = ? AND page = ? ORDER BY reading_order",
            (doc_id, page),
        ).fetchall()
        return [self._row_to_block(r) for r in rows]

    def count_blocks(self, kind: BlockKind | None = None) -> int:
        conn = self._connect()
        if kind is None:
            row = conn.execute("SELECT COUNT(*) AS c FROM blocks").fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM blocks WHERE kind = ?", (kind.value,)
            ).fetchone()
        return int(row["c"])

    def _row_to_block(self, row) -> Block:
        bbox = BBox(**json.loads(row["bbox"])) if row["bbox"] else None
        table = None
        if row["table_json"]:
            table = TableData(**json.loads(row["table_json"]))
        return Block(
            id=row["id"], doc_id=row["doc_id"], page=row["page"],
            kind=BlockKind(row["kind"]), reading_order=row["reading_order"] or 0,
            bbox=bbox, text=row["text"], table=table,
            image_path=row["image_path"], latex=row["latex"],
            section_path=row["section_path"], heading_level=row["heading_level"],
            attrs=json.loads(row["attrs"]) if row["attrs"] else {},
        )

    # ------- L1 chunks -------

    def upsert_chunks(self, chunks: list) -> None:
        """批量写入 L1 chunk（同时写 FTS 索引）。"""
        if not chunks:
            return
        conn = self._connect()
        rows = [
            (
                c.id, c.doc_id, c.page, c.page_start, c.page_end,
                c.section_id, c.section_node_id, c.text, c.hash, c.source_hash,
                json.dumps(c.block_ids, ensure_ascii=False),
                c.chunk_type or c.kind,
                json.dumps(c.attrs, ensure_ascii=False),
            )
            for c in chunks
        ]
        conn.executemany(
            """
            INSERT INTO chunks (
              id, doc_id, page, page_start, page_end, section_id, section_node_id,
              text, hash, source_hash, block_ids, chunk_type, attrs
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              page=excluded.page, page_start=excluded.page_start,
              page_end=excluded.page_end, section_id=excluded.section_id,
              section_node_id=excluded.section_node_id, text=excluded.text,
              hash=excluded.hash, source_hash=excluded.source_hash,
              block_ids=excluded.block_ids, chunk_type=excluded.chunk_type,
              attrs=excluded.attrs
            """,
            rows,
        )
        conn.executemany("DELETE FROM chunks_fts WHERE chunk_id = ?",
                         [(r[0],) for r in rows])
        conn.executemany(
            "INSERT INTO chunks_fts (chunk_id, text) VALUES (?, ?)",
            [(r[0], r[7]) for r in rows],
        )
        conn.commit()

    def get_chunk(self, chunk_id: str):
        conn = self._connect()
        row = conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        return self._row_to_chunk(row) if row else None

    def list_chunks(self, limit: int = 100000):
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM chunks ORDER BY doc_id, page_start, page, id LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_chunk(r) for r in rows]

    def _row_to_chunk(self, row):
        from docgraph.graph.schema import Chunk
        attrs = json.loads(row["attrs"]) if row["attrs"] else {}
        block_ids = json.loads(row["block_ids"]) if row["block_ids"] else []
        return Chunk(
            id=row["id"], doc_id=row["doc_id"], page=row["page"],
            page_start=row["page_start"] if "page_start" in row.keys() else row["page"],
            page_end=row["page_end"] if "page_end" in row.keys() else row["page"],
            section_id=row["section_id"],
            section_node_id=row["section_node_id"] if "section_node_id" in row.keys() else None,
            text=row["text"], hash=row["hash"],
            source_hash=row["source_hash"] if "source_hash" in row.keys() else row["hash"],
            block_ids=block_ids,
            kind=(row["chunk_type"] if "chunk_type" in row.keys() and row["chunk_type"] else attrs.get("chunk_type", "section")),
            chunk_type=(row["chunk_type"] if "chunk_type" in row.keys() else None),
            attrs=attrs,
        )

    def count_chunks(self) -> int:
        conn = self._connect()
        return int(conn.execute("SELECT COUNT(*) AS c FROM chunks").fetchone()["c"])

    def search_chunks_fts(self, query: str, limit: int = 20):
        """全文检索（FTS5 + LIKE 降级）。返回 [(chunk_id, snippet), ...]。

        策略：先 FTS5 MATCH；若返回空且 query 可能含 CJK（unicode61 不分词 →
        MATCH 空但不报错），自动降级到 LIKE 做子串匹配。
        """
        import re
        conn = self._connect()
        q = query.strip()
        if not q:
            return []
        _CJK = re.compile(r"[一-鿿㐀-䶿]")
        rows = []
        has_cjk = bool(_CJK.search(q))
        # 对纯 ASCII/拉丁 query 直接 MATCH；CJK 混合 query 需要 LIKE 降级
        if not has_cjk:
            try:
                rows = conn.execute(
                    "SELECT chunk_id, snippet(chunks_fts, 1, '【', '】', '…', 20) AS snip "
                    "FROM chunks_fts WHERE chunks_fts MATCH ? LIMIT ?",
                    (q, limit * 3),
                ).fetchall()
            except Exception:
                rows = []
        fallback = conn.execute(
            "SELECT id AS chunk_id, substr(text,1,240) AS snip "
            "FROM chunks WHERE text LIKE ? LIMIT ?",
            (f"%{q}%", limit * 3),
        ).fetchall()
        seen: set[str] = set()
        out: list[tuple[str, str]] = []
        for r in list(rows) + list(fallback):
            if r["chunk_id"] in seen:
                continue
            seen.add(r["chunk_id"])
            out.append((r["chunk_id"], r["snip"]))
            if len(out) >= limit * 3:
                break
        return out

    # ------- stats -------

    def count_nodes(self, kind: NodeKind | None = None) -> int:
        conn = self._connect()
        if kind is None:
            row = conn.execute("SELECT COUNT(*) AS c FROM nodes").fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM nodes WHERE kind = ?", (kind.value,)
            ).fetchone()
        return int(row["c"])

    def count_edges(self, kind: EdgeKind | None = None) -> int:
        conn = self._connect()
        if kind is None:
            row = conn.execute("SELECT COUNT(*) AS c FROM edges").fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM edges WHERE kind = ?", (kind.value,)
            ).fetchone()
        return int(row["c"])

    def list_docs(self) -> list[str]:
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT doc_id FROM nodes
            UNION
            SELECT doc_id FROM blocks
            UNION
            SELECT doc_id FROM chunks
            ORDER BY doc_id
            """
        ).fetchall()
        return [r["doc_id"] for r in rows]

    # ------- 行 → 对象 -------

    def _row_to_node(self, row: sqlite3.Row) -> Node:
        bbox = None
        if row["bbox"]:
            bbox = BBox(**json.loads(row["bbox"]))
        location = Location(
            page=row["page"],
            bbox=bbox,
            section_path=row["section_path"],
        )
        # 取别名
        conn = self._connect()
        aliases = [
            r["alias"]
            for r in conn.execute(
                "SELECT alias FROM aliases WHERE node_id = ?", (row["id"],)
            ).fetchall()
        ]
        return Node(
            schema_version=row["schema_version"] or 1,
            id=row["id"],
            kind=NodeKind(row["kind"]),
            name=row["name"],
            qualified_name=row["qualified_name"],
            aliases=aliases,
            doc_id=row["doc_id"],
            location=location,
            evidence=_row_evidence(row),
            attrs=json.loads(row["attrs"]) if row["attrs"] else {},
            summary=row["summary"],
            embedding_id=row["embedding_id"],
            hash=row["hash"],
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )

    def _row_to_edge(self, row: sqlite3.Row) -> Edge:
        evidence_data = json.loads(row["evidence"]) if row["evidence"] else {}
        return Edge(
            schema_version=row["schema_version"] or 1,
            src=row["src"],
            dst=row["dst"],
            kind=EdgeKind(row["kind"]),
            confidence=row["confidence"] or 1.0,
            evidence=Evidence(**evidence_data) if evidence_data else Evidence(extractor="unknown"),
            attrs=json.loads(row["attrs"]) if row["attrs"] else {},
            created_at=row["created_at"] or "",
        )


def _row_evidence(row: sqlite3.Row) -> Evidence:
    if "evidence" not in row.keys() or not row["evidence"]:
        return Evidence(extractor="unknown")
    evidence_data = json.loads(row["evidence"])
    return Evidence(**evidence_data) if evidence_data else Evidence(extractor="unknown")
