"""MCP v2 server for the Agent-facing DocGraph query contract."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from docgraph.core.bootstrap import bootstrap
from docgraph.core.config import docgraph_dir, load_config, project_root_from_cwd
from docgraph.core.dotenv import autoload_env
from docgraph.core.manifest import load_manifest
from docgraph.embeddings.factory import open_query_embeddings
from docgraph.graph.schema import EdgeKind, NodeKind
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.query.engine import ContextRequestError, QueryEngine, entity_view
from docgraph.version import __version__

MCP_TOOL_NAMES = (
    "docgraph_query",
    "docgraph_read",
    "docgraph_entities",
    "docgraph_neighbors",
    "docgraph_outline",
    "docgraph_documents",
)

_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


class _ResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QueryResult(_ResultModel):
    coverage: Literal["complete_l1", "paginated_l1", "retrieval_candidates"]
    l1_complete: bool
    truncated: bool
    next_cursor: str | None = None
    total_documents: int
    total_chunks: int
    returned_chunks: int
    remaining_candidates: int
    retrieval_methods: list[str] = Field(default_factory=list)
    chunks: list[dict[str, Any]] = Field(default_factory=list)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ReadResult(_ResultModel):
    requested_chunk_ids: list[str]
    missing_chunk_ids: list[str]
    chunks: list[dict[str, Any]]
    blocks: list[dict[str, Any]]
    entities: list[dict[str, Any]]
    links: dict[str, dict[str, list[str]]]
    warnings: list[str] = Field(default_factory=list)


class EntitySearchResult(_ResultModel):
    entities: list[dict[str, Any]]
    returned_count: int
    truncated: bool
    warnings: list[str] = Field(default_factory=list)


class NeighborResult(_ResultModel):
    root_id: str
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    truncated: bool
    warnings: list[str] = Field(default_factory=list)


class OutlineItem(_ResultModel):
    id: str
    name: str
    page: int | None = None
    section_path: str | None = None


class OutlineResult(_ResultModel):
    doc_id: str
    section_id: str | None = None
    sections: list[OutlineItem]
    truncated: bool


class DocumentInfo(_ResultModel):
    doc_id: str
    path: str | None = None
    parser: str | None = None
    status: str | None = None
    quality_status: str | None = None
    last_run: str | None = None
    error: str | None = None
    warnings: list[dict[str, str]] = Field(default_factory=list)
    chunks: int
    characters: int


class GraphSummary(_ResultModel):
    nodes: int
    edges: int
    by_node_kind: dict[str, int]
    by_edge_kind: dict[str, int]
    vectors: int


class BuildInfo(_ResultModel):
    status: str
    completed_at: str | None = None
    files_failed: int = 0
    warnings: list[dict[str, str]] = Field(default_factory=list)
    cost_usd: float = 0.0


class DerivedIndexInfo(_ResultModel):
    status: str
    last_run: str | None = None
    error: str | None = None
    items: int = 0
    cost_usd: float = 0.0


class DocumentsResult(_ResultModel):
    documents: list[DocumentInfo]
    graph: GraphSummary
    build: BuildInfo | None = None
    derived: dict[str, DerivedIndexInfo] = Field(default_factory=dict)


@dataclass
class AppContext:
    root: Path
    store: SQLiteGraphStore
    engine: QueryEngine


def _open_runtime() -> AppContext:
    root = project_root_from_cwd()
    if not docgraph_dir(root).is_dir():
        raise RuntimeError("No .docgraph/ found in cwd or parents. Run docgraph init first.")
    autoload_env(root)
    cfg = load_config(root)
    bootstrap()
    store = SQLiteGraphStore(docgraph_dir(root) / "graph.db")
    store.init_schema()

    vstore, encoder = open_query_embeddings(cfg.embeddings, cfg.storage, docgraph_dir(root))
    return AppContext(root=root, store=store, engine=QueryEngine(store, vstore, encoder))


def _engine(ctx: Context[AppContext]) -> QueryEngine:
    return ctx.request_context.lifespan_context.engine


def _as_tool_error(exc: ContextRequestError) -> ToolError:
    return ToolError(f"{exc.code}: {exc}")


def _query_result(raw: dict[str, Any]) -> QueryResult:
    selection = raw["selection"]
    warnings: list[str] = []
    if selection["coverage"] == "retrieval_candidates":
        warnings.append("These are retrieval candidates; an empty result does not prove absence.")
    if selection.get("enrichments_truncated"):
        warnings.append("Some related entities were omitted by the entity budget.")
    return QueryResult(
        coverage=selection["coverage"],
        l1_complete=selection["l1_complete"],
        truncated=selection["truncated"],
        next_cursor=selection.get("next_cursor"),
        total_documents=selection["total_docs"],
        total_chunks=selection["total_chunks"],
        returned_chunks=selection["returned_chunks"],
        remaining_candidates=selection["unreturned_candidates"],
        retrieval_methods=selection.get("retrieval_methods", []),
        chunks=raw["chunks"],
        entities=raw.get("enrichments", []),
        warnings=warnings,
    )


def create_server(
    runtime_factory: Callable[[], AppContext] = _open_runtime,
) -> MCPServer[AppContext]:
    @asynccontextmanager
    async def lifespan(server: MCPServer[AppContext]) -> AsyncIterator[AppContext]:
        runtime = runtime_factory()
        try:
            yield runtime
        finally:
            runtime.store.close()

    server = MCPServer(
        "DocGraph",
        version=__version__,
        instructions=(
            "Use docgraph_query for document questions. Read returned chunks directly; "
            "call docgraph_read only for table, image, layout, or source verification. "
            "L2 entities are navigation aids and must follow source_quality."
        ),
        lifespan=lifespan,
    )

    @server.tool(title="Query documents", annotations=_READ_ONLY)
    def docgraph_query(
        task: Annotated[
            str | None,
            Field(description="Question or retrieval intent. Omit to browse in document order."),
        ] = None,
        doc_ids: Annotated[
            list[str] | None,
            Field(description="Optional document IDs from docgraph_documents."),
        ] = None,
        cursor: Annotated[
            str | None,
            Field(description="Opaque next_cursor from the previous call."),
        ] = None,
        include_entities: Annotated[
            bool,
            Field(description="Include L2 entities linked to the returned chunks."),
        ] = False,
        *,
        ctx: Context[AppContext],
    ) -> QueryResult:
        """Find relevant source text, or browse documents when task is omitted."""
        try:
            raw = _engine(ctx).agent_query(
                task=task,
                doc_ids=doc_ids,
                cursor=cursor,
                include_entities=include_entities,
            )
        except ContextRequestError as exc:
            raise _as_tool_error(exc) from exc
        return _query_result(raw)

    @server.tool(title="Read source evidence", annotations=_READ_ONLY)
    def docgraph_read(
        chunk_ids: Annotated[
            list[str],
            Field(
                min_length=1,
                max_length=20,
                description="One to twenty chunk IDs returned by docgraph_query.",
            ),
        ],
        *,
        ctx: Context[AppContext],
    ) -> ReadResult:
        """Read complete chunks with deduplicated original blocks and related entities."""
        try:
            raw = _engine(ctx).fetch_many(chunk_ids)
        except ContextRequestError as exc:
            raise _as_tool_error(exc) from exc
        if not raw["chunks"]:
            raise ToolError("None of the requested chunk IDs exist. Run docgraph_query again.")
        warnings = []
        if raw["missing_chunk_ids"]:
            warnings.append(
                "Some requested chunk IDs were not found; the remaining evidence is complete."
            )
        return ReadResult(
            requested_chunk_ids=raw["requested_chunk_ids"],
            missing_chunk_ids=raw["missing_chunk_ids"],
            chunks=raw["chunks"],
            blocks=raw["blocks"],
            entities=raw["entities"],
            links=raw["links"],
            warnings=warnings,
        )

    @server.tool(title="Search entities", annotations=_READ_ONLY)
    def docgraph_entities(
        query: Annotated[str, Field(min_length=1, description="Entity name or alias.")],
        kind: Annotated[NodeKind | None, Field(description="Optional entity kind.")] = None,
        doc_ids: Annotated[
            list[str] | None,
            Field(description="Optional document IDs from docgraph_documents."),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
        *,
        ctx: Context[AppContext],
    ) -> EntitySearchResult:
        """Search L2 entities within an optional document scope."""
        if not query.strip():
            raise ToolError("query cannot be blank")
        try:
            nodes = _engine(ctx).search(
                query.strip(),
                kind=kind,
                limit=limit + 1,
                doc_ids=doc_ids,
            )
        except ContextRequestError as exc:
            raise _as_tool_error(exc) from exc
        truncated = len(nodes) > limit
        entities = [entity_view(node) for node in nodes[:limit]]
        warnings = []
        if any(item["source_quality"]["needs_source_check"] for item in entities):
            warnings.append("Some entities require verification through their source_chunk_ids.")
        return EntitySearchResult(
            entities=entities,
            returned_count=len(entities),
            truncated=truncated,
            warnings=warnings,
        )

    @server.tool(title="Explore entity relationships", annotations=_READ_ONLY)
    def docgraph_neighbors(
        node_id: Annotated[str, Field(min_length=1, description="Starting L2 node ID.")],
        edge_kinds: Annotated[
            list[EdgeKind] | None,
            Field(description="Optional relationship kinds to follow."),
        ] = None,
        depth: Annotated[int, Field(ge=1, le=3)] = 1,
        max_nodes: Annotated[int, Field(ge=1, le=100)] = 50,
        *,
        ctx: Context[AppContext],
    ) -> NeighborResult:
        """Expand a bounded L2 neighborhood and preserve source references."""
        qe = _engine(ctx)
        if qe.node(node_id) is None:
            raise ToolError(f"Unknown node_id: {node_id}")
        subgraph = qe.neighbors(
            node_id,
            edge_kinds=edge_kinds,
            depth=depth,
            limit=max_nodes + 1,
        )
        truncated = len(subgraph.nodes) > max_nodes
        nodes = subgraph.nodes[:max_nodes]
        node_ids = {node.id for node in nodes}
        edges = [
            edge.model_dump(mode="json")
            for edge in subgraph.edges
            if edge.src in node_ids and edge.dst in node_ids
        ]
        node_views = [entity_view(node) for node in nodes]
        warnings = []
        if truncated:
            warnings.append("The neighborhood reached max_nodes; narrow edge_kinds or depth.")
        if any(item["source_quality"]["needs_source_check"] for item in node_views):
            warnings.append("Some nodes require verification through their source_chunk_ids.")
        return NeighborResult(
            root_id=node_id,
            nodes=node_views,
            edges=edges,
            truncated=truncated,
            warnings=warnings,
        )

    @server.tool(title="Browse document outline", annotations=_READ_ONLY)
    def docgraph_outline(
        doc_id: Annotated[str, Field(min_length=1, description="Document ID.")],
        section_id: Annotated[
            str | None,
            Field(description="Optional exact section node ID to expand."),
        ] = None,
        depth: Annotated[int, Field(ge=1, le=3)] = 1,
        limit: Annotated[int, Field(ge=1, le=500)] = 200,
        *,
        ctx: Context[AppContext],
    ) -> OutlineResult:
        """List a document outline or expand one exact section node."""
        try:
            nodes = _engine(ctx).outline(
                doc_id,
                section_id=section_id,
                depth=depth,
                limit=limit + 1,
            )
        except ContextRequestError as exc:
            raise _as_tool_error(exc) from exc
        if section_id is not None and not nodes:
            raise ToolError(f"Unknown section_id {section_id!r} in document {doc_id!r}.")
        truncated = len(nodes) > limit
        sections = [
            OutlineItem(
                id=node.id,
                name=node.name,
                page=node.location.page,
                section_path=node.location.section_path,
            )
            for node in nodes[:limit]
        ]
        return OutlineResult(
            doc_id=doc_id,
            section_id=section_id,
            sections=sections,
            truncated=truncated,
        )

    @server.tool(title="List documents and index status", annotations=_READ_ONLY)
    def docgraph_documents(*, ctx: Context[AppContext]) -> DocumentsResult:
        """List indexed documents with build metadata and graph statistics."""
        runtime = ctx.request_context.lifespan_context
        status = runtime.engine.status()
        manifest = load_manifest(runtime.root)
        records = {
            record.doc_id: record for record in manifest.files.values() if record.doc_id is not None
        }
        documents = []
        for doc_id in status.docs:
            stats = runtime.store.chunk_corpus_stats([doc_id])
            record = records.get(doc_id)
            documents.append(
                DocumentInfo(
                    doc_id=doc_id,
                    path=record.path if record else None,
                    parser=record.parser if record else None,
                    status=record.status if record else None,
                    quality_status=record.quality_status if record else None,
                    last_run=record.last_run if record else None,
                    error=record.error if record else None,
                    warnings=record.warnings if record else [],
                    chunks=stats["total_chunks"],
                    characters=stats["total_chars"],
                )
            )
        return DocumentsResult(
            documents=documents,
            graph=GraphSummary(
                nodes=status.nodes_total,
                edges=status.edges_total,
                by_node_kind=status.by_kind,
                by_edge_kind=status.by_edge_kind,
                vectors=status.vector_count,
            ),
            build=(
                BuildInfo(
                    status=manifest.last_build.status,
                    completed_at=manifest.last_build.completed_at,
                    files_failed=manifest.last_build.files_failed,
                    warnings=manifest.last_build.warnings,
                    cost_usd=manifest.last_build.cost_usd,
                )
                if manifest.last_build is not None
                else None
            ),
            derived={
                name: DerivedIndexInfo(
                    status=stage.status,
                    last_run=stage.last_run,
                    error=stage.error,
                    items=stage.items,
                    cost_usd=stage.cost_usd,
                )
                for name, stage in manifest.derived.items()
            },
        )

    return server


mcp = create_server()


def run_stdio() -> None:
    mcp.run()
