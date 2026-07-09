from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from docgraph.graph.schema import Chunk, Node, NodeKind
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.graph.store import NodeQuery
from docgraph.linker.llm_ie import LLMIERelation, LLMIEResult, LLMIELinker


@pytest.fixture()
def tmp_store():
    with tempfile.TemporaryDirectory() as d:
        store = SQLiteGraphStore(Path(d) / "graph.db")
        store.init_schema()
        yield store
        store.close()


class FakeLLM:
    """模拟 LLMClient.json：按 payload 返回 LLMIEResult。"""

    def __init__(self, payload: LLMIEResult):
        self.payload = payload
        self.calls = 0

    def json(self, prompt, *, schema, extractor="_", **kwargs):
        self.calls += 1
        return self.payload


class FlakyLLM:
    """First JSON call fails like an empty model response, then succeeds."""

    def __init__(self, payload: LLMIEResult):
        self.payload = payload
        self.calls = 0
        self.prompts: list[str] = []

    def json(self, prompt, *, schema, extractor="_", **kwargs):
        self.calls += 1
        self.prompts.append(prompt)
        if self.calls == 1:
            raise ValueError("empty LLM response")
        return self.payload


def _edges(store, src=None):
    conn = sqlite3.connect(str(store.path))
    try:
        if src:
            return conn.execute("SELECT src,dst,kind FROM edges WHERE src=?", (src,)).fetchall()
        return conn.execute("SELECT src,dst,kind FROM edges").fetchall()
    finally:
        conn.close()


def _seed(store, doc_id="d"):
    store.upsert_node(Node(
        id=f"{doc_id}#reg:CTRL", kind=NodeKind.REGISTER, name="CTRL",
        qualified_name="CTRL", doc_id=doc_id,
    ))
    store.upsert_node(Node(
        id=f"{doc_id}#mod:DMA", kind=NodeKind.MODULE, name="DMA",
        qualified_name="DMA", doc_id=doc_id,
    ))
    store.upsert_chunks([
        Chunk(id=f"{doc_id}#c_section_p1_0", doc_id=doc_id, page=1, kind="section",
              text="The CTRL register belongs to the DMA module and controls its behavior. "
                   "It drives the DMA clock signal and references the DMA configuration."),
    ])


def test_llm_ie_creates_edge_between_existing_entities(tmp_store):
    _seed(tmp_store)
    payload = LLMIEResult(relations=[
        LLMIERelation(src="CTRL", relation="belongs_to", dst="DMA", confidence=0.85),
    ])
    rep = LLMIELinker().run(tmp_store, llm_client=FakeLLM(payload))

    assert rep.llm_calls == 1
    assert rep.edges_created == 1
    assert ("d#reg:CTRL", "d#mod:DMA", "belongs_to") in _edges(tmp_store, "d#reg:CTRL")


def test_llm_ie_retries_empty_response_with_grounded_entity_list(tmp_store):
    _seed(tmp_store)
    payload = LLMIEResult(relations=[
        LLMIERelation(src="CTRL", relation="belongs_to", dst="DMA", confidence=0.85),
    ])
    fake = FlakyLLM(payload)
    rep = LLMIELinker().run(tmp_store, llm_client=fake)

    assert fake.calls == 2
    assert rep.fallback_calls == 1
    assert rep.edges_created == 1
    assert "候选实体" in fake.prompts[0]
    assert "- CTRL" in fake.prompts[0]
    assert "- DMA" in fake.prompts[0]


def test_llm_ie_skips_when_entity_not_found(tmp_store):
    _seed(tmp_store)
    payload = LLMIEResult(relations=[
        LLMIERelation(src="CTRL", relation="belongs_to", dst="NonExistent", confidence=0.85),
    ])
    rep = LLMIELinker().run(tmp_store, llm_client=FakeLLM(payload))

    assert rep.edges_created == 0
    assert rep.skipped_no_match >= 1


def test_llm_ie_skips_low_confidence(tmp_store):
    _seed(tmp_store)
    payload = LLMIEResult(relations=[
        LLMIERelation(src="CTRL", relation="belongs_to", dst="DMA", confidence=0.3),
    ])
    rep = LLMIELinker().run(tmp_store, llm_client=FakeLLM(payload))

    assert rep.edges_created == 0


def test_llm_ie_skips_unknown_relation_type(tmp_store):
    _seed(tmp_store)
    payload = LLMIEResult(relations=[
        LLMIERelation(src="CTRL", relation="loves", dst="DMA", confidence=0.9),
    ])
    rep = LLMIELinker().run(tmp_store, llm_client=FakeLLM(payload))

    assert rep.edges_created == 0


def test_llm_ie_skips_when_no_llm(tmp_store):
    _seed(tmp_store)
    rep = LLMIELinker().run(tmp_store, llm_client=None)
    assert rep.llm_calls == 0
    assert rep.edges_created == 0


def test_llm_ie_skips_chunk_below_entity_threshold(tmp_store):
    """chunk 只提到 1 个已知实体（< MIN_ENTITIES_MENTIONED=2）→ 不调 LLM。"""
    store = tmp_store
    store.upsert_node(Node(
        id="d#reg:CTRL", kind=NodeKind.REGISTER, name="CTRL",
        qualified_name="CTRL", doc_id="d",
    ))
    store.upsert_chunks([
        Chunk(id="d#c_section_p1_0", doc_id="d", page=1, kind="section",
              text="The CTRL register is configured here. No other known entity mentioned."),
    ])
    fake = FakeLLM(LLMIEResult(relations=[]))
    LLMIELinker().run(store, llm_client=fake)
    assert fake.calls == 0


def test_llm_ie_skips_requirement_heavy_chunks(tmp_store):
    """REQ_ 需求条目密集的 chunk 跳过（text_entity 已抽，且 DeepSeek 在这类 chunk 空响应）。"""
    store = tmp_store
    store.upsert_node(Node(id="d#reg:REGA", kind=NodeKind.REGISTER, name="REGA", qualified_name="REGA", doc_id="d"))
    store.upsert_node(Node(id="d#reg:REGB", kind=NodeKind.REGISTER, name="REGB", qualified_name="REGB", doc_id="d"))
    store.upsert_chunks([
        Chunk(id="d#c_section_p1_0", doc_id="d", page=1, kind="section",
              text="REQ_PCIE_001: REGA 配置 REGB 的时钟。REQ_PCIE_002: REGA 控制 REGB 的复位。"
                   "REQ_PCIE_003: REGA 与 REGB 互连。这是需求清单，不该走 llm_ie。"),
    ])
    fake = FakeLLM(LLMIEResult(relations=[]))
    rep = LLMIELinker().run(store, llm_client=fake)
    assert fake.calls == 0
    assert rep.skipped_req >= 1


def test_llm_ie_fuzzy_matches_entity_with_noise_prefix(tmp_store):
    """LLM 抽出 'the DMA engine' 能模糊匹到节点 'DMA Engine'。"""
    store = tmp_store
    store.upsert_node(Node(
        id="d#mod:DMAEngine", kind=NodeKind.MODULE, name="DMA Engine",
        qualified_name="DMA Engine", doc_id="d",
    ))
    store.upsert_node(Node(
        id="d#reg:CTRL", kind=NodeKind.REGISTER, name="CTRL",
        qualified_name="CTRL", doc_id="d",
    ))
    store.upsert_chunks([
        Chunk(id="d#c_section_p1_0", doc_id="d", page=1, kind="section",
              text="The CTRL register belongs to the DMA engine and drives its operation. "
                   "It references the DMA configuration and controls the engine behavior."),
    ])
    payload = LLMIEResult(relations=[
        LLMIERelation(src="CTRL", relation="belongs_to", dst="the DMA engine", confidence=0.85),
    ])
    rep = LLMIELinker().run(store, llm_client=FakeLLM(payload))
    assert rep.edges_created == 1
    assert ("d#reg:CTRL", "d#mod:DMAEngine", "belongs_to") in _edges(store, "d#reg:CTRL")


def test_llm_ie_creates_entity_with_high_confidence(tmp_store):
    """confidence ≥ 0.8：LLM 提到的新实体不在已有节点中 -> 创建实体 + 建边。"""
    store = tmp_store
    store.upsert_node(Node(
        id="d#reg:CTRL", kind=NodeKind.REGISTER, name="CTRL",
        qualified_name="CTRL", doc_id="d",
    ))
    store.upsert_node(Node(
        id="d#mod:DMA", kind=NodeKind.MODULE, name="DMA",
        qualified_name="DMA", doc_id="d",
    ))
    store.upsert_chunks([
        Chunk(id="d#c_section_p1_0", doc_id="d", page=1, kind="section",
              text="The CTRL register and DMA module drive a new signal mstr_aclk which has not been "
                   "seen before in any table."),
    ])
    payload = LLMIEResult(relations=[
        LLMIERelation(src="CTRL", src_type="register", relation="drives", dst="mstr_aclk", dst_type="signal", confidence=0.9),
    ])
    rep = LLMIELinker().run(store, llm_client=FakeLLM(payload))

    assert rep.edges_created == 1
    assert rep.entities_created == 1
    assert rep.entities_pending == 0
    node = store.search_nodes(NodeQuery(kind=NodeKind.SIGNAL, name="mstr_aclk", limit=1))
    assert len(node) == 1
    assert node[0].attrs["llm_confidence"] == 0.9
    assert "pending" not in node[0].attrs


def test_llm_ie_creates_pending_entity_with_medium_confidence(tmp_store):
    """0.6 ≤ confidence < 0.8：创建实体但标记 pending。"""
    store = tmp_store
    store.upsert_node(Node(
        id="d#reg:CTRL", kind=NodeKind.REGISTER, name="CTRL",
        qualified_name="CTRL", doc_id="d",
    ))
    store.upsert_node(Node(
        id="d#mod:DMA", kind=NodeKind.MODULE, name="DMA",
        qualified_name="DMA", doc_id="d",
    ))
    store.upsert_chunks([
        Chunk(id="d#c_section_p1_0", doc_id="d", page=1, kind="section",
              text="The CTRL register and DMA module might be connected to a new signal clkout which needs further verification."),
    ])
    payload = LLMIEResult(relations=[
        LLMIERelation(src="CTRL", src_type="register", relation="drives", dst="clkout", dst_type="signal", confidence=0.7),
    ])
    rep = LLMIELinker().run(store, llm_client=FakeLLM(payload))

    assert rep.edges_created == 1
    assert rep.entities_created == 0
    assert rep.entities_pending == 1
    node = store.search_nodes(NodeQuery(kind=NodeKind.SIGNAL, name="clkout", limit=1))
    assert len(node) == 1
    assert node[0].attrs["status"] == "pending"
    assert node[0].attrs["llm_confidence"] == 0.7


def test_llm_ie_skips_entity_with_low_confidence(tmp_store):
    """confidence < 0.6：不建实体也不建边。"""
    store = tmp_store
    store.upsert_node(Node(
        id="d#reg:CTRL", kind=NodeKind.REGISTER, name="CTRL", qualified_name="CTRL", doc_id="d",
    ))
    store.upsert_node(Node(
        id="d#mod:DMA", kind=NodeKind.MODULE, name="DMA", qualified_name="DMA", doc_id="d",
    ))
    store.upsert_chunks([
        Chunk(id="d#c_section_p1_0", doc_id="d", page=1, kind="section",
              text="The CTRL register and DMA module might drive clkout signal but the confidence is not high enough to create a new entity node."),
    ])
    payload = LLMIEResult(relations=[
        LLMIERelation(src="CTRL", src_type="register", relation="drives", dst="clkout", dst_type="signal", confidence=0.5),
    ])
    rep = LLMIELinker().run(store, llm_client=FakeLLM(payload))

    assert rep.edges_created == 0
    assert rep.entities_created == 0
    assert rep.entities_pending == 0


def test_llm_ie_dedup_entity_by_name_and_kind(tmp_store):
    """同 doc 同 kind 同归一化名 -> 不重复建实体，复用已有。"""
    store = tmp_store
    store.upsert_node(Node(
        id="d#reg:CTRL", kind=NodeKind.REGISTER, name="CTRL", qualified_name="CTRL", doc_id="d",
    ))
    store.upsert_node(Node(
        id="d#mod:DMA", kind=NodeKind.MODULE, name="DMA", qualified_name="DMA", doc_id="d",
    ))
    # 已有 signal "ClkOut" (LLM 抽出 "clkout" 归一化后同名)
    store.upsert_node(Node(
        id="d#sig:CLKOUT", kind=NodeKind.SIGNAL, name="ClkOut", qualified_name="ClkOut", doc_id="d",
    ))
    store.upsert_chunks([
        Chunk(id="d#c_section_p1_0", doc_id="d", page=1, kind="section",
              text="The CTRL register and DMA module both drive the ClkOut signal which provides a high-speed interface clock."),
    ])
    payload = LLMIEResult(relations=[
        LLMIERelation(src="CTRL", src_type="register", relation="drives", dst="clkout", dst_type="signal", confidence=0.9),
    ])
    rep = LLMIELinker().run(store, llm_client=FakeLLM(payload))

    assert rep.edges_created == 1
    assert rep.entities_created == 0  # 复用已有，不新创
    assert rep.entities_pending == 0


def test_llm_ie_skips_unknown_entity_type(tmp_store):
    """src_type/dst_type 不在 _KIND_MAP -> 无法确定类型，跳过创建。"""
    store = tmp_store
    store.upsert_node(Node(
        id="d#reg:CTRL", kind=NodeKind.REGISTER, name="CTRL", qualified_name="CTRL", doc_id="d",
    ))
    store.upsert_node(Node(
        id="d#mod:DMA", kind=NodeKind.MODULE, name="DMA", qualified_name="DMA", doc_id="d",
    ))
    store.upsert_chunks([
        Chunk(id="d#c_section_p1_0", doc_id="d", page=1, kind="section",
              text="The CTRL register and DMA module are part of the dataflow architecture in the chip design specification."),
    ])
    payload = LLMIEResult(relations=[
        LLMIERelation(src="CTRL", src_type="register", relation="depends_on", dst="dataflow", dst_type="dataflow", confidence=0.9),
    ])
    rep = LLMIELinker().run(store, llm_client=FakeLLM(payload))

    assert rep.edges_created == 0  # dst 匹不到也建不了
    assert rep.entities_created == 0
