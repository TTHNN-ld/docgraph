from __future__ import annotations

import json


def test_l2_eval_expected_registers_file(tmp_path):
    from docgraph.graph.schema import Evidence, Location, Node, NodeKind
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.quality.l2_eval import eval_l2_golden

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    store.upsert_node(Node(
        id="d::reg:CTRL",
        kind=NodeKind.REGISTER,
        name="CTRL",
        doc_id="d",
        location=Location(page=1),
        evidence=Evidence(extractor="test", pages=[1]),
        attrs={"schema_name": "register"},
    ))
    store.upsert_node(Node(
        id="d::reg:STATUS",
        kind=NodeKind.REGISTER,
        name="STATUS",
        doc_id="d",
        location=Location(page=2),
        evidence=Evidence(extractor="test", pages=[2]),
        attrs={"schema_name": "register"},
    ))
    golden = tmp_path / "expected_registers.json"
    golden.write_text(json.dumps([
        {"name": "CTRL", "doc_id": "d"},
        {"name": "MISSING", "doc_id": "d"},
    ]), encoding="utf-8")

    report = eval_l2_golden(store, golden, min_precision=0.9, min_recall=0.9)
    assert not report.ok
    assert report.totals["expected"] == 2
    assert report.totals["actual"] == 2
    assert report.totals["true_positive"] == 1
    assert report.totals["false_positive"] == 1
    assert report.totals["false_negative"] == 1
    assert report.totals["precision"] == 0.5
    assert report.totals["recall"] == 0.5
    store.close()


def test_l2_eval_l2_expected_object(tmp_path):
    from docgraph.graph.schema import Evidence, Location, Node, NodeKind
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.quality.l2_eval import eval_l2_golden

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    store.upsert_node(Node(
        id="d::sig:clk",
        kind=NodeKind.SIGNAL,
        name="clk",
        doc_id="d",
        location=Location(page=1),
        evidence=Evidence(extractor="test", pages=[1]),
        attrs={"schema_name": "signal"},
    ))
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    (golden_dir / "l2_expected.json").write_text(json.dumps({
        "signals": ["clk"],
    }), encoding="utf-8")

    report = eval_l2_golden(store, golden_dir, kinds=["signal"], min_precision=1.0, min_recall=1.0)
    assert report.ok
    assert report.totals["precision"] == 1.0
    assert report.totals["recall"] == 1.0
    assert report.by_kind[0].kind == "signal"
    store.close()
