from __future__ import annotations

import json

import pytest

from docgraph.graph.schema import (
    Block,
    BlockKind,
    Chunk,
    Evidence,
    Node,
    NodeKind,
)
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.query.engine import ContextRequestError, QueryEngine


def _store(tmp_path) -> SQLiteGraphStore:
    store = SQLiteGraphStore(tmp_path / "graph.db")
    store.init_schema()
    return store


def _chunk(
    chunk_id: str,
    text: str,
    *,
    doc_id: str = "doc",
    page: int = 1,
    block_ids: list[str] | None = None,
) -> Chunk:
    return Chunk(
        id=chunk_id,
        doc_id=doc_id,
        page=page,
        page_start=page,
        page_end=page,
        text=text,
        block_ids=block_ids or [],
        kind="section",
        source_hash=f"source:{chunk_id}:{text}",
    )


def test_auto_returns_complete_small_l1_without_rewriting(tmp_path) -> None:
    store = _store(tmp_path)
    store.upsert_chunks(
        [
            _chunk("b", "Second page — exact source text.", page=2),
            _chunk("a", "First page — exact source text.", page=1),
        ]
    )

    result = QueryEngine(store).document_context()

    assert result["selection"]["mode"] == "full"
    assert result["selection"]["coverage"] == "complete_l1"
    assert result["selection"]["l1_complete"] is True
    assert [chunk["id"] for chunk in result["chunks"]] == ["a", "b"]
    assert result["chunks"][0]["text"] == "First page — exact source text."
    assert result["selection"]["corpus_chunks_not_returned"] == 0
    store.close()


def test_auto_large_scope_requires_agent_task(tmp_path) -> None:
    store = _store(tmp_path)
    store.upsert_chunks([_chunk("a", "A" * 30), _chunk("b", "B" * 30, page=2)])

    with pytest.raises(ContextRequestError) as exc:
        QueryEngine(store).document_context(max_chars=40)

    assert exc.value.code == "task_required"
    store.close()


def test_auto_large_scope_returns_explained_retrieval_candidates(tmp_path) -> None:
    store = _store(tmp_path)
    store.upsert_chunks(
        [
            _chunk("dma", "DMA accesses system memory through the AXI master interface."),
            _chunk("irq", "Interrupt status and mask register description.", page=2),
            _chunk("reset", "Reset sequencing requirements for the subsystem.", page=3),
        ]
    )

    result = QueryEngine(store).document_context(
        task="DMA system memory",
        max_chars=80,
        max_chunks=2,
    )

    selection = result["selection"]
    assert selection["mode"] == "search"
    assert selection["coverage"] == "retrieval_candidates"
    assert selection["l1_complete"] is False
    assert selection["retrieval_methods"]
    assert selection["corpus_chunks_not_returned"] >= 2
    assert result["chunks"][0]["id"] == "dma"
    assert result["chunks"][0]["rank_reasons"]
    assert result["chunks"][0]["text"].startswith("DMA accesses")
    store.close()


def test_search_scope_is_applied_before_candidate_limit(tmp_path) -> None:
    store = _store(tmp_path)
    store.upsert_chunks(
        [
            _chunk(
                f"other-{index}",
                "shared search term",
                doc_id="other",
            )
            for index in range(320)
        ]
    )
    store.upsert_chunks(
        [_chunk("target", "shared search term target", doc_id="selected")]
    )

    result = QueryEngine(store).document_context(
        task="shared search term",
        mode="search",
        doc_ids=["selected"],
        include_enrichments=False,
    )

    assert [chunk["id"] for chunk in result["chunks"]] == ["target"]
    store.close()


def test_natural_language_task_expands_to_retrieval_terms(tmp_path) -> None:
    store = _store(tmp_path)
    store.upsert_chunks(
        [
            _chunk(
                "register-table",
                "Reg name | Field | Msb | Lsb | SWaccess | Default\n"
                "per_vector_misc | mask_bit | 20 | 20 | RW | 0x1",
            ),
            _chunk("unrelated", "Clock source and reset distribution architecture."),
        ]
    )

    result = QueryEngine(store).document_context(
        mode="search",
        task=(
            "UVM RAL register modeling: find all register tables containing field "
            "definitions with offset, access, reset value, and bit-range columns"
        ),
        include_enrichments=False,
    )

    assert result["chunks"][0]["id"] == "register-table"
    assert "term-overlap" in " ".join(result["chunks"][0]["rank_reasons"])
    store.close()


def test_search_view_pages_large_candidate_sets(tmp_path) -> None:
    store = _store(tmp_path)
    store.upsert_chunks(
        [_chunk(f"register-{index:02d}", f"register field {index}") for index in range(30)]
    )

    result = QueryEngine(store).document_context(
        mode="search",
        task="register field",
        max_chunks=80,
        include_enrichments=False,
    )

    assert len(result["chunks"]) == 20
    assert result["selection"]["response_chunk_limit"] == 20
    assert result["selection"]["unreturned_candidates"] == 10
    assert result["selection"]["next_cursor"]

    continued = QueryEngine(store).document_context(
        mode="search",
        cursor=result["selection"]["next_cursor"],
        max_chunks=80,
        include_enrichments=False,
    )
    assert len(continued["chunks"]) == 10
    assert continued["selection"]["next_cursor"] is None
    store.close()


def test_full_mode_pages_without_claiming_complete_l1(tmp_path) -> None:
    store = _store(tmp_path)
    store.upsert_chunks(
        [
            _chunk("a", "A" * 20, page=1),
            _chunk("b", "B" * 20, page=2),
            _chunk("c", "C" * 20, page=3),
        ]
    )
    engine = QueryEngine(store)

    first = engine.document_context(mode="full", max_chars=25, max_chunks=2)
    second = engine.document_context(
        mode="full",
        max_chars=25,
        max_chunks=2,
        cursor=first["selection"]["next_cursor"],
    )
    third = engine.document_context(
        mode="full",
        max_chars=25,
        max_chunks=2,
        cursor=second["selection"]["next_cursor"],
    )

    assert [first["chunks"][0]["id"], second["chunks"][0]["id"], third["chunks"][0]["id"]] == [
        "a",
        "b",
        "c",
    ]
    assert first["selection"]["coverage"] == "paginated_l1"
    assert third["selection"]["l1_complete"] is False
    assert third["selection"]["next_cursor"] is None
    store.close()


def test_cursor_expires_when_l1_changes(tmp_path) -> None:
    store = _store(tmp_path)
    store.upsert_chunks([_chunk("a", "A" * 20), _chunk("b", "B" * 20, page=2)])
    engine = QueryEngine(store)
    first = engine.document_context(mode="full", max_chars=25, max_chunks=1)
    store.upsert_chunks([_chunk("b", "changed content", page=2)])

    with pytest.raises(ContextRequestError) as exc:
        engine.document_context(
            mode="full",
            max_chars=25,
            max_chunks=1,
            cursor=first["selection"]["next_cursor"],
        )

    assert exc.value.code == "cursor_expired"
    store.close()


def test_vlm_data_is_separate_from_l1_text(tmp_path) -> None:
    store = _store(tmp_path)
    block_id = "doc#p1#b0"
    chunk_id = "figure-chunk"
    store.upsert_blocks(
        [
            Block(
                id=block_id,
                doc_id="doc",
                page=1,
                kind=BlockKind.FIGURE,
                text="Figure 1 System architecture",
                image_path="figures/system.png",
            )
        ]
    )
    store.upsert_chunks(
        [
            _chunk(
                chunk_id,
                "Figure 1 System architecture",
                block_ids=[block_id],
            )
        ]
    )
    store.upsert_node(
        Node(
            id="doc::figure:system",
            kind=NodeKind.FIGURE,
            name="System architecture",
            doc_id="doc",
            summary="DMA connects to system memory through AXI.",
            evidence=Evidence(extractor="figure@vlm", chunk_ids=[chunk_id], pages=[1]),
            attrs={
                "source": "figure@vlm",
                "source_chunk_ids": [chunk_id],
                "source_block_ids": [block_id],
                "semantic_summary": "DMA connects to system memory through AXI.",
                "mermaid": "graph LR\nDMA --> AXI",
            },
        )
    )

    result = QueryEngine(store).document_context()

    assert result["chunks"][0]["text"] == "Figure 1 System architecture"
    assert "DMA connects" not in result["chunks"][0]["text"]
    assert result["enrichments"][0]["attrs"]["semantic_summary"].startswith("DMA")
    assert result["enrichments"][0]["source_quality"]["needs_source_check"] is True
    store.close()


def test_fetch_many_deduplicates_blocks_and_entities(tmp_path) -> None:
    store = _store(tmp_path)
    block_id = "doc#p1#b0"
    store.upsert_blocks(
        [
            Block(
                id=block_id,
                doc_id="doc",
                page=1,
                kind=BlockKind.PARAGRAPH,
                text="Shared source paragraph.",
            )
        ]
    )
    store.upsert_chunks(
        [
            _chunk("chunk-a", "First L1 view.", block_ids=[block_id]),
            _chunk("chunk-b", "Second L1 view.", block_ids=[block_id]),
        ]
    )
    store.upsert_node(
        Node(
            id="doc::term:shared",
            kind=NodeKind.TERM,
            name="shared",
            doc_id="doc",
            summary="Shared extracted fact.",
            evidence=Evidence(extractor="test", chunk_ids=["chunk-a", "chunk-b"], pages=[1]),
            attrs={"source_block_ids": [block_id]},
        )
    )

    result = QueryEngine(store).fetch_many(["chunk-a", "chunk-b", "chunk-a"])

    assert result["requested_chunk_ids"] == ["chunk-a", "chunk-b"]
    assert result["missing_chunk_ids"] == []
    assert [chunk["id"] for chunk in result["chunks"]] == ["chunk-a", "chunk-b"]
    assert [block["id"] for block in result["blocks"]] == [block_id]
    assert [entity["id"] for entity in result["entities"]] == ["doc::term:shared"]
    assert result["links"]["chunk-a"]["block_ids"] == [block_id]
    assert result["links"]["chunk-b"]["entity_ids"] == ["doc::term:shared"]
    store.close()


def test_fetch_many_reports_missing_chunks_and_rejects_empty_input(tmp_path) -> None:
    store = _store(tmp_path)
    store.upsert_chunks([_chunk("present", "Present chunk.")])
    engine = QueryEngine(store)

    result = engine.fetch_many(["present", "missing"])
    assert result["missing_chunk_ids"] == ["missing"]
    assert [chunk["id"] for chunk in result["chunks"]] == ["present"]

    with pytest.raises(ContextRequestError) as exc:
        engine.fetch_many([])
    assert exc.value.code == "invalid_chunk_ids"
    store.close()


def test_mcp_registers_context_and_returns_stable_error_code(tmp_path) -> None:
    from docgraph.mcp.server import TOOLS, _handle_request

    store = _store(tmp_path)
    store.upsert_chunks([_chunk("a", "A" * 50), _chunk("b", "B" * 50, page=2)])
    engine = QueryEngine(store)

    assert "docgraph_context" in {tool["name"] for tool in TOOLS}
    assert "docgraph_fetch_many" in {tool["name"] for tool in TOOLS}
    search_chunks_tool = next(
        tool for tool in TOOLS if tool["name"] == "docgraph_search_chunks"
    )
    assert "doc_ids" in search_chunks_tool["inputSchema"]["properties"]
    response = _handle_request(
        engine,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "docgraph_context",
                "arguments": {"max_chars": 40},
            },
        },
    )

    assert response["error"]["data"]["context_error"] == "task_required"
    json.dumps(response)
    store.close()
