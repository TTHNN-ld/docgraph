from __future__ import annotations


def test_legacy_nodes_default_to_conservative_trust_states():
    from docgraph.graph.schema import Evidence, Node, NodeKind

    candidate = Node(
        id="d#register#ctrl",
        kind=NodeKind.REGISTER,
        name="CTRL",
        doc_id="d",
        evidence=Evidence(extractor="legacy"),
    )
    document_entity = Node(
        id="d#section#1",
        kind=NodeKind.SECTION,
        name="Overview",
        doc_id="d",
    )

    assert candidate.attrs["l2_status"] == "candidate"
    assert candidate.attrs["derivation"]["verified"] is False
    assert document_entity.attrs["l2_status"] == "document_entity"


def test_deterministic_table_node_is_promoted_but_llm_node_is_not():
    from docgraph.extractors.base import ExtractContext
    from docgraph.extractors.schema_registry import RegisterDef, get_schema
    from docgraph.extractors.table_entity import TableEntityExtractor
    from docgraph.graph.schema import DerivationMethod

    schema = get_schema("register")
    assert schema is not None
    extractor = TableEntityExtractor(schema_names=["register"])
    item = RegisterDef(name="CTRL", offset="0x4")
    common = {
        "item": item,
        "schema_name": "register",
        "schema": schema,
        "page": 1,
        "ctx": ExtractContext(family="chip"),
        "doc_id": "d",
        "source_block_ids": ["d#p1#b0"],
        "source_chunk_ids": ["d#c1"],
        "candidate_id": "d#c1#candidate_table",
    }

    deterministic = extractor._node_from_model(
        **common,
        derivation_method=DerivationMethod.DETERMINISTIC,
    )["node"]
    inferred = extractor._node_from_model(
        **common,
        derivation_method=DerivationMethod.LLM_INFERRED,
    )["node"]

    assert deterministic.attrs["l2_status"] == "fact"
    assert deterministic.attrs["derivation"] == {
        "method": "deterministic",
        "extractor": "table_entity:register",
        "confidence": "exact",
        "verified": True,
    }
    assert inferred.attrs["l2_status"] == "candidate"
    assert inferred.attrs["derivation"]["verified"] is False
    assert inferred.attrs["validation_issues"][0]["code"] == "l2.fact.nondeterministic_source"


def test_layer_audit_rejects_unverified_model_fact(tmp_path):
    from docgraph.graph.schema import (
        Block,
        BlockKind,
        Chunk,
        Evidence,
        Node,
        NodeKind,
    )
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.quality.layers import audit_l0_l1

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    store.upsert_blocks([
        Block(
            id="d#p1#b0",
            doc_id="d",
            page=1,
            kind=BlockKind.PARAGRAPH,
            text="CTRL register",
        ),
    ])
    store.upsert_chunks([
        Chunk(
            id="d#c1",
            doc_id="d",
            page=1,
            text="CTRL register",
            source_hash="sha256:test",
            block_ids=["d#p1#b0"],
        ),
    ])
    store.upsert_node(Node(
        id="d#register#ctrl",
        kind=NodeKind.REGISTER,
        name="CTRL",
        doc_id="d",
        evidence=Evidence(extractor="llm_ie", chunk_ids=["d#c1"]),
        attrs={
            "source_block_ids": ["d#p1#b0"],
            "source_chunk_ids": ["d#c1"],
            "l2_status": "fact",
            "derivation": {
                "method": "llm_inferred",
                "extractor": "llm_ie",
                "confidence": "high",
                "verified": False,
            },
            "validation_issues": [],
        },
    ))

    report = audit_l0_l1(store)

    assert report.ok is False
    assert report.totals["l2_facts"] == 1
    assert any(issue.code == "l2.invalid_fact_status" for issue in report.issues)
    store.close()


def test_multisource_merge_upgrades_candidate_to_deterministic_fact(tmp_path):
    from docgraph.graph.schema import Evidence, Node, NodeKind
    from docgraph.graph.sqlite_store import SQLiteGraphStore

    store = SQLiteGraphStore(tmp_path / "g.db")
    store.init_schema()
    common = {
        "id": "d#signal#ready",
        "kind": NodeKind.SIGNAL,
        "name": "ready",
        "doc_id": "d",
    }
    candidate = Node(
        **common,
        evidence=Evidence(extractor="figure@vlm"),
        attrs={
            "source": "figure@vlm",
            "l2_status": "candidate",
            "derivation": {
                "method": "vlm_inferred",
                "extractor": "figure@vlm",
                "confidence": "low",
                "verified": False,
            },
            "validation_issues": [{"code": "needs_verification", "severity": "error"}],
        },
    )
    fact = Node(
        **common,
        evidence=Evidence(extractor="table_entity:signal"),
        attrs={
            "source": "table_entity:signal",
            "l2_status": "fact",
            "derivation": {
                "method": "deterministic",
                "extractor": "table_entity:signal",
                "confidence": "exact",
                "verified": True,
            },
            "validation_issues": [],
        },
    )

    store.upsert_node(candidate)
    store.upsert_node(fact)
    merged = store.get_node(candidate.id)

    assert merged is not None
    assert merged.attrs["l2_status"] == "fact"
    assert merged.attrs["derivation"]["method"] == "deterministic"
    assert merged.attrs["validation_issues"] == []
    assert {item["method"] for item in merged.attrs["derivation_history"]} == {
        "deterministic",
        "vlm_inferred",
    }
    store.close()
