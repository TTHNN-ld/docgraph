"""M5 Web UI 测试 —— 用 FastAPI TestClient 跑全部页面 + JSON API。"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def project_with_data():
    """构造一份带有 register / bitfield / section 的临时项目。"""
    from docgraph.graph.schema import (
        DocMetadata, Edge, EdgeKind, Evidence, Location, Node, NodeKind,
    )
    from docgraph.graph.sqlite_store import SQLiteGraphStore

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / ".docgraph").mkdir()
        (root / "docgraph.yaml").write_text(
            "project:\n  name: t\n  family: testchip\n", encoding="utf-8"
        )
        store = SQLiteGraphStore(root / ".docgraph" / "graph.db")
        store.init_schema()

        # 节点
        reg = Node(
            id="testchip::reg:FOO_CTRL", kind=NodeKind.REGISTER,
            name="FOO_CTRL", qualified_name="FOO_CTRL",
            doc_id="testchip::ds", location=Location(page=42),
            summary="FOO control register.",
            attrs={"address": "0x40000000", "offset": "0x00",
                   "width": 32, "access": "RW", "reset_value": "0x0",
                   "source": "llm:title"},
        )
        bf0 = Node(
            id="testchip::bf:FOO_CTRL.EN", kind=NodeKind.BITFIELD,
            name="EN", qualified_name="FOO_CTRL.EN", doc_id="testchip::ds",
            attrs={"bit_high": 0, "bit_low": 0, "access": "RW",
                   "reset": "0", "description": "Enable.", "register_id": reg.id},
        )
        bf1 = Node(
            id="testchip::bf:FOO_CTRL.MODE", kind=NodeKind.BITFIELD,
            name="MODE", qualified_name="FOO_CTRL.MODE", doc_id="testchip::ds",
            attrs={"bit_high": 3, "bit_low": 1, "access": "RW",
                   "reset": "0b000", "description": "Mode select.",
                   "register_id": reg.id},
        )
        sec = Node(
            id="testchip::sec:1.2", kind=NodeKind.SECTION,
            name="1.2 Overview", qualified_name="1.2",
            doc_id="testchip::ds",
            location=Location(page=10, section_path="1.2"),
        )
        pin = Node(
            id="testchip::pin:PA0", kind=NodeKind.PIN, name="PA0",
            qualified_name="PA0", doc_id="testchip::ds",
            attrs={"direction": "IO", "description": "GPIO A0"},
        )
        term = Node(
            id="testchip::term:AHB", kind=NodeKind.TERM, name="AHB",
            doc_id="testchip::ds",
            aliases=["Advanced High-performance Bus"],
            attrs={"full": "Advanced High-performance Bus"},
        )
        for n in (reg, bf0, bf1, sec, pin, term):
            store.upsert_node(n)
        # 边
        for bf in (bf0, bf1):
            store.upsert_edge(Edge(
                src=reg.id, dst=bf.id, kind=EdgeKind.HAS_BITFIELD,
                confidence=0.9, evidence=Evidence(extractor="test"),
            ))
        store.close()
        yield root


@pytest.fixture()
def client(project_with_data, monkeypatch):
    from fastapi.testclient import TestClient
    from docgraph.web.server import create_app
    # 让 _open_project 找到项目
    monkeypatch.chdir(project_with_data)
    app = create_app(project_with_data)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "DocGraph" in r.text
    assert "testchip" in r.text or "节点" in r.text


def test_registers_page(client):
    r = client.get("/registers")
    assert r.status_code == 200
    assert "FOO_CTRL" in r.text


def test_register_detail_page(client):
    r = client.get("/registers/testchip::reg:FOO_CTRL")
    assert r.status_code == 200
    assert "FOO_CTRL" in r.text
    assert "EN" in r.text
    assert "MODE" in r.text
    # 位图渲染
    assert "bit-cell" in r.text


def test_register_not_found(client):
    r = client.get("/registers/testchip::reg:does-not-exist")
    assert r.status_code == 404


def test_pins_page(client):
    r = client.get("/pins")
    assert r.status_code == 200
    assert "PA0" in r.text


def test_timing_page(client):
    r = client.get("/timing")
    assert r.status_code == 200


def test_sections_page(client):
    r = client.get("/sections")
    assert r.status_code == 200


def test_glossary_page(client):
    r = client.get("/glossary")
    assert r.status_code == 200
    assert "AHB" in r.text


def test_figures_page(client):
    r = client.get("/figures")
    assert r.status_code == 200


def test_search_page(client):
    r = client.get("/search?q=FOO_CTRL")
    assert r.status_code == 200
    assert "FOO_CTRL" in r.text


def test_graph_page(client):
    r = client.get("/graph")
    assert r.status_code == 200
    assert "d3" in r.text.lower()
    assert "Shift+左键" in r.text
    assert "右键" in r.text
    assert "Mac / Windows" in r.text
    assert "configureNavigationControls" in r.text
    assert "installTrackpadPan" in r.text
    assert "installLinearPan" not in r.text
    assert "installPointerZoom" not in r.text
    assert "startGraphLabelRenderLoop" not in r.text
    assert "docgraphFit" not in r.text
    assert "适配图谱" not in r.text
    assert "graph-toolbar" not in r.text
    assert "sizeGraphToContainer" in r.text
    assert "显示节点名称" in r.text
    assert 'id="graph-labels-toggle" checked' not in r.text
    assert "onNodeHover" in r.text
    assert "graph-hover-tip" in r.text
    assert "clampHoverTip" in r.text
    assert "isClickNotDrag" in r.text
    assert "scheduleLabelUpdate" in r.text
    assert "createGraphLabels" in r.text
    assert "graph2ScreenCoords" in r.text
    assert "bindGraphViewport" in r.text
    assert "nodeVisibleInView" in r.text
    assert "labelIsolation" in r.text
    assert "MAX_VISIBLE_LABELS" in r.text
    assert ".linkDistance(" not in r.text
    assert "graphFetchGen" in r.text
    assert "userMovedCamera" in r.text
    assert "syncLeftButton" in r.text
    assert re.search(r'name="node_kind" value="register"\s+checked', r.text)
    assert re.search(r'name="node_kind" value="bitfield"\s*>', r.text)


def test_graph_label_css_keeps_hidden_labels_invisible():
    css = (
        Path(__file__).resolve().parents[2] / "docgraph" / "web" / "static" / "app.css"
    ).read_text(encoding="utf-8")
    assert ".graph-node-label[hidden]" in css
    assert ".graph-label-layer[hidden]" in css
    assert "display: none !important" in css
    assert "minmax(0, 1fr)" in css
    assert "overflow-x: clip" in css
    assert ".graph-hover-tip" in css


def test_plugins_page(client):
    r = client.get("/plugins")
    assert r.status_code == 200
    assert "register" in r.text


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


def test_api_status(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert data["nodes_total"] >= 5
    assert data["edges_total"] >= 2
    assert "register" in data["by_kind"]


def test_api_sections_tree(client):
    r = client.get("/api/sections/tree")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 1
    assert data["roots"][0]["path"] == "1.2"


def test_api_sections_tree_falls_back_to_l1_chunks(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from docgraph.graph.schema import Block, BlockKind, Chunk
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.web.server import create_app

    root = tmp_path
    (root / ".docgraph").mkdir()
    (root / "docgraph.yaml").write_text(
        "project:\n  name: t\n  family: testchip\n", encoding="utf-8"
    )
    store = SQLiteGraphStore(root / ".docgraph" / "graph.db")
    store.init_schema()
    store.upsert_blocks([
        Block(id="d#p6#b0", doc_id="d", page=6, kind=BlockKind.HEADING,
              reading_order=0, text="1.5 AXI", section_path="1.5"),
    ])
    store.upsert_chunks([
        Chunk(id="d#c1", doc_id="d", page=6, page_start=6, page_end=7,
              section_id="1.5", text="1.5AXI\nAXI slave interface",
              block_ids=["d#p6#b0"], kind="section", chunk_type="section",
              source_hash="h"),
    ])
    store.close()
    monkeypatch.chdir(root)
    client = TestClient(create_app(root))

    r = client.get("/api/sections/tree")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 1
    assert data["roots"][0]["path"] == "1.5"
    assert data["roots"][0]["source"] == "l1"


def test_api_search(client):
    r = client.get("/api/search?q=FOO_CTRL")
    assert r.status_code == 200
    data = r.json()
    assert any(n["name"] == "FOO_CTRL" for n in data["results"])


def test_api_node(client):
    r = client.get("/api/node/testchip::reg:FOO_CTRL")
    assert r.status_code == 200
    assert r.json()["name"] == "FOO_CTRL"


def test_api_neighbors(client):
    r = client.get("/api/neighbors/testchip::reg:FOO_CTRL?depth=1")
    assert r.status_code == 200
    data = r.json()
    assert len(data["nodes"]) >= 3   # 自身 + 2 个 bitfield
    assert any(e["kind"] == "has_bitfield" for e in data["edges"])


def test_api_graph(client):
    r = client.get("/api/graph?kinds=register,bitfield&limit=50")
    assert r.status_code == 200
    data = r.json()
    names = {n["name"] for n in data["nodes"]}
    assert "FOO_CTRL" in names
    assert "EN" in names
    assert any(e["kind"] == "has_bitfield" for e in data["edges"])


def test_api_graph_splits_limit_across_kinds(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from docgraph.graph.schema import Location, Node, NodeKind
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.web.server import create_app

    root = tmp_path
    (root / ".docgraph").mkdir()
    (root / "docgraph.yaml").write_text(
        "project:\n  name: t\n  family: testchip\n", encoding="utf-8"
    )
    store = SQLiteGraphStore(root / ".docgraph" / "graph.db")
    store.init_schema()
    for i in range(20):
        store.upsert_node(Node(
            id=f"testchip::sig:s{i}", kind=NodeKind.SIGNAL,
            name=f"sig_{i}", qualified_name=f"sig_{i}",
            doc_id="testchip::ds", location=Location(page=1),
        ))
    for i in range(5):
        store.upsert_node(Node(
            id=f"testchip::mod:m{i}", kind=NodeKind.MODULE,
            name=f"mod_{i}", qualified_name=f"mod_{i}",
            doc_id="testchip::ds", location=Location(page=1),
        ))
    store.close()
    monkeypatch.chdir(root)
    client = TestClient(create_app(root))

    r = client.get("/api/graph?kinds=signal,module&limit=10")
    assert r.status_code == 200
    data = r.json()
    by_kind = {}
    for node in data["nodes"]:
        by_kind[node["kind"]] = by_kind.get(node["kind"], 0) + 1
    assert by_kind.get("signal") == 5
    assert by_kind.get("module") == 5


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------


def test_static_css(client):
    r = client.get("/static/app.css")
    assert r.status_code == 200
    assert "DocGraph" in r.text  # css 文件头里写了项目名注释
