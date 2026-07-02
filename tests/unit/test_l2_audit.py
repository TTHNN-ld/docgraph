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
    assert register["sample_candidate_ids"] == ["c_register"]
    assert register["materialization_rate"] == 0.0
    assert any(issue.code == "l2.matched_but_no_nodes" for issue in report.issues)
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


def test_l2_audit_routes_registers_for_protocol_specs(tmp_path):
    from docgraph.graph.schema import Block, BlockKind, Chunk, TableData
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.quality.l2 import audit_l2_candidates

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    doc_id = "chip::protocol::PCIe Subsystem Spec"
    store.upsert_blocks([
        Block(
            id=f"{doc_id}#p1#b0",
            doc_id=doc_id,
            page=1,
            kind=BlockKind.TABLE,
            reading_order=0,
            table=TableData(
                caption="Fields for Register: AMBA_ORDERING_CTRL_OFF",
                headers=["Bits", "Name", "Access", "Reset", "Description"],
                rows=[["4:3", "AX_MSTR_ORDR_P_EVENT_SE_L", "RW", "0x0", "selector"]],
            ),
        ),
    ])
    store.upsert_chunks([
        Chunk(
            id=f"{doc_id}#c_reg",
            doc_id=doc_id,
            page=1,
            page_start=1,
            page_end=1,
            text="| Bits | Name | Access | Reset | Description |",
            block_ids=[f"{doc_id}#p1#b0"],
            chunk_type="table",
        ),
    ])

    report = audit_l2_candidates(store)
    by_schema = {row["schema"]: row for row in report.by_schema}
    assert report.by_doc[0]["doc_type"] == "protocol"
    assert by_schema["register"]["table_candidates_matched"] == 1
    store.close()


def test_l2_audit_routes_backend_constraints_for_unknown_docs(tmp_path):
    from docgraph.graph.schema import Block, BlockKind, Chunk, TableData
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.quality.l2 import audit_l2_candidates

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    doc_id = "chip::doc::backend_constraints"
    store.upsert_blocks([
        Block(
            id=f"{doc_id}#p1#b0",
            doc_id=doc_id,
            page=1,
            kind=BlockKind.TABLE,
            reading_order=0,
            table=TableData(
                caption="STA constraints",
                headers=["Constraint", "Target", "Value", "Unit", "Corner"],
                rows=[["clock_uncertainty", "core_clk", "0.08", "ns", "SSG"]],
            ),
        ),
        Block(
            id=f"{doc_id}#p2#b0",
            doc_id=doc_id,
            page=2,
            kind=BlockKind.TABLE,
            reading_order=0,
            table=TableData(
                caption="Floorplan constraints",
                headers=["Rule", "Object", "Layer", "Region", "Spacing"],
                rows=[["SRAM_keepout", "u_sram0", "M2-M6", "CORE_NW", "5um"]],
            ),
        ),
    ])
    store.upsert_chunks([
        Chunk(
            id=f"{doc_id}#c_sta",
            doc_id=doc_id,
            page=1,
            page_start=1,
            page_end=1,
            text="| Constraint | Target | Value | Unit | Corner |",
            block_ids=[f"{doc_id}#p1#b0"],
            chunk_type="table",
        ),
        Chunk(
            id=f"{doc_id}#c_floorplan",
            doc_id=doc_id,
            page=2,
            page_start=2,
            page_end=2,
            text="| Rule | Object | Layer | Region | Spacing |",
            block_ids=[f"{doc_id}#p2#b0"],
            chunk_type="table",
        ),
    ])

    report = audit_l2_candidates(store)
    by_schema = {row["schema"]: row for row in report.by_schema}
    assert by_schema["constraint"]["table_candidates_matched"] == 1
    assert by_schema["physical_constraint"]["table_candidates_matched"] == 1
    assert report.by_doc[0]["doc_type"] == "unknown"
    store.close()
