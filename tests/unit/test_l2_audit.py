from __future__ import annotations


def test_l2_audit_reports_schema_candidate_hits(tmp_path):
    from docgraph.graph.schema import Block, BlockKind, Chunk, TableData
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.quality.l2 import audit_l2_candidates

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    store.upsert_blocks([
        Block(
            id="d#p1#b0",
            doc_id="d",
            page=1,
            kind=BlockKind.TABLE,
            reading_order=0,
            table=TableData(
                caption="Register table",
                headers=["Bits", "Name", "Access", "Reset", "Description"],
                rows=[["0", "EN", "RW", "0x0", "enable"]],
            ),
        ),
        Block(
            id="d#p2#b0",
            doc_id="d",
            page=2,
            kind=BlockKind.TABLE,
            reading_order=0,
            table=TableData(
                caption="Revision history",
                headers=["Version", "Date"],
                rows=[["1.0", "2026-01-01"]],
            ),
        ),
    ])
    store.upsert_chunks([
        Chunk(
            id="c_register",
            doc_id="d",
            page=1,
            page_start=1,
            page_end=1,
            text="| Bits | Name | Access | Reset | Description |",
            block_ids=["d#p1#b0"],
            chunk_type="table",
        ),
        Chunk(
            id="c_revision",
            doc_id="d",
            page=2,
            page_start=2,
            page_end=2,
            text="| Version | Date |",
            block_ids=["d#p2#b0"],
            chunk_type="table",
        ),
    ])

    report = audit_l2_candidates(store, schema_names=["register"])
    assert report.ok
    assert report.totals["table_candidates"] == 2
    assert report.totals["table_schema_hits"] == 1
    register = next(row for row in report.by_schema if row["schema"] == "register")
    assert register["table_candidates_seen"] == 2
    assert register["table_candidates_matched"] == 1
    assert any(issue.code == "l2.table_no_schema_hits" for issue in report.issues) is False
    store.close()


def test_l2_audit_warns_when_tables_do_not_match_schema(tmp_path):
    from docgraph.graph.schema import Block, BlockKind, Chunk, TableData
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.quality.l2 import audit_l2_candidates

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    store.upsert_blocks([
        Block(
            id="d#p1#b0",
            doc_id="d",
            page=1,
            kind=BlockKind.TABLE,
            reading_order=0,
            table=TableData(
                caption="Revision history",
                headers=["Version", "Date"],
                rows=[["1.0", "2026-01-01"]],
            ),
        ),
    ])
    store.upsert_chunks([
        Chunk(
            id="c_revision",
            doc_id="d",
            page=1,
            page_start=1,
            page_end=1,
            text="| Version | Date |",
            block_ids=["d#p1#b0"],
            chunk_type="table",
        ),
    ])

    report = audit_l2_candidates(store, schema_names=["register"])
    assert report.ok
    assert report.totals["table_schema_hits"] == 0
    assert any(issue.code == "l2.table_no_schema_hits" for issue in report.issues)
    store.close()


def test_l2_audit_infers_doc_type_for_schema_routing(tmp_path):
    from docgraph.graph.schema import Block, BlockKind, Chunk, TableData
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.quality.l2 import audit_l2_candidates

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    doc_id = "chip::datasheet::demo"
    store.upsert_blocks([
        Block(
            id=f"{doc_id}#p1#b0",
            doc_id=doc_id,
            page=1,
            kind=BlockKind.TABLE,
            reading_order=0,
            table=TableData(
                caption="Signal list",
                headers=["Signal", "Direction", "Width", "Description"],
                rows=[["clk", "IN", "1", "clock"]],
            ),
        ),
    ])
    store.upsert_chunks([
        Chunk(
            id=f"{doc_id}#c_table",
            doc_id=doc_id,
            page=1,
            page_start=1,
            page_end=1,
            text="| Signal | Direction | Width | Description |",
            block_ids=[f"{doc_id}#p1#b0"],
            chunk_type="table",
        ),
    ])

    report = audit_l2_candidates(store)
    signal = next(row for row in report.by_schema if row["schema"] == "signal")
    assert signal["table_candidates_matched"] == 1
    assert report.by_doc[0]["doc_type"] == "datasheet"
    store.close()
