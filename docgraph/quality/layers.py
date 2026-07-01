"""L0/L1 production quality checks.

The checks here are deliberately storage-level. They do not depend on L2 nodes,
so they can be used after a parser/chunker-only build to prove that the lossless
layout and retrieval substrate are intact.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from docgraph.graph.schema import NodeKind
from docgraph.graph.sqlite_store import SQLiteGraphStore


_L2_PROVENANCE_KINDS = {
    NodeKind.REGISTER.value,
    NodeKind.BITFIELD.value,
    NodeKind.PIN.value,
    NodeKind.SIGNAL.value,
    NodeKind.MODULE.value,
    NodeKind.INTERFACE.value,
    NodeKind.PARAMETER.value,
    NodeKind.INTERRUPT.value,
    NodeKind.CLOCK.value,
    NodeKind.POWER_DOMAIN.value,
    NodeKind.MEMORY_MAP.value,
    NodeKind.REQUIREMENT.value,
    NodeKind.ERRATA.value,
    NodeKind.FIGURE.value,
}


@dataclass
class LayerIssue:
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
class LayerQualityReport:
    ok: bool
    totals: dict[str, Any]
    by_doc: list[dict[str, Any]]
    issues: list[LayerIssue] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "totals": self.totals,
            "by_doc": self.by_doc,
            "issues": [i.as_dict() for i in self.issues],
        }


def audit_l0_l1(store: SQLiteGraphStore, *, table_cell_warn_ratio: float = 0.98) -> LayerQualityReport:
    """Audit L0/L1 invariants.

    Production-grade acceptance starts with these invariants:
    - every document has L0 blocks and L1 chunks
    - every chunk points back to existing L0 block IDs
    - chunk text / page range / FTS rows are consistent
    - table cell preservation is measured and warned when below threshold
    """
    conn = store._connect()
    blocks = conn.execute("SELECT * FROM blocks").fetchall()
    chunks = conn.execute("SELECT * FROM chunks").fetchall()
    nodes = conn.execute("SELECT id, kind, doc_id, attrs, evidence FROM nodes").fetchall()
    fts_count = int(conn.execute("SELECT COUNT(*) AS c FROM chunks_fts").fetchone()["c"])

    block_ids = {r["id"] for r in blocks}
    chunk_ids = {r["id"] for r in chunks}
    docs = sorted({r["doc_id"] for r in blocks} | {r["doc_id"] for r in chunks})
    by_doc: dict[str, dict[str, Any]] = {
        doc_id: {
            "doc_id": doc_id,
            "blocks": 0,
            "chunks": 0,
            "block_kinds": Counter(),
            "chunk_kinds": Counter(),
            "tables": 0,
            "tables_with_cells": 0,
            "tables_with_evidence": 0,
            "figures": 0,
            "figures_with_image": 0,
            "figures_with_evidence": 0,
            "chunks_with_block_ids": 0,
            "chunks_with_section_id": 0,
            "chunks_with_section_node_id": 0,
            "multi_page_chunks": 0,
            "table_profiles": Counter(),
            "l2_nodes": 0,
            "l2_nodes_with_source_blocks": 0,
            "l2_nodes_with_source_chunks": 0,
            "l2_nodes_with_evidence": 0,
            "l2_nodes_structurally_valid": 0,
        }
        for doc_id in docs
    }
    issues: list[LayerIssue] = []

    page_orders: dict[tuple[str, int], list[int]] = defaultdict(list)
    for r in blocks:
        doc = by_doc[r["doc_id"]]
        doc["blocks"] += 1
        doc["block_kinds"][r["kind"]] += 1
        page_orders[(r["doc_id"], r["page"])].append(int(r["reading_order"] or 0))
        if r["kind"] == "table":
            doc["tables"] += 1
            data = json.loads(r["table_json"]) if r["table_json"] else None
            if data and (data.get("headers") or data.get("rows")):
                doc["tables_with_cells"] += 1
            if (data and (data.get("headers") or data.get("rows"))) or r["image_path"]:
                doc["tables_with_evidence"] += 1
        if r["kind"] == "figure":
            doc["figures"] += 1
            if r["image_path"]:
                doc["figures_with_image"] += 1
            bbox = json.loads(r["bbox"]) if r["bbox"] else {}
            has_visual_bbox = _bbox_area(bbox) > 1.0
            if r["image_path"] or (not has_visual_bbox and (r["text"] or "").strip()):
                doc["figures_with_evidence"] += 1

    missing_block_refs: dict[str, list[str]] = defaultdict(list)
    empty_chunks: dict[str, list[str]] = defaultdict(list)
    orphan_chunks: dict[str, list[str]] = defaultdict(list)
    bad_page_ranges: dict[str, list[str]] = defaultdict(list)
    missing_source_hash: dict[str, list[str]] = defaultdict(list)
    for r in chunks:
        doc = by_doc[r["doc_id"]]
        attrs = json.loads(r["attrs"]) if r["attrs"] else {}
        block_refs = json.loads(r["block_ids"]) if r["block_ids"] else []
        chunk_type = r["chunk_type"] or attrs.get("chunk_type") or "section"
        doc["chunks"] += 1
        doc["chunk_kinds"][chunk_type] += 1
        if block_refs:
            doc["chunks_with_block_ids"] += 1
        else:
            orphan_chunks[r["doc_id"]].append(r["id"])
        missing = [bid for bid in block_refs if bid not in block_ids]
        if missing:
            missing_block_refs[r["doc_id"]].append(r["id"])
        if not (r["text"] or "").strip():
            empty_chunks[r["doc_id"]].append(r["id"])
        if r["page_start"] and r["page_end"] and int(r["page_start"]) > int(r["page_end"]):
            bad_page_ranges[r["doc_id"]].append(r["id"])
        if not r["source_hash"]:
            missing_source_hash[r["doc_id"]].append(r["id"])
        if r["section_id"]:
            doc["chunks_with_section_id"] += 1
        if r["section_node_id"]:
            doc["chunks_with_section_node_id"] += 1
        if r["page_start"] and r["page_end"] and r["page_start"] != r["page_end"]:
            doc["multi_page_chunks"] += 1
        if chunk_type in {"table", "logical_table"}:
            profile = (attrs.get("table_profile") or {}).get("kind", "none")
            doc["table_profiles"][profile] += 1

    for doc_id, doc in by_doc.items():
        if doc["blocks"] == 0:
            issues.append(LayerIssue("error", "l0.empty_doc", "document has no L0 blocks", doc_id))
        if doc["chunks"] == 0:
            issues.append(LayerIssue("error", "l1.empty_doc", "document has no L1 chunks", doc_id))
        if doc["tables"]:
            ratio = doc["tables_with_cells"] / doc["tables"]
            evidence_ratio = doc["tables_with_evidence"] / doc["tables"]
            if evidence_ratio < 1.0:
                issues.append(LayerIssue(
                    "error",
                    "l0.table_evidence_missing",
                    f"table evidence coverage {evidence_ratio:.1%}",
                    doc_id,
                ))
            if ratio < table_cell_warn_ratio:
                issues.append(LayerIssue(
                    "warning",
                    "l0.table_cell_coverage",
                    f"table cell coverage {ratio:.1%} below {table_cell_warn_ratio:.1%}; raw evidence coverage {evidence_ratio:.1%}",
                    doc_id,
                ))
        if doc["figures"] and doc["figures_with_evidence"] < doc["figures"]:
            issues.append(LayerIssue(
                "error",
                "l0.figure_image_missing",
                f"{doc['figures'] - doc['figures_with_evidence']} figure blocks have no image_path or caption-only evidence",
                doc_id,
            ))

    if fts_count != len(chunks):
        issues.append(LayerIssue(
            "error",
            "l1.fts_mismatch",
            f"chunks_fts rows={fts_count}, chunks rows={len(chunks)}",
        ))

    _audit_l2_provenance(nodes, by_doc, block_ids, chunk_ids, issues)
    _audit_l2_structure(nodes, by_doc, issues)
    _extend_sample_issues(issues, "error", "l1.orphan_chunk", "chunks without block_ids", orphan_chunks)
    _extend_sample_issues(issues, "error", "l1.missing_block_ref", "chunks reference missing L0 block IDs", missing_block_refs)
    _extend_sample_issues(issues, "error", "l1.empty_chunk_text", "chunks have empty text", empty_chunks)
    _extend_sample_issues(issues, "error", "l1.bad_page_range", "chunks have invalid page range", bad_page_ranges)
    _extend_sample_issues(issues, "warning", "l1.missing_source_hash", "chunks missing source_hash", missing_source_hash)

    for (doc_id, page), orders in page_orders.items():
        if orders != sorted(orders):
            issues.append(LayerIssue(
                "warning",
                "l0.reading_order_unsorted",
                f"reading_order is not monotonic on page {page}",
                doc_id,
            ))
            break

    by_doc_rows = [_freeze_doc_stats(by_doc[doc_id]) for doc_id in docs]
    totals = _totals(by_doc_rows, fts_count=fts_count)
    ok = not any(i.severity == "error" for i in issues)
    return LayerQualityReport(ok=ok, totals=totals, by_doc=by_doc_rows, issues=issues)


def _extend_sample_issues(
    issues: list[LayerIssue],
    severity: str,
    code: str,
    message: str,
    grouped: dict[str, list[str]],
) -> None:
    for doc_id, ids in grouped.items():
        if ids:
            issues.append(LayerIssue(severity, code, f"{message}: {len(ids)}", doc_id, ids[:5]))


def _freeze_doc_stats(doc: dict[str, Any]) -> dict[str, Any]:
    out = dict(doc)
    out["block_kinds"] = dict(sorted(doc["block_kinds"].items()))
    out["chunk_kinds"] = dict(sorted(doc["chunk_kinds"].items()))
    out["table_profiles"] = dict(sorted(doc["table_profiles"].items()))
    return out


def _totals(rows: list[dict[str, Any]], *, fts_count: int) -> dict[str, Any]:
    total = Counter()
    block_kinds: Counter[str] = Counter()
    chunk_kinds: Counter[str] = Counter()
    table_profiles: Counter[str] = Counter()
    for row in rows:
        for key in (
            "blocks", "chunks", "tables", "tables_with_cells", "tables_with_evidence",
            "figures", "figures_with_image", "figures_with_evidence",
            "chunks_with_block_ids", "chunks_with_section_id",
            "chunks_with_section_node_id", "multi_page_chunks",
            "l2_nodes", "l2_nodes_with_source_blocks", "l2_nodes_with_source_chunks",
            "l2_nodes_with_evidence", "l2_nodes_structurally_valid",
        ):
            total[key] += int(row.get(key) or 0)
        block_kinds.update(row.get("block_kinds") or {})
        chunk_kinds.update(row.get("chunk_kinds") or {})
        table_profiles.update(row.get("table_profiles") or {})
    total["docs"] = len(rows)
    total["chunks_fts"] = fts_count
    total["block_kinds"] = dict(sorted(block_kinds.items()))
    total["chunk_kinds"] = dict(sorted(chunk_kinds.items()))
    total["table_profiles"] = dict(sorted(table_profiles.items()))
    return dict(total)


def _bbox_area(bbox: dict[str, Any]) -> float:
    try:
        return max(0.0, float(bbox.get("x1", 0)) - float(bbox.get("x0", 0))) * max(
            0.0,
            float(bbox.get("y1", 0)) - float(bbox.get("y0", 0)),
        )
    except Exception:
        return 0.0


def _audit_l2_provenance(
    nodes,
    by_doc: dict[str, dict[str, Any]],
    block_ids: set[str],
    chunk_ids: set[str],
    issues: list[LayerIssue],
) -> None:
    missing_blocks: dict[str, list[str]] = defaultdict(list)
    missing_chunks: dict[str, list[str]] = defaultdict(list)
    bad_blocks: dict[str, list[str]] = defaultdict(list)
    bad_chunks: dict[str, list[str]] = defaultdict(list)
    missing_evidence: dict[str, list[str]] = defaultdict(list)

    for row in nodes:
        if row["kind"] not in _L2_PROVENANCE_KINDS:
            continue
        doc = by_doc.setdefault(row["doc_id"], {
            "doc_id": row["doc_id"],
            "blocks": 0,
            "chunks": 0,
            "block_kinds": Counter(),
            "chunk_kinds": Counter(),
            "tables": 0,
            "tables_with_cells": 0,
            "tables_with_evidence": 0,
            "figures": 0,
            "figures_with_image": 0,
            "figures_with_evidence": 0,
            "chunks_with_block_ids": 0,
            "chunks_with_section_id": 0,
            "chunks_with_section_node_id": 0,
            "multi_page_chunks": 0,
            "table_profiles": Counter(),
            "l2_nodes": 0,
            "l2_nodes_with_source_blocks": 0,
            "l2_nodes_with_source_chunks": 0,
            "l2_nodes_with_evidence": 0,
            "l2_nodes_structurally_valid": 0,
        })
        doc["l2_nodes"] += 1
        attrs = json.loads(row["attrs"]) if row["attrs"] else {}
        evidence = json.loads(row["evidence"]) if row["evidence"] else {}
        if evidence.get("extractor") and evidence.get("extractor") != "unknown":
            doc["l2_nodes_with_evidence"] += 1
        else:
            missing_evidence[row["doc_id"]].append(row["id"])
        source_block_ids = attrs.get("source_block_ids") or attrs.get("block_ids") or []
        source_chunk_ids = attrs.get("source_chunk_ids") or attrs.get("chunk_ids") or []
        if source_block_ids:
            doc["l2_nodes_with_source_blocks"] += 1
            if any(bid not in block_ids for bid in source_block_ids):
                bad_blocks[row["doc_id"]].append(row["id"])
        else:
            missing_blocks[row["doc_id"]].append(row["id"])
        if source_chunk_ids:
            doc["l2_nodes_with_source_chunks"] += 1
            if any(cid not in chunk_ids for cid in source_chunk_ids):
                bad_chunks[row["doc_id"]].append(row["id"])
        else:
            missing_chunks[row["doc_id"]].append(row["id"])

    _extend_sample_issues(issues, "error", "l2.missing_source_blocks", "L2 nodes missing source_block_ids", missing_blocks)
    _extend_sample_issues(issues, "error", "l2.missing_source_chunks", "L2 nodes missing source_chunk_ids", missing_chunks)
    _extend_sample_issues(issues, "error", "l2.bad_source_blocks", "L2 nodes reference missing L0 block IDs", bad_blocks)
    _extend_sample_issues(issues, "error", "l2.bad_source_chunks", "L2 nodes reference missing L1 chunk IDs", bad_chunks)
    _extend_sample_issues(issues, "error", "l2.missing_evidence", "L2 nodes missing real evidence extractor", missing_evidence)


def _audit_l2_structure(nodes, by_doc: dict[str, dict[str, Any]], issues: list[LayerIssue]) -> None:
    """Validate strong L2 entity invariants that should never rely on LLM trust."""
    rows = {row["id"]: row for row in nodes}
    attrs_by_id = {
        row["id"]: (json.loads(row["attrs"]) if row["attrs"] else {})
        for row in nodes
    }
    invalid_bit_ranges: dict[str, list[str]] = defaultdict(list)
    invalid_bit_widths: dict[str, list[str]] = defaultdict(list)
    missing_register_refs: dict[str, list[str]] = defaultdict(list)
    overlapping_bitfields: dict[str, list[str]] = defaultdict(list)
    weak_access_values: dict[str, list[str]] = defaultdict(list)
    valid_node_ids: set[str] = set()

    bitfields_by_register: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for row in nodes:
        kind = row["kind"]
        attrs = attrs_by_id[row["id"]]
        if kind == NodeKind.REGISTER.value:
            valid_node_ids.add(row["id"])
            width = _parse_int(attrs.get("width"), default=32)
            if width is None or width <= 0:
                invalid_bit_widths[row["doc_id"]].append(row["id"])
            access = attrs.get("access")
            if access and not _looks_like_access(access):
                weak_access_values[row["doc_id"]].append(row["id"])
        elif kind == NodeKind.BITFIELD.value:
            reg_id = attrs.get("register_id")
            if not reg_id or reg_id not in rows:
                missing_register_refs[row["doc_id"]].append(row["id"])
                continue
            bit_high = _parse_int(attrs.get("bit_high"))
            bit_low = _parse_int(attrs.get("bit_low"))
            if bit_high is None or bit_low is None or bit_high < bit_low:
                invalid_bit_ranges[row["doc_id"]].append(row["id"])
                continue
            reg_attrs = attrs_by_id.get(reg_id, {})
            width = _parse_int(reg_attrs.get("width"), default=32)
            if width is not None and bit_high >= width:
                invalid_bit_widths[row["doc_id"]].append(row["id"])
                continue
            access = attrs.get("access")
            if access and not _looks_like_access(access):
                weak_access_values[row["doc_id"]].append(row["id"])
            bitfields_by_register[reg_id].append((bit_low, bit_high, row["id"]))
            valid_node_ids.add(row["id"])

    for reg_id, ranges in bitfields_by_register.items():
        ranges.sort()
        prev_low = prev_high = None
        prev_id = ""
        for low, high, node_id in ranges:
            if prev_low is not None and low <= prev_high:
                doc_id = rows[node_id]["doc_id"]
                overlapping_bitfields[doc_id].extend([prev_id, node_id])
            prev_low, prev_high, prev_id = low, high, node_id

    invalid_ids = {
        node_id
        for grouped in (
            invalid_bit_ranges,
            invalid_bit_widths,
            missing_register_refs,
            overlapping_bitfields,
        )
        for ids in grouped.values()
        for node_id in ids
    }
    for node_id in valid_node_ids - invalid_ids:
        doc_id = rows[node_id]["doc_id"]
        if doc_id in by_doc:
            by_doc[doc_id]["l2_nodes_structurally_valid"] += 1

    _extend_sample_issues(issues, "error", "l2.invalid_bit_range", "bitfields have invalid bit ranges", invalid_bit_ranges)
    _extend_sample_issues(issues, "error", "l2.invalid_bit_width", "register or bitfield width constraints are invalid", invalid_bit_widths)
    _extend_sample_issues(issues, "error", "l2.missing_register_ref", "bitfields reference missing registers", missing_register_refs)
    _extend_sample_issues(issues, "error", "l2.overlapping_bitfields", "bitfields overlap within the same register", overlapping_bitfields)
    _extend_sample_issues(issues, "warning", "l2.weak_access_value", "access values are not normalized", weak_access_values)


def _parse_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip(), 0)
    except Exception:
        return default


def _looks_like_access(value: Any) -> bool:
    text = str(value).strip().upper().replace(" ", "")
    if not text:
        return True
    allowed = {
        "R", "W", "RW", "RO", "WO", "W1C", "W1S", "W0C", "RC", "RS", "WC",
        "READ", "WRITE", "READONLY", "WRITEONLY", "R/W", "R/O", "W/O",
        "R/W1C", "RW1C",
    }
    return text in allowed
