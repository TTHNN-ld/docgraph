"""Stable paginated traversal over graph-store collections."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from docgraph.graph.schema import Node, NodeKind
from docgraph.graph.store import GraphStore, NodeQuery


def iter_nodes(store: GraphStore, kind: NodeKind, *, page_size: int = 2000) -> Iterator[Node]:
    yield from iter_node_query(store, NodeQuery(kind=kind), page_size=page_size)


def iter_node_query(
    store: GraphStore, query: NodeQuery, *, page_size: int = 2000
) -> Iterator[Node]:
    offset = 0
    while True:
        page = store.search_nodes(query.model_copy(update={"limit": page_size, "offset": offset}))
        yield from page
        if len(page) < page_size:
            return
        offset += len(page)


def iter_chunks(
    store: Any,
    *,
    doc_ids: list[str] | None = None,
    page_size: int = 2000,
) -> Iterator[Any]:
    after: tuple[str, int, int, str] | None = None
    while True:
        page = store.list_chunks_page(doc_ids=doc_ids, after=after, limit=page_size)
        yield from page
        if len(page) < page_size:
            return
        last = page[-1]
        after = (
            last.doc_id,
            last.page_start or last.page or 0,
            last.page_end or last.page or 0,
            last.id,
        )
