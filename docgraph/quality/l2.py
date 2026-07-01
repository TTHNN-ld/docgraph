"""L2 candidate coverage audit.

This module is intentionally model-free: it never calls LLM/VLM. It answers
whether L0/L1 contain extractable candidates and which registered schemas would
be routed to those candidates before any paid enrichment happens.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from docgraph.extractors.schema_registry import get_schema, schemas_for_doctype
from docgraph.extractors.table_entity import TableEntityExtractor
from docgraph.graph.schema import Block, BlockKind, DocType, TableData
from docgraph.graph.sqlite_store import SQLiteGraphStore


@dataclass
class L2AuditIssue:
    severity: str
    code: str
    message: str
    doc_id: str | None = None
    sample_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "doc_id": self.doc_id,
            "sample_ids": self.sample_ids,
        }


@dataclass
class L2AuditReport:
    ok: bool
    totals: dict[str, Any]
    by_doc: list[dict[str, Any]]
    by_schema: list[dict[str, Any]]
    issues: list[L2AuditIssue] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "totals": self.totals,
            "by_doc": self.by_doc,
            "by_schema": self.by_schema,
            "issues": [i.as_dict() for i in self.issues],
        }


def audit_l2_candidates(
    store: SQLiteGraphStore,
    *,
    schema_names: list[str] | None = None,
) -> L2AuditReport:
    """Audit L2 candidate coverage from persisted L0/L1 data."""
    conn = store._connect()
    chunks = conn.execute("SELECT * FROM chunks").fetchall()
    blocks = conn.execute("SELECT * FROM blocks").fetchall()
    nodes = conn.execute("SELECT kind, doc_id, attrs FROM nodes").fetchall()

    block_by_id = {r["id"]: _row_to_block(r) for r in blocks}
    doc_types = _doc_types_by_id(conn)
    docs = sorted({r["doc_id"] for r in chunks} | {r["doc_id"] for r in blocks})
    for doc_id in docs:
        doc_types.setdefault(doc_id, _infer_doc_type_from_id(doc_id).value)
    selected_schemas = schema_names or []

    by_doc: dict[str, dict[str, Any]] = {
        doc_id: {
            "doc_id": doc_id,
            "doc_type": doc_types.get(doc_id, DocType.UNKNOWN.value),
            "chunks": 0,
            "table_chunks": 0,
            "text_chunks": 0,
            "figure_chunks": 0,
            "candidates_total": 0,
            "table_candidates": 0,
            "text_candidates": 0,
            "figure_candidates": 0,
            "table_schema_hits": 0,
            "text_schema_hits": 0,
            "schemas_hit": Counter(),
            "l2_nodes": 0,
        }
        for doc_id in docs
    }
    by_schema: dict[str, dict[str, Any]] = {}
    issues: list[L2AuditIssue] = []
    skipped_tables: dict[str, list[str]] = defaultdict(list)
    skipped_texts: dict[str, list[str]] = defaultdict(list)

    for row in chunks:
        doc_id = row["doc_id"]
        doc = by_doc[doc_id]
        doc["chunks"] += 1
        attrs = json.loads(row["attrs"]) if row["attrs"] else {}
        chunk_type = row["chunk_type"] or attrs.get("chunk_type") or "section"
        block_ids = json.loads(row["block_ids"]) if row["block_ids"] else []
        chunk_blocks = [block_by_id[bid] for bid in block_ids if bid in block_by_id]
        schemas = _schemas_for_doc(doc_types.get(doc_id), selected_schemas)

        if chunk_type in {"table", "logical_table"}:
            doc["table_chunks"] += 1
            table_blocks = [
                block for block in chunk_blocks
                if block.kind == BlockKind.TABLE and block.table is not None
            ]
            if not table_blocks:
                skipped_tables[doc_id].append(row["id"])
                continue
            doc["candidates_total"] += 1
            doc["table_candidates"] += 1
            table = _merge_table_data(table_blocks)
            matched = False
            for schema_name, schema in schemas:
                srow = _schema_row(by_schema, schema_name)
                srow["table_candidates_seen"] += 1
                if TableEntityExtractor._table_matches(table, schema):
                    matched = True
                    doc["table_schema_hits"] += 1
                    doc["schemas_hit"][schema_name] += 1
                    srow["table_candidates_matched"] += 1
                    srow["candidate_docs"].add(doc_id)
            if not matched:
                skipped_tables[doc_id].append(row["id"])
            continue

        if chunk_type == "figure":
            doc["figure_chunks"] += 1
            if any(block.kind == BlockKind.FIGURE for block in chunk_blocks):
                doc["candidates_total"] += 1
                doc["figure_candidates"] += 1
            continue

        doc["text_chunks"] += 1
        text = row["text"] or ""
        if not text.strip():
            skipped_texts[doc_id].append(row["id"])
            continue
        doc["candidates_total"] += 1
        doc["text_candidates"] += 1
        matched = False
        for schema_name, schema in schemas:
            srow = _schema_row(by_schema, schema_name)
            srow["text_candidates_seen"] += 1
            if TableEntityExtractor._text_looks_like_entity(text, schema):
                matched = True
                doc["text_schema_hits"] += 1
                doc["schemas_hit"][schema_name] += 1
                srow["text_candidates_matched"] += 1
                srow["candidate_docs"].add(doc_id)
        if not matched:
            skipped_texts[doc_id].append(row["id"])

    for row in nodes:
        attrs = json.loads(row["attrs"]) if row["attrs"] else {}
        schema_name = attrs.get("schema_name")
        if schema_name:
            _schema_row(by_schema, schema_name)["l2_nodes"] += 1
        if row["doc_id"] in by_doc and schema_name:
            by_doc[row["doc_id"]]["l2_nodes"] += 1

    for doc_id, doc in by_doc.items():
        if doc["candidates_total"] == 0:
            issues.append(L2AuditIssue(
                "warning",
                "l2.no_candidates",
                "document has no L2 candidates from persisted L0/L1",
                doc_id,
            ))
        if doc["table_candidates"] and doc["table_schema_hits"] == 0:
            issues.append(L2AuditIssue(
                "warning",
                "l2.table_no_schema_hits",
                "table candidates exist but no enabled schema matched",
                doc_id,
                skipped_tables.get(doc_id, [])[:5],
            ))

    for schema_name, row in by_schema.items():
        if row["table_candidates_matched"] + row["text_candidates_matched"] == 0:
            issues.append(L2AuditIssue(
                "warning",
                "l2.schema_no_candidates",
                f"schema '{schema_name}' has no matched candidates",
            ))

    by_doc_rows = [_freeze_doc_row(doc) for doc in by_doc.values()]
    by_schema_rows = [_freeze_schema_row(row) for row in by_schema.values()]
    totals = _totals(by_doc_rows, by_schema_rows)
    ok = not any(issue.severity == "error" for issue in issues)
    return L2AuditReport(ok=ok, totals=totals, by_doc=by_doc_rows, by_schema=by_schema_rows, issues=issues)


def _schemas_for_doc(
    doc_type_value: str | None,
    selected_schemas: list[str],
) -> list[tuple[str, Any]]:
    names = selected_schemas or schemas_for_doctype(doc_type_value or DocType.UNKNOWN)
    out = []
    for name in names:
        schema = get_schema(name)
        if schema is not None:
            out.append((name, schema))
    return out


def _schema_row(rows: dict[str, dict[str, Any]], schema_name: str) -> dict[str, Any]:
    if schema_name not in rows:
        rows[schema_name] = {
            "schema": schema_name,
            "table_candidates_seen": 0,
            "table_candidates_matched": 0,
            "text_candidates_seen": 0,
            "text_candidates_matched": 0,
            "candidate_docs": set(),
            "l2_nodes": 0,
        }
    return rows[schema_name]


def _doc_types_by_id(conn) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        rows = conn.execute("SELECT id, attrs FROM nodes WHERE kind = 'document'").fetchall()
    except Exception:
        return out
    for row in rows:
        attrs = json.loads(row["attrs"]) if row["attrs"] else {}
        doc_type = attrs.get("type") or attrs.get("doc_type")
        if doc_type:
            out[row["id"]] = doc_type
    return out


def _infer_doc_type_from_id(doc_id: str) -> DocType:
    name = doc_id.lower()
    if "errata" in name:
        return DocType.ERRATA
    if "trm" in name or "reference" in name or "manual" in name:
        return DocType.REFERENCE_MANUAL
    if "datasheet" in name or "数据表" in name:
        return DocType.DATASHEET
    if "user" in name and "guide" in name:
        return DocType.USER_GUIDE
    if "app" in name and "note" in name:
        return DocType.APP_NOTE
    if "protocol" in name or "subsystem spec" in name:
        return DocType.PROTOCOL
    return DocType.UNKNOWN


def _row_to_block(row) -> Block:
    table = TableData(**json.loads(row["table_json"])) if row["table_json"] else None
    return Block(
        id=row["id"],
        doc_id=row["doc_id"],
        page=row["page"],
        kind=BlockKind(row["kind"]),
        reading_order=row["reading_order"] or 0,
        text=row["text"],
        table=table,
        image_path=row["image_path"],
        latex=row["latex"],
        section_path=row["section_path"],
        heading_level=row["heading_level"],
        attrs=json.loads(row["attrs"]) if row["attrs"] else {},
    )


def _merge_table_data(blocks: list[Block]) -> TableData:
    if len(blocks) == 1 and blocks[0].table is not None:
        return blocks[0].table
    headers: list[str] = []
    rows: list[list[str]] = []
    captions: list[str] = []
    html_parts: list[str] = []
    for block in blocks:
        table = block.table
        if table is None:
            continue
        if not headers and table.headers:
            headers = list(table.headers)
        rows.extend(table.rows or [])
        if table.caption:
            captions.append(table.caption)
        if table.html:
            html_parts.append(table.html)
    return TableData(
        headers=headers,
        rows=rows,
        n_rows=len(rows),
        n_cols=max([len(headers), *(len(row) for row in rows)] or [0]),
        caption="\n".join(dict.fromkeys(captions)) or None,
        html="\n".join(html_parts) or None,
    )


def _freeze_doc_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["schemas_hit"] = dict(sorted(row["schemas_hit"].items()))
    return out


def _freeze_schema_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["candidate_docs"] = sorted(row["candidate_docs"])
    out["candidate_doc_count"] = len(out["candidate_docs"])
    return out


def _totals(doc_rows: list[dict[str, Any]], schema_rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = Counter()
    schemas_hit: Counter[str] = Counter()
    for row in doc_rows:
        for key in (
            "chunks", "table_chunks", "text_chunks", "figure_chunks",
            "candidates_total", "table_candidates", "text_candidates",
            "figure_candidates", "table_schema_hits", "text_schema_hits",
            "l2_nodes",
        ):
            total[key] += int(row.get(key) or 0)
        schemas_hit.update(row.get("schemas_hit") or {})
    total["docs"] = len(doc_rows)
    total["schemas_with_candidates"] = sum(
        1 for row in schema_rows
        if row["table_candidates_matched"] + row["text_candidates_matched"] > 0
    )
    total["schemas_hit"] = dict(sorted(schemas_hit.items()))
    return dict(total)
