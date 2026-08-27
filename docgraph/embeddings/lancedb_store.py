"""LanceDB-backed vector store.

LanceDB is optional. The core project keeps SQLite JSON vectors as the zero
dependency default; this adapter is selected only when `storage.vector_backend`
is configured as `lancedb`.
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Any

from docgraph.embeddings.vector_store import _cosine


class LanceDBVectorStore:
    """VectorStore-compatible adapter backed by local LanceDB tables."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)
        self._db: Any | None = None

    def _connect(self):
        if self._db is None:
            try:
                import lancedb
            except ImportError as e:  # pragma: no cover - optional dependency
                raise RuntimeError(
                    "LanceDB vector backend requires lancedb. Install with: uv sync --extra lancedb"
                ) from e
            self._db = lancedb.connect(str(self.db_path))
        return self._db

    def init_schema(self) -> None:
        self._connect()
        self._ensure_table(
            "vec_nodes",
            {
                "node_id": "string",
                "model": "string",
                "dim": "int32",
                "vector": "float32_list",
                "content_hash": "string",
            },
        )
        self._ensure_table(
            "vec_items",
            {
                "namespace": "string",
                "item_id": "string",
                "model": "string",
                "dim": "int32",
                "vector": "float32_list",
                "content_hash": "string",
            },
        )

    def _ensure_table(self, name: str, fields: dict[str, str]) -> None:
        db = self._connect()
        if name in set(db.table_names()):
            table = db.open_table(name)
            schema = table.schema
            if callable(schema):
                schema = schema()
            if set(fields).issubset(set(schema.names)):
                return
            # Vector data is derived and can be recreated safely when the
            # local table predates the current schema.
            db.drop_table(name)
        import pyarrow as pa

        pa_fields = []
        for field_name, field_type in fields.items():
            if field_type == "string":
                pa_fields.append(pa.field(field_name, pa.string()))
            elif field_type == "int32":
                pa_fields.append(pa.field(field_name, pa.int32()))
            elif field_type == "float32_list":
                pa_fields.append(pa.field(field_name, pa.list_(pa.float32())))
        schema = pa.schema(pa_fields)
        db.create_table(name, schema=schema)

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
        table = self._table("vec_nodes")
        ids = ", ".join(f"'{_sql_quote(node_id)}'" for node_id, _model, _vec, _hash in entries)
        table.delete(f"node_id IN ({ids})")
        table.add(
            [
                {
                    "node_id": node_id,
                    "model": model,
                    "dim": len(vector),
                    "vector": _float32(vector),
                    "content_hash": content_hash,
                }
                for node_id, model, vector, content_hash in entries
            ]
        )

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
        table = self._table("vec_items")
        ids = ", ".join(f"'{_sql_quote(item_id)}'" for item_id, _model, _vec, _hash in entries)
        table.delete(f"namespace = '{_sql_quote(namespace)}' AND item_id IN ({ids})")
        table.add(
            [
                {
                    "namespace": namespace,
                    "item_id": item_id,
                    "model": model,
                    "dim": len(vector),
                    "vector": _float32(vector),
                    "content_hash": content_hash,
                }
                for item_id, model, vector, content_hash in entries
            ]
        )

    def delete(self, node_id: str) -> None:
        self._table("vec_nodes").delete(f"node_id = '{_sql_quote(node_id)}'")

    def delete_by_doc(self, doc_ids: list[str], graph_db: Path) -> int:
        if not doc_ids:
            return 0
        placeholders = ",".join("?" * len(doc_ids))
        with sqlite3.connect(str(graph_db)) as conn:
            node_ids = {
                row[0]
                for row in conn.execute(
                    f"SELECT id FROM nodes WHERE doc_id IN ({placeholders})", doc_ids
                ).fetchall()
            }
            chunk_ids = {
                row[0]
                for row in conn.execute(
                    f"SELECT id FROM chunks WHERE doc_id IN ({placeholders})", doc_ids
                ).fetchall()
            }
        node_table = self._table("vec_nodes")
        item_table = self._table("vec_items")
        for node_id in node_ids:
            node_table.delete(f"node_id = '{_sql_quote(node_id)}'")
        for chunk_id in chunk_ids:
            item_table.delete(f"namespace = 'chunk' AND item_id = '{_sql_quote(chunk_id)}'")
        return len(node_ids) + len(chunk_ids)

    def stored_node_hashes(self, model: str) -> dict[str, str | None]:
        return {
            str(row["node_id"]): row.get("content_hash")
            for row in self._all_rows("vec_nodes")
            if row.get("model") == model
        }

    def stored_item_hashes(self, namespace: str, model: str) -> dict[str, str | None]:
        return {
            str(row["item_id"]): row.get("content_hash")
            for row in self._all_rows("vec_items")
            if row.get("namespace") == namespace and row.get("model") == model
        }

    def prune(self, node_ids: set[str], namespace: str, item_ids: set[str]) -> int:
        node_rows = self._all_rows("vec_nodes")
        item_rows = self._all_rows("vec_items")
        stale_nodes = {
            str(row["node_id"]) for row in node_rows if str(row["node_id"]) not in node_ids
        }
        stale_items = {
            str(row["item_id"])
            for row in item_rows
            if row.get("namespace") == namespace and str(row["item_id"]) not in item_ids
        }
        node_table = self._table("vec_nodes")
        item_table = self._table("vec_items")
        for node_id in stale_nodes:
            node_table.delete(f"node_id = '{_sql_quote(node_id)}'")
        for item_id in stale_items:
            item_table.delete(
                f"namespace = '{_sql_quote(namespace)}' AND item_id = '{_sql_quote(item_id)}'"
            )
        return len(stale_nodes) + len(stale_items)

    def all_for_model(self, model: str) -> list[tuple[str, list[float]]]:
        rows = [r for r in self._all_rows("vec_nodes") if r.get("model") == model]
        return [(str(r["node_id"]), list(r["vector"])) for r in rows]

    def all_items_for_model(self, namespace: str, model: str) -> list[tuple[str, list[float]]]:
        rows = [
            r
            for r in self._all_rows("vec_items")
            if r.get("namespace") == namespace and r.get("model") == model
        ]
        return [(str(r["item_id"]), list(r["vector"])) for r in rows]

    def count(self) -> int:
        return len(self._all_rows("vec_nodes")) + len(self._all_rows("vec_items"))

    def count_items(self, namespace: str | None = None) -> int:
        rows = self._all_rows("vec_items")
        if namespace is None:
            return len(rows)
        return sum(1 for r in rows if r.get("namespace") == namespace)

    def search(
        self, query_vec: list[float], model: str, top_k: int = 10
    ) -> list[tuple[str, float]]:
        native = self._native_search("vec_nodes", query_vec, model, top_k)
        if native is not None:
            return [(str(r["node_id"]), _distance_to_score(r)) for r in native]
        rows = self.all_for_model(model)
        return _cosine_top_k(rows, query_vec, top_k)

    def search_items(
        self,
        namespace: str,
        query_vec: list[float],
        model: str,
        top_k: int = 10,
        allowed_ids: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        if allowed_ids is not None:
            rows = [
                row for row in self.all_items_for_model(namespace, model) if row[0] in allowed_ids
            ]
            return _cosine_top_k(rows, query_vec, top_k)
        native = self._native_search(
            "vec_items",
            query_vec,
            model,
            top_k,
            where=f"namespace = '{_sql_quote(namespace)}' AND model = '{_sql_quote(model)}'",
        )
        if native is not None:
            return [(str(r["item_id"]), _distance_to_score(r)) for r in native]
        rows = self.all_items_for_model(namespace, model)
        return _cosine_top_k(rows, query_vec, top_k)

    def close(self) -> None:
        self._db = None

    def _table(self, name: str):
        return self._connect().open_table(name)

    def _all_rows(self, table_name: str) -> list[dict[str, Any]]:
        table = self._table(table_name)
        try:
            return list(table.to_list())
        except AttributeError:  # pragma: no cover - compatibility with older LanceDB
            return table.to_pandas().to_dict("records")

    def _native_search(
        self,
        table_name: str,
        query_vec: list[float],
        model: str,
        top_k: int,
        *,
        where: str | None = None,
    ) -> list[dict[str, Any]] | None:
        try:
            table = self._table(table_name)
            q = table.search(_float32(query_vec)).distance_type("cosine")
            if where is None:
                where = f"model = '{_sql_quote(model)}'"
            q = q.where(where, prefilter=True).limit(top_k)
            return list(q.to_list())
        except Exception:
            return None


def _cosine_top_k(
    rows: list[tuple[str, list[float]]],
    query_vec: list[float],
    top_k: int,
) -> list[tuple[str, float]]:
    results = [(item_id, _cosine(query_vec, vec)) for item_id, vec in rows]
    results.sort(key=lambda kv: kv[1], reverse=True)
    return results[:top_k]


def _distance_to_score(row: dict[str, Any]) -> float:
    if "_score" in row:
        try:
            return float(row["_score"])
        except Exception:
            pass
    if "_distance" in row:
        try:
            distance = float(row["_distance"])
            if math.isfinite(distance):
                return max(-1.0, min(1.0, 1.0 - distance))
        except Exception:
            pass
    return 0.0


def _float32(values: list[float]) -> list[float]:
    return [float(v) for v in values]


def _sql_quote(value: str) -> str:
    return value.replace("'", "''")
