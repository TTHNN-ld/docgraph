from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from docgraph.graph.schema import Block, BlockKind, Location, Node, NodeKind
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.linker.relation_infer import RelationInferLinker


@pytest.fixture()
def tmp_store():
    with tempfile.TemporaryDirectory() as d:
        store = SQLiteGraphStore(Path(d) / "graph.db")
        store.init_schema()
        yield store
        store.close()


def _edges(store: SQLiteGraphStore, src: str) -> list[tuple[str, str, str]]:
    """直接查 edges 表，返回 (src, dst, kind)。"""
    conn = sqlite3.connect(str(store.path))
    try:
        return conn.execute(
            "SELECT src, dst, kind FROM edges WHERE src=?", (src,)
        ).fetchall()
    finally:
        conn.close()


def _section(doc_id: str, path: str, name: str) -> Node:
    return Node(
        id=f"{doc_id}#sec:{path}",
        kind=NodeKind.SECTION,
        name=name,
        qualified_name=name,
        doc_id=doc_id,
        location=Location(section_path=path),
    )


def test_belongs_to_links_entity_to_section(tmp_store):
    """register 的 source_block_ids 回溯到 block.section_path → belongs_to section。"""
    tmp_store.upsert_node(_section("d", "3.2", "DMA Module"))
    tmp_store.upsert_blocks([
        Block(id="d#p3#b1", doc_id="d", page=3, kind=BlockKind.TABLE, section_path="3.2"),
    ])
    tmp_store.upsert_node(Node(
        id="d#reg:CTRL", kind=NodeKind.REGISTER, name="CTRL",
        qualified_name="CTRL", doc_id="d",
        attrs={"source_block_ids": ["d#p3#b1"]},
    ))

    rep = RelationInferLinker().run(tmp_store)

    assert rep.belongs_to_edges == 1
    assert _edges(tmp_store, "d#reg:CTRL") == [("d#reg:CTRL", "d#sec:3.2", "belongs_to")]


def test_belongs_to_prefers_module_when_name_matches(tmp_store):
    """section 与某 module 节点同名 → 优先连 module（更语义）。"""
    tmp_store.upsert_node(_section("d", "3.2", "DMA Engine"))
    tmp_store.upsert_node(Node(
        id="d#mod:DMAEngine", kind=NodeKind.MODULE, name="DMA Engine",
        qualified_name="DMA Engine", doc_id="d",
    ))
    tmp_store.upsert_blocks([
        Block(id="d#p3#b1", doc_id="d", page=3, kind=BlockKind.TABLE, section_path="3.2"),
    ])
    tmp_store.upsert_node(Node(
        id="d#reg:CTRL", kind=NodeKind.REGISTER, name="CTRL",
        qualified_name="CTRL", doc_id="d",
        attrs={"source_block_ids": ["d#p3#b1"]},
    ))

    RelationInferLinker().run(tmp_store)

    edges = _edges(tmp_store, "d#reg:CTRL")
    assert edges == [("d#reg:CTRL", "d#mod:DMAEngine", "belongs_to")]


def test_belongs_to_skips_when_section_not_recoverable(tmp_store):
    """无 source_block_ids 且节点无 section_path → 跳过，不建边。"""
    tmp_store.upsert_node(_section("d", "3.2", "DMA Module"))
    tmp_store.upsert_node(Node(
        id="d#reg:CTRL", kind=NodeKind.REGISTER, name="CTRL",
        qualified_name="CTRL", doc_id="d", attrs={},
    ))

    rep = RelationInferLinker().run(tmp_store)

    assert rep.belongs_to_edges == 0
    assert rep.skipped_no_section >= 1
    assert _edges(tmp_store, "d#reg:CTRL") == []


def test_contained_in_by_address_prefix(tmp_store):
    """memory_map.base 是 register.address 的前缀 → contained_in。"""
    tmp_store.upsert_node(Node(
        id="d#mm:DMA", kind=NodeKind.MEMORY_MAP, name="DMA",
        qualified_name="DMA", doc_id="d",
        attrs={"address": "0x13800000"},
    ))
    tmp_store.upsert_node(Node(
        id="d#reg:CTRL", kind=NodeKind.REGISTER, name="CTRL",
        qualified_name="CTRL", doc_id="d",
        attrs={"address": "0x13800020"},
    ))

    rep = RelationInferLinker().run(tmp_store)

    assert rep.contained_in_edges == 1
    assert _edges(tmp_store, "d#mm:DMA") == [("d#mm:DMA", "d#reg:CTRL", "contained_in")]


def test_relation_infer_is_idempotent(tmp_store):
    """重复跑不产生重复边（upsert_edge 对 (src,dst,kind) 去重）。"""
    tmp_store.upsert_node(_section("d", "3.2", "DMA Module"))
    tmp_store.upsert_blocks([
        Block(id="d#p3#b1", doc_id="d", page=3, kind=BlockKind.TABLE, section_path="3.2"),
    ])
    tmp_store.upsert_node(Node(
        id="d#reg:CTRL", kind=NodeKind.REGISTER, name="CTRL",
        qualified_name="CTRL", doc_id="d",
        attrs={"source_block_ids": ["d#p3#b1"]},
    ))

    RelationInferLinker().run(tmp_store)
    RelationInferLinker().run(tmp_store)

    assert _edges(tmp_store, "d#reg:CTRL") == [("d#reg:CTRL", "d#sec:3.2", "belongs_to")]
