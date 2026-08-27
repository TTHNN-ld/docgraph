from __future__ import annotations

import pytest
from mcp import Client

from docgraph.core.manifest import (
    BuildRunRecord,
    DerivedStageRecord,
    FileRecord,
    Manifest,
    save_manifest,
)
from docgraph.graph.schema import (
    Block,
    BlockKind,
    Chunk,
    Edge,
    EdgeKind,
    Evidence,
    Location,
    Node,
    NodeKind,
)
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.mcp.server import MCP_TOOL_NAMES, AppContext, create_server
from docgraph.query.engine import QueryEngine


def _runtime(tmp_path) -> AppContext:
    store = SQLiteGraphStore(tmp_path / "graph.db")
    store.init_schema()
    return AppContext(root=tmp_path, store=store, engine=QueryEngine(store))


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


@pytest.mark.anyio
async def test_mcp_v2_lists_only_the_agent_contract(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    server = create_server(lambda: runtime)

    async with Client(server, raise_exceptions=True) as client:
        result = await client.list_tools()

    assert tuple(tool.name for tool in result.tools) == MCP_TOOL_NAMES
    for tool in result.tools:
        assert tool.output_schema is not None
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.open_world_hint is False


@pytest.mark.anyio
async def test_query_returns_structured_content_without_json_reparse(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    runtime.store.upsert_chunks(
        [
            _chunk("dma", "DMA accesses memory through AXI."),
            _chunk("reset", "Reset sequence.", page=2),
        ]
    )
    server = create_server(lambda: runtime)

    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool("docgraph_query", {"task": "DMA AXI"})

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["coverage"] == "retrieval_candidates"
    assert result.structured_content["chunks"][0]["id"] == "dma"
    assert result.structured_content["l1_complete"] is False


@pytest.mark.anyio
async def test_read_validates_input_and_returns_tool_errors(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    server = create_server(lambda: runtime)

    async with Client(server, raise_exceptions=True) as client:
        invalid = await client.call_tool("docgraph_read", {"chunk_ids": []})
        missing = await client.call_tool("docgraph_read", {"chunk_ids": ["missing"]})

    assert invalid.is_error is True
    assert missing.is_error is True
    assert "None of the requested chunk IDs exist" in missing.content[0].text


@pytest.mark.anyio
async def test_read_deduplicates_evidence_and_keeps_source_quality(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    block_id = "doc#p1#b0"
    runtime.store.upsert_blocks(
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
    runtime.store.upsert_chunks(
        [
            _chunk("chunk-a", "First L1 view.", block_ids=[block_id]),
            _chunk("chunk-b", "Second L1 view.", block_ids=[block_id]),
        ]
    )
    runtime.store.upsert_node(
        Node(
            id="doc::term:shared",
            kind=NodeKind.TERM,
            name="shared",
            doc_id="doc",
            evidence=Evidence(extractor="unknown", chunk_ids=["chunk-a"]),
            attrs={"source_block_ids": [block_id]},
        )
    )
    server = create_server(lambda: runtime)

    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool(
            "docgraph_read",
            {"chunk_ids": ["chunk-a", "chunk-b"]},
        )

    payload = result.structured_content
    assert payload is not None
    assert [block["id"] for block in payload["blocks"]] == [block_id]
    assert payload["entities"][0]["doc_id"] == "doc"
    assert payload["entities"][0]["source_quality"]["needs_source_check"] is True


@pytest.mark.anyio
async def test_entity_search_is_scoped_to_documents(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    runtime.store.upsert_chunks(
        [
            _chunk("a", "register A", doc_id="doc-a"),
            _chunk("b", "register B", doc_id="doc-b"),
        ]
    )
    for doc_id in ("doc-a", "doc-b"):
        runtime.store.upsert_node(
            Node(
                id=f"{doc_id}::register:ctrl",
                kind=NodeKind.REGISTER,
                name="CTRL",
                doc_id=doc_id,
                evidence=Evidence(extractor="table_entity", chunk_ids=[]),
            )
        )
    server = create_server(lambda: runtime)

    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool(
            "docgraph_entities",
            {"query": "CTRL", "kind": "register", "doc_ids": ["doc-b"]},
        )

    payload = result.structured_content
    assert payload is not None
    assert [entity["doc_id"] for entity in payload["entities"]] == ["doc-b"]


@pytest.mark.anyio
async def test_query_cursor_continues_without_repeating_task(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    runtime.store.upsert_chunks(
        [_chunk(f"register-{index:02d}", f"register field {index}") for index in range(25)]
    )
    server = create_server(lambda: runtime)

    async with Client(server, raise_exceptions=True) as client:
        first = await client.call_tool("docgraph_query", {"task": "register field"})
        cursor = first.structured_content["next_cursor"]
        second = await client.call_tool("docgraph_query", {"cursor": cursor})

    assert first.structured_content["returned_chunks"] == 20
    assert second.structured_content["returned_chunks"] == 5
    assert second.structured_content["next_cursor"] is None


@pytest.mark.anyio
async def test_neighbors_are_bounded_and_use_uniform_entity_views(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    nodes = [
        Node(
            id=f"doc::module:{name}",
            kind=NodeKind.MODULE,
            name=name,
            doc_id="doc",
            evidence=Evidence(extractor="unknown", chunk_ids=[]),
        )
        for name in ("root", "a", "b")
    ]
    for node in nodes:
        runtime.store.upsert_node(node)
    runtime.store.upsert_edge(
        Edge(
            src=nodes[0].id,
            dst=nodes[1].id,
            kind=EdgeKind.CONTAINS,
            evidence=Evidence(extractor="test"),
        )
    )
    runtime.store.upsert_edge(
        Edge(
            src=nodes[0].id,
            dst=nodes[2].id,
            kind=EdgeKind.CONTAINS,
            evidence=Evidence(extractor="test"),
        )
    )
    server = create_server(lambda: runtime)

    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool(
            "docgraph_neighbors",
            {"node_id": nodes[0].id, "max_nodes": 2},
        )

    payload = result.structured_content
    assert payload is not None
    assert payload["truncated"] is True
    assert len(payload["nodes"]) == 2
    assert all("source_quality" in node for node in payload["nodes"])
    returned_ids = {node["id"] for node in payload["nodes"]}
    assert all(
        edge["src"] in returned_ids and edge["dst"] in returned_ids for edge in payload["edges"]
    )


@pytest.mark.anyio
async def test_outline_requires_document_scope_and_exact_section_id(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    runtime.store.upsert_chunks([_chunk("a", "section text", doc_id="doc")])
    for index, name in enumerate(("Overview", "Interrupts"), start=1):
        runtime.store.upsert_node(
            Node(
                id=f"doc::section:{index}",
                kind=NodeKind.SECTION,
                name=name,
                doc_id="doc",
                location=Location(page=index, section_path=str(index)),
                evidence=Evidence(extractor="section", chunk_ids=[]),
            )
        )
    server = create_server(lambda: runtime)

    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool("docgraph_outline", {"doc_id": "doc"})
        missing = await client.call_tool(
            "docgraph_outline",
            {"doc_id": "doc", "section_id": "Interrupts"},
        )

    assert [section["name"] for section in result.structured_content["sections"]] == [
        "Overview",
        "Interrupts",
    ]
    assert missing.is_error is True


@pytest.mark.anyio
async def test_documents_combines_manifest_and_index_status(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    runtime.store.upsert_chunks([_chunk("a", "indexed text", doc_id="doc")])
    save_manifest(
        tmp_path,
        Manifest(
            files={
                "docs/spec.pdf": FileRecord(
                    path="docs/spec.pdf",
                    doc_id="doc",
                    parser="docling",
                    status="linked",
                )
            },
            last_build=BuildRunRecord(status="degraded", files_failed=0),
            derived={"linker": DerivedStageRecord(status="error", error="linker unavailable")},
        ),
    )
    server = create_server(lambda: runtime)

    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool("docgraph_documents")

    payload = result.structured_content
    assert payload is not None
    assert payload["documents"] == [
        {
            "doc_id": "doc",
            "path": "docs/spec.pdf",
            "parser": "docling",
            "status": "linked",
            "quality_status": None,
            "last_run": None,
            "error": None,
            "warnings": [],
            "chunks": 1,
            "characters": len("indexed text"),
        }
    ]
    assert payload["build"]["status"] == "degraded"
    assert payload["derived"]["linker"]["error"] == "linker unavailable"
