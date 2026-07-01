"""M2 新组件测试：linker、向量、新 extractors、context、trace。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from docgraph.embeddings.hash_encoder import HashEncoder
from docgraph.embeddings.indexer import embed_graph, text_for_embedding
from docgraph.embeddings.vector_store import VectorStore, _cosine
from docgraph.extractors.base import ExtractContext
from docgraph.extractors.glossary import GlossaryExtractor
from docgraph.extractors.table_entity import TableEntityExtractor
from docgraph.graph.schema import (
    Block,
    BlockKind,
    Edge,
    EdgeKind,
    Evidence,
    Node,
    NodeKind,
    ParsedDoc,
    ParsedPage,
    TocEntry,
)
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.linker.entity_resolver import EntityResolver, normalize
from docgraph.linker.federation import FederationLinker
from docgraph.linker.xref import XRefLinker
from docgraph.query.engine import QueryEngine


@pytest.fixture()
def tmp_store():
    with tempfile.TemporaryDirectory() as d:
        store = SQLiteGraphStore(Path(d) / "graph.db")
        store.init_schema()
        yield store
        store.close()


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------


def test_table_entity_no_llm_returns_empty():
    """无 LLM 时 TableEntityExtractor 返回空（不崩溃、不阻塞 pipeline）。"""
    parsed = ParsedDoc(doc_id="d", source_path="x.pdf",
                        pages=[ParsedPage(page_no=1)])
    res = TableEntityExtractor().extract(parsed, ExtractContext(family="t"))
    assert res.nodes == []
    assert res.stats.nodes_emitted == 0


def test_table_entity_no_table_blocks():
    """无 table block → 不走 LLM，不出错。"""
    parsed = ParsedDoc(doc_id="d", source_path="x.pdf",
                        pages=[ParsedPage(page_no=1)])
    ctx = ExtractContext(family="t", llm_client=None)
    res = TableEntityExtractor(schema_names=["register"]).extract(parsed, ctx)
    assert res.nodes == []
    assert res.stats.llm_calls == 0


def test_glossary_extractor():
    toc = [TocEntry(level=1, title="List of Abbreviations", page=2)]
    text = (
        "AXI    Advanced eXtensible Interface\n"
        "AHB    Advanced High-performance Bus\n"
        "APB    Advanced Peripheral Bus\n"
    )
    parsed = ParsedDoc(
        doc_id="d", source_path="x.pdf",
        toc=toc,
        pages=[
            ParsedPage(page_no=1),
            ParsedPage(page_no=2, blocks=[
                Block(id="d#p2#b0", doc_id="d", page=2, kind=BlockKind.PARAGRAPH, reading_order=0, text=text),
            ]),
        ],
    )
    res = GlossaryExtractor().extract(parsed, ExtractContext(family="t"))
    names = {n.name for n in res.nodes}
    assert {"AXI", "AHB", "APB"}.issubset(names)


# ---------------------------------------------------------------------------
# Linker
# ---------------------------------------------------------------------------


def test_entity_resolver_normalize():
    assert normalize("TIM1_CR1") == normalize("TIM1-CR1") == normalize("tim1 cr1")


def test_entity_resolver_runs(tmp_store):
    # 同 family 不同 doc 的同名寄存器
    for i, doc_id in enumerate(["d1", "d2"]):
        tmp_store.upsert_node(
            Node(
                id=f"f::reg:CR1#{doc_id}",
                kind=NodeKind.REGISTER,
                name="CR1",
                qualified_name="CR1",
                doc_id=doc_id,
            )
        )
    r = EntityResolver().run(tmp_store)
    assert r.alias_edges >= 1


def test_federation_supersedes(tmp_store):
    for doc_id in ["d_main", "d_errata"]:
        tmp_store.upsert_node(
            Node(
                id=f"f::reg:CR1#{doc_id}",
                kind=NodeKind.REGISTER,
                name="CR1",
                qualified_name="CR1",
                doc_id=doc_id,
            )
        )
    r = FederationLinker().run(
        tmp_store, doc_priorities={"d_main": 10, "d_errata": 100}
    )
    assert r.supersedes_edges == 1


def test_xref_linker(tmp_store):
    sec = Node(
        id="f::sec:3.2", kind=NodeKind.SECTION, name="3.2 Title",
        qualified_name="3.2", doc_id="d",
    )
    sec_ref = Node(
        id="f::sec:5.1", kind=NodeKind.SECTION, name="5.1 Other",
        qualified_name="5.1", doc_id="d",
        summary="See Section 3.2 for details.",
    )
    tmp_store.upsert_node(sec)
    tmp_store.upsert_node(sec_ref)
    r = XRefLinker().run(tmp_store)
    assert r.edges_added >= 1


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


def test_hash_encoder_deterministic():
    enc = HashEncoder(dim=64)
    a = enc.encode(["TIM1 control register"])[0]
    b = enc.encode(["TIM1 control register"])[0]
    assert a == b
    # 维度一致
    assert len(a) == 64


def test_cosine():
    assert _cosine([1, 0, 0], [1, 0, 0]) == 1.0
    assert abs(_cosine([1, 0, 0], [0, 1, 0])) < 1e-6


def test_embed_and_search(tmp_store):
    with tempfile.TemporaryDirectory() as d:
        vstore = VectorStore(Path(d) / "v.db")
        vstore.init_schema()
        enc = HashEncoder(dim=128)

        for name, summary in [
            ("PWM_CTRL", "PWM control register, enable/disable timer"),
            ("UART_BR", "UART baud rate register"),
            ("ADC_DR", "ADC data register"),
        ]:
            tmp_store.upsert_node(
                Node(
                    id=f"f::reg:{name}",
                    kind=NodeKind.REGISTER,
                    name=name, qualified_name=name,
                    doc_id="d", summary=summary,
                )
            )
        embed_graph(tmp_store, vstore, enc)
        assert vstore.count() == 3

        qe = QueryEngine(tmp_store, vstore=vstore, encoder=enc)
        # 精确名
        assert qe.search("PWM_CTRL")[0].name == "PWM_CTRL"
        # 语义检索：用类似词
        results = qe.search("control timer enable", limit=3)
        assert results  # 至少有命中
        vstore.close()


# ---------------------------------------------------------------------------
# Query Engine
# ---------------------------------------------------------------------------


def test_context_bundle(tmp_store):
    tmp_store.upsert_node(
        Node(
            id="f::reg:PWM_CTRL",
            kind=NodeKind.REGISTER,
            name="PWM_CTRL",
            qualified_name="PWM_CTRL",
            doc_id="d",
            summary="PWM control register",
        )
    )
    qe = QueryEngine(tmp_store)
    cb = qe.context("如何配置 PWM_CTRL 来输出 100kHz")
    assert any(n.name == "PWM_CTRL" for n in cb.nodes)


def test_trace_path(tmp_store):
    nodes = ["A", "B", "C", "D"]
    for n in nodes:
        tmp_store.upsert_node(
            Node(id=n, kind=NodeKind.SIGNAL, name=n, doc_id="d")
        )
    chain = [("A", "B"), ("B", "C"), ("C", "D")]
    for src, dst in chain:
        tmp_store.upsert_edge(
            Edge(
                src=src, dst=dst, kind=EdgeKind.CONNECTS_TO,
                evidence=Evidence(extractor="test"),
            )
        )
    qe = QueryEngine(tmp_store)
    paths = qe.trace("A", "D")
    assert paths and paths[0].length == 3


def test_impact_report(tmp_store):
    root_n = Node(id="r", kind=NodeKind.REGISTER, name="R", doc_id="d")
    bf = Node(id="bf", kind=NodeKind.BITFIELD, name="bf", doc_id="d")
    sig = Node(id="sig", kind=NodeKind.SIGNAL, name="sig", doc_id="d")
    for n in (root_n, bf, sig):
        tmp_store.upsert_node(n)
    for src, dst in [("r", "bf"), ("bf", "sig")]:
        tmp_store.upsert_edge(
            Edge(src=src, dst=dst, kind=EdgeKind.HAS_BITFIELD,
                 evidence=Evidence(extractor="test"))
        )
    qe = QueryEngine(tmp_store)
    rep = qe.impact("r", depth=2)
    assert rep is not None
    affected_ids = {n.id for n in rep.affected}
    assert "bf" in affected_ids


# ---------------------------------------------------------------------------
# LLM client (offline)
# ---------------------------------------------------------------------------


def test_llm_extract_json_helpers():
    from docgraph.llm.client import _extract_json
    # plain
    assert _extract_json('{"a": 1}') == {"a": 1}
    # with fence
    assert _extract_json('```json\n{"a": 2}\n```') == {"a": 2}
    # with extra text
    assert _extract_json('blabla\n{"a": 3}\ntrailing') == {"a": 3}


def test_llm_null_provider_errors():
    from docgraph.llm.client import NullLLMProvider
    with pytest.raises(RuntimeError):
        NullLLMProvider().complete("test", model="x")