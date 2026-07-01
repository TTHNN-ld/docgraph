"""Batch evaluate DocGraph L0/L1 parsing quality for local PDFs.

This script intentionally stops before L2 extraction. It is meant for checking
whether parser output and chunking are good enough as the retrieval substrate.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

from docgraph.core.bootstrap import bootstrap
from docgraph.core.config import docgraph_dir, load_config, project_root_from_cwd
from docgraph.core.dotenv import autoload_env
from docgraph.core.ids import file_hash
from docgraph.core.manifest import FileRecord
from docgraph.core.pipeline import _stage_parse, _stage_store_blocks, _stage_store_chunks
from docgraph.graph.schema import BlockKind
from docgraph.graph.sqlite_store import SQLiteGraphStore


def main() -> None:
    root = project_root_from_cwd()
    autoload_env(root)
    bootstrap()
    cfg = load_config(root)

    if len(sys.argv) > 1:
        files = [Path(arg) for arg in sys.argv[1:]]
        files = [p if p.is_absolute() else root / p for p in files]
    else:
        files = sorted((root / "spec").glob("*.pdf"))
        files.extend([
            root / "case" / "PCIE Subsystem Spec_v3.21.pdf",
            root / "case" / "PCIe Subsystem TRS_r2p0.pdf",
        ])
    missing = [str(p.relative_to(root) if p.is_relative_to(root) else p) for p in files if not p.exists()]
    files = [p for p in files if p.exists()]

    dg = docgraph_dir(root)
    dg.mkdir(exist_ok=True)
    _sqlite_backup(dg / "graph.db", dg / f"graph.db.bak.l0l1-{time.strftime('%Y%m%d-%H%M%S')}")

    store = SQLiteGraphStore(dg / "graph.db")
    store.init_schema()

    per_doc: list[dict] = []
    started = time.time()
    out = dg / "l0_l1_eval.json"
    for path in files:
        rel = str(path.relative_to(root))
        rec = FileRecord(path=rel)
        rec.hash = file_hash(path)
        print(f"[l0/l1] {rel}", flush=True)
        try:
            t0 = time.time()
            parsed = _stage_parse(path, cfg, root, rec)
            parse_s = round(time.time() - t0, 2)
            _stage_store_blocks(parsed, store, rec)
            n_chunks = _stage_store_chunks(parsed, store, rec)
            chunks = [
                c for c in store.list_chunks(limit=1_000_000)
                if c.doc_id == parsed.doc_id
            ]
            blocks = [b for page in parsed.pages for b in page.blocks]
            per_doc.append(_summarize_doc(rel, parsed, blocks, chunks, n_chunks, parse_s))
        except Exception as e:
            per_doc.append({"path": rel, "ok": False, "error": str(e)})
        _write_report(out, started, len(files), per_doc, missing)

    report = _write_report(out, started, len(files), per_doc, missing)
    print(json.dumps(report["totals"], ensure_ascii=False, indent=2), flush=True)
    print(f"[l0/l1] report: {out}", flush=True)
    store.close()


def _write_report(out: Path, started: float, n_files: int, per_doc: list[dict], missing: list[str]) -> dict:
    report = {
        "ok": all(d.get("ok") for d in per_doc) and not missing,
        "duration_s": round(time.time() - started, 2),
        "pdfs": n_files,
        "missing": missing,
        "totals": _totals(per_doc),
        "documents": per_doc,
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _sqlite_backup(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    source = sqlite3.connect(str(src))
    try:
        target = sqlite3.connect(str(dst))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _summarize_doc(rel: str, parsed, blocks, chunks, n_chunks: int, parse_s: float) -> dict:
    block_kinds = Counter(b.kind.value for b in blocks)
    chunk_kinds = Counter((c.chunk_type or c.kind) for c in chunks)
    table_blocks = [b for b in blocks if b.kind == BlockKind.TABLE]
    figure_blocks = [b for b in blocks if b.kind == BlockKind.FIGURE]
    image_blocks = [b for b in blocks if b.image_path]
    tables_with_cells = [
        b for b in table_blocks
        if b.table and (b.table.headers or b.table.rows)
    ]
    table_profiles = Counter(
        ((c.attrs or {}).get("table_profile") or {}).get("kind", "none")
        for c in chunks
        if (c.chunk_type or c.kind) in {"table", "logical_table"}
    )
    orphan_chunks = [c.id for c in chunks if not c.block_ids]
    return {
        "path": rel,
        "ok": True,
        "doc_id": parsed.doc_id,
        "parser": parsed.parser,
        "pages": len(parsed.pages),
        "parse_s": parse_s,
        "toc_entries": len(parsed.toc or []),
        "blocks": len(blocks),
        "block_kinds": dict(sorted(block_kinds.items())),
        "tables": len(table_blocks),
        "tables_with_cells": len(tables_with_cells),
        "figures": len(figure_blocks),
        "image_blocks": len(image_blocks),
        "chunks": n_chunks,
        "chunk_kinds": dict(sorted(chunk_kinds.items())),
        "chunks_with_block_ids": sum(1 for c in chunks if c.block_ids),
        "chunks_with_section_node_id": sum(1 for c in chunks if c.section_node_id),
        "multi_page_chunks": sum(
            1 for c in chunks
            if c.page_start and c.page_end and c.page_end != c.page_start
        ),
        "table_profiles": dict(sorted(table_profiles.items())),
        "orphan_chunk_sample": orphan_chunks[:5],
    }


def _totals(per_doc: list[dict]) -> dict:
    ok_docs = [d for d in per_doc if d.get("ok")]
    block_kinds: Counter[str] = Counter()
    chunk_kinds: Counter[str] = Counter()
    table_profiles: Counter[str] = Counter()
    for d in ok_docs:
        block_kinds.update(d.get("block_kinds") or {})
        chunk_kinds.update(d.get("chunk_kinds") or {})
        table_profiles.update(d.get("table_profiles") or {})
    return {
        "ok_docs": len(ok_docs),
        "errors": len(per_doc) - len(ok_docs),
        "pages": sum(d.get("pages", 0) for d in ok_docs),
        "blocks": sum(d.get("blocks", 0) for d in ok_docs),
        "block_kinds": dict(sorted(block_kinds.items())),
        "tables": sum(d.get("tables", 0) for d in ok_docs),
        "tables_with_cells": sum(d.get("tables_with_cells", 0) for d in ok_docs),
        "figures": sum(d.get("figures", 0) for d in ok_docs),
        "image_blocks": sum(d.get("image_blocks", 0) for d in ok_docs),
        "chunks": sum(d.get("chunks", 0) for d in ok_docs),
        "chunk_kinds": dict(sorted(chunk_kinds.items())),
        "chunks_with_block_ids": sum(d.get("chunks_with_block_ids", 0) for d in ok_docs),
        "chunks_with_section_node_id": sum(
            d.get("chunks_with_section_node_id", 0) for d in ok_docs
        ),
        "multi_page_chunks": sum(d.get("multi_page_chunks", 0) for d in ok_docs),
        "table_profiles": dict(sorted(table_profiles.items())),
    }


if __name__ == "__main__":
    main()
