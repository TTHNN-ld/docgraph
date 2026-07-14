"""Query Engine —— Graph + 向量混合检索 + Agent 友好的高层 API。

M2 升级：
- search：图谱命中 → 别名 → fuzzy → 向量语义
- context：组合"按 task 找相关节点"
- trace：path finding（BFS）
- impact：N hops 反向影响分析
- 专项查询：pin / timing / figure / section / glossary
"""
from __future__ import annotations

import base64
import hashlib
import json
from collections import deque

from pydantic import BaseModel, Field

from docgraph.embeddings.base import EmbeddingProvider
from docgraph.embeddings.vector_store import VectorStore
from docgraph.graph.schema import Block, Edge, EdgeKind, Node, NodeKind
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.graph.store import NodeQuery, Subgraph

# ---------------------------------------------------------------------------
# 输出模型
# ---------------------------------------------------------------------------


class StatusReport(BaseModel):
    nodes_total: int
    edges_total: int
    docs: list[str]
    by_kind: dict[str, int] = Field(default_factory=dict)
    by_edge_kind: dict[str, int] = Field(default_factory=dict)
    vector_count: int = 0


class RegisterDetail(BaseModel):
    node: Node
    bitfields: list[Node] = Field(default_factory=list)


class PinDetail(BaseModel):
    node: Node


class TimingDetail(BaseModel):
    node: Node


class FigureDetail(BaseModel):
    node: Node


class SectionDetail(BaseModel):
    node: Node
    children: list[Node] = Field(default_factory=list)


class TermDetail(BaseModel):
    node: Node


class ContextBundle(BaseModel):
    task: str
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    semantic_hits: list[dict] = Field(default_factory=list)
    providers: list[str] = Field(default_factory=list)


class ImpactReport(BaseModel):
    root: Node
    affected: list[Node] = Field(default_factory=list)
    depth: int = 1
    edges: list[Edge] = Field(default_factory=list)


class Path(BaseModel):
    nodes: list[str]
    edges: list[Edge] = Field(default_factory=list)
    length: int


class ContextRequestError(ValueError):
    """A stable, client-actionable context request error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_CONTEXT_CURSOR_VERSION = 1
_CONTEXT_MAX_CHARS = 80_000
_CONTEXT_MAX_CHUNKS = 160
_CONTEXT_MAX_ENRICHMENT_CHARS = 20_000
_CONTEXT_CANDIDATE_LIMIT = 300
_CONTEXT_SEARCH_PAGE_CHUNKS = 20
_FETCH_MANY_MAX_CHUNKS = 20


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class QueryEngine:
    def __init__(
        self,
        store: SQLiteGraphStore,
        vstore: VectorStore | None = None,
        encoder: EmbeddingProvider | None = None,
    ) -> None:
        self.store = store
        self.vstore = vstore
        self.encoder = encoder

    # ------- status -------

    def status(self) -> StatusReport:
        by_kind: dict[str, int] = {}
        for kind in NodeKind:
            c = self.store.count_nodes(kind)
            if c:
                by_kind[kind.value] = c
        by_edge: dict[str, int] = {}
        for ek in EdgeKind:
            c = self.store.count_edges(ek)
            if c:
                by_edge[ek.value] = c
        vec_count = self.vstore.count() if self.vstore else 0
        return StatusReport(
            nodes_total=self.store.count_nodes(),
            edges_total=self.store.count_edges(),
            docs=self.store.list_docs(),
            by_kind=by_kind,
            by_edge_kind=by_edge,
            vector_count=vec_count,
        )

    # ------- 基础检索 -------

    def search(
        self,
        query: str,
        kind: NodeKind | None = None,
        limit: int = 20,
        use_semantic: bool = True,
    ) -> list[Node]:
        # 1. 精确 name
        exact = self.store.search_nodes(NodeQuery(name=query, kind=kind, limit=limit))
        if exact:
            return exact
        # 2. alias
        alias = self.store.search_nodes(NodeQuery(alias=query, kind=kind, limit=limit))
        if alias:
            return alias
        # 3. qualified_name 精确
        qual = self.store.search_nodes(
            NodeQuery(qualified_name=query, kind=kind, limit=limit)
        )
        if qual:
            return qual
        # 4. fuzzy
        fuzzy = self.store.search_nodes(NodeQuery(fuzzy=query, kind=kind, limit=limit))
        if fuzzy:
            return fuzzy
        # 5. 向量语义
        if use_semantic and self.vstore is not None and self.encoder is not None:
            return self._semantic_search(query, kind=kind, top_k=limit)
        return []

    def node(self, id: str) -> Node | None:
        return self.store.get_node(id)

    def neighbors(
        self,
        id: str,
        edge_kinds: list[EdgeKind] | None = None,
        depth: int = 1,
    ) -> Subgraph:
        return self.store.neighbors(id, edge_kinds=edge_kinds, depth=depth)

    # ------- 专项 -------

    def register(self, name: str) -> RegisterDetail | None:
        nodes = self._first_match(name, NodeKind.REGISTER)
        if not nodes:
            return None
        reg = nodes[0]
        sub = self.store.neighbors(
            reg.id, edge_kinds=[EdgeKind.HAS_BITFIELD], depth=1
        )
        bitfields = [n for n in sub.nodes if n.kind == NodeKind.BITFIELD]
        bitfields.sort(
            key=lambda n: int(n.attrs.get("bit_high", 0)), reverse=True
        )
        return RegisterDetail(node=reg, bitfields=bitfields)

    def pin(self, name: str) -> PinDetail | None:
        nodes = self._first_match(name, NodeKind.PIN)
        return PinDetail(node=nodes[0]) if nodes else None

    def timing(self, name: str) -> TimingDetail | None:
        nodes = self._first_match(name, NodeKind.PARAMETER)
        return TimingDetail(node=nodes[0]) if nodes else None

    def figure(self, id_or_name: str) -> FigureDetail | None:
        n = self.store.get_node(id_or_name)
        if n is None or n.kind != NodeKind.FIGURE:
            cands = self._first_match(id_or_name, NodeKind.FIGURE)
            if cands:
                n = cands[0]
        return FigureDetail(node=n) if n else None

    def section(self, path_or_id: str) -> SectionDetail | None:
        n = self.store.get_node(path_or_id)
        if n is None or n.kind != NodeKind.SECTION:
            cands = self.store.search_nodes(
                NodeQuery(kind=NodeKind.SECTION, fuzzy=path_or_id, limit=1)
            )
            if cands:
                n = cands[0]
        if n is None:
            return None
        sub = self.store.neighbors(n.id, edge_kinds=[EdgeKind.CONTAINS], depth=1)
        children = [c for c in sub.nodes if c.id != n.id and c.kind == NodeKind.SECTION]
        return SectionDetail(node=n, children=children)

    def glossary(self, term: str) -> list[TermDetail]:
        nodes = self.store.search_nodes(
            NodeQuery(kind=NodeKind.TERM, fuzzy=term, limit=10)
        )
        if not nodes:
            # 也按别名查
            nodes = self.store.search_nodes(
                NodeQuery(alias=term, kind=NodeKind.TERM, limit=10)
            )
        return [TermDetail(node=n) for n in nodes]

    # ------- 高级 -------

    def context(self, task: str, max_nodes: int = 20) -> ContextBundle:
        """根据 task 文本拉一束"相关包"：图谱命中 + 向量命中 + 一阶邻居。"""
        providers = []
        nodes_map: dict[str, Node] = {}
        edges_map: dict[tuple[str, str, str], Edge] = {}
        semantic_hits: list[dict] = []

        # 1. 试图把 task 当成名字精确查
        for tok in _tokenize_task(task):
            for n in self.store.search_nodes(NodeQuery(name=tok, limit=3)):
                nodes_map[n.id] = n
            for n in self.store.search_nodes(NodeQuery(qualified_name=tok, limit=3)):
                nodes_map[n.id] = n
        if nodes_map:
            providers.append("name-hit")

        # 2. 向量检索
        if self.vstore is not None and self.encoder is not None:
            hits = self._semantic_search_raw(task, top_k=max_nodes)
            providers.append("semantic")
            for nid, score in hits:
                if nid in nodes_map:
                    continue
                n = self.store.get_node(nid)
                if n is None:
                    continue
                nodes_map[n.id] = n
                semantic_hits.append({"id": n.id, "score": round(score, 4)})

        # 3. 拉一阶邻居丰富上下文
        seeds = list(nodes_map.values())[: max_nodes // 2]
        for seed in seeds:
            sub = self.store.neighbors(seed.id, depth=1, limit=10)
            for n in sub.nodes:
                if n.id != seed.id and n.id not in nodes_map and len(nodes_map) < max_nodes:
                    nodes_map[n.id] = n
            for e in sub.edges:
                key = (e.src, e.dst, e.kind.value)
                edges_map[key] = e

        if nodes_map and "neighbors" not in providers:
            providers.append("neighbors")

        return ContextBundle(
            task=task,
            nodes=list(nodes_map.values())[:max_nodes],
            edges=list(edges_map.values()),
            semantic_hits=semantic_hits,
            providers=providers,
        )

    def trace(self, from_id: str, to_id: str, max_depth: int = 5) -> list[Path]:
        """BFS 找最短路径。"""
        if from_id == to_id:
            return [Path(nodes=[from_id], length=0)]
        store = self.store
        visited = {from_id: None}  # node_id -> (parent_id, edge)
        queue: deque[tuple[str, int]] = deque([(from_id, 0)])
        found = False
        while queue:
            cur, d = queue.popleft()
            if d >= max_depth:
                continue
            sub = store.neighbors(cur, depth=1, limit=200)
            for e in sub.edges:
                # 沿出边走（边的方向）
                if e.src != cur:
                    continue
                nxt = e.dst
                if nxt in visited:
                    continue
                visited[nxt] = (cur, e)
                if nxt == to_id:
                    found = True
                    break
                queue.append((nxt, d + 1))
            if found:
                break

        if to_id not in visited:
            return []

        # 回溯
        path_nodes: list[str] = []
        path_edges: list[Edge] = []
        cur = to_id
        while cur is not None:
            path_nodes.append(cur)
            prev = visited.get(cur)
            if prev is None:
                break
            parent, edge = prev
            path_edges.append(edge)
            cur = parent
        path_nodes.reverse()
        path_edges.reverse()
        return [Path(nodes=path_nodes, edges=path_edges, length=len(path_nodes) - 1)]

    def impact(self, id: str, depth: int = 2) -> ImpactReport | None:
        root = self.store.get_node(id)
        if root is None:
            return None
        # 出边代表"影响下游"
        sub = self.store.neighbors(id, depth=depth, limit=500)
        affected = [n for n in sub.nodes if n.id != id]
        return ImpactReport(
            root=root,
            affected=affected,
            depth=depth,
            edges=sub.edges,
        )

    # ------- helpers -------

    def _first_match(self, name: str, kind: NodeKind) -> list[Node]:
        exact = self.store.search_nodes(NodeQuery(name=name, kind=kind, limit=3))
        if exact:
            return exact
        return self.store.search_nodes(NodeQuery(fuzzy=name, kind=kind, limit=3))

    def _semantic_search(
        self, query: str, kind: NodeKind | None = None, top_k: int = 10
    ) -> list[Node]:
        hits = self._semantic_search_raw(query, top_k=top_k * 3)
        out: list[Node] = []
        for nid, _ in hits:
            n = self.store.get_node(nid)
            if n is None:
                continue
            if kind and n.kind != kind:
                continue
            out.append(n)
            if len(out) >= top_k:
                break
        return out

    def _semantic_search_raw(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        if self.vstore is None or self.encoder is None:
            return []
        vec = self.encoder.encode([query])[0]
        return self.vstore.search(vec, self.encoder.model, top_k=top_k)

    # ------- L0/L1 回溯接口（M7-P4） -------

    def blocks(self, block_ids: list[str]) -> list[Block]:
        """按 ID 取 L0 Block（原文无损回溯）。"""
        return self.store.get_blocks(block_ids)

    def blocks_for_page(self, doc_id: str, page: int) -> list[Block]:
        return self.store.blocks_for_page(doc_id, page)

    def fetch(self, chunk_id: str) -> dict:
        """Return chunk + its L0 blocks + L2 entities that reference them.

        The primary agent reading path: after search_chunks discovers relevant
        chunks, fetch delivers the complete original content alongside any L2
        extraction results. The agent sees both and can judge L2 accuracy against
        the original table/text/figure.
        """
        chunk = self.store.get_chunk(chunk_id)
        if chunk is None:
            return {"error": "not_found", "chunk_id": chunk_id}
        evidence = self._chunk_evidence(chunk)
        return {
            "chunk": evidence["chunk"],
            "blocks": evidence["blocks"],
            "entities": evidence["entities"],
            "usage_policy": (
                "The chunk text and blocks are the authoritative source (L0/L1). "
                "Entities are L2 extraction candidates — check each entity's "
                "source_quality.needs_source_check before relying on it. "
                "If false (deterministic/verified), the extraction is table-based and reliable. "
                "If true (vlm/llm), verify against the original blocks above."
            ),
        }

    def fetch_many(self, chunk_ids: list[str]) -> dict:
        """Return deduplicated L0/L1 evidence for several chunks in one call."""
        if not isinstance(chunk_ids, list):
            raise ContextRequestError("invalid_chunk_ids", "chunk_ids must be a list")
        normalized: list[str] = []
        for raw_id in chunk_ids:
            chunk_id = str(raw_id).strip()
            if chunk_id and chunk_id not in normalized:
                normalized.append(chunk_id)
        if not normalized:
            raise ContextRequestError("invalid_chunk_ids", "chunk_ids cannot be empty")
        if len(normalized) > _FETCH_MANY_MAX_CHUNKS:
            raise ContextRequestError(
                "too_many_chunk_ids",
                f"fetch_many accepts at most {_FETCH_MANY_MAX_CHUNKS} chunk_ids",
            )

        chunks: list[dict] = []
        blocks_by_id: dict[str, dict] = {}
        entities_by_id: dict[str, dict] = {}
        links: dict[str, dict] = {}
        missing: list[str] = []
        for chunk_id in normalized:
            chunk = self.store.get_chunk(chunk_id)
            if chunk is None:
                missing.append(chunk_id)
                continue
            evidence = self._chunk_evidence(chunk)
            chunks.append(evidence["chunk"])
            block_ids: list[str] = []
            entity_ids: list[str] = []
            for block in evidence["blocks"]:
                blocks_by_id.setdefault(block["id"], block)
                block_ids.append(block["id"])
            for entity in evidence["entities"]:
                entities_by_id.setdefault(entity["id"], entity)
                entity_ids.append(entity["id"])
            links[chunk.id] = {
                "block_ids": block_ids,
                "entity_ids": sorted(set(entity_ids)),
            }

        return {
            "requested_chunk_ids": normalized,
            "missing_chunk_ids": missing,
            "chunks": chunks,
            "blocks": list(blocks_by_id.values()),
            "entities": list(entities_by_id.values()),
            "links": links,
            "usage_policy": (
                "Batch evidence is deduplicated across chunks. Chunk text and blocks "
                "are authoritative L1/L0 source; entities are L2 candidates. Use "
                "links[chunk_id] to see which blocks and entities support each chunk."
            ),
        }

    def _chunk_evidence(self, chunk) -> dict:
        blocks = self.store.get_blocks(chunk.block_ids) if chunk.block_ids else []
        entities_by_id: dict[str, dict] = {}
        for node in self.store.get_entities_for_chunk(chunk.id):
            entities_by_id.setdefault(node.id, _entity_summary(node))
        for block in blocks:
            for node in self.store.get_entities_for_block(block.id):
                entities_by_id.setdefault(node.id, _entity_summary(node))
        return {
            "chunk": _chunk_view(chunk),
            "blocks": [_block_view(block) for block in blocks],
            "entities": list(entities_by_id.values()),
        }

    def document_context(
        self,
        *,
        task: str | None = None,
        mode: str = "auto",
        doc_ids: list[str] | None = None,
        max_chars: int = 40_000,
        max_chunks: int = 80,
        include_enrichments: bool = True,
        max_enrichment_chars: int = 8_000,
        cursor: str | None = None,
    ) -> dict:
        """Return a transparent, budgeted L1 view without summarizing chunks."""
        requested_mode = (mode or "auto").strip().lower()
        if requested_mode not in {"auto", "full", "search"}:
            raise ContextRequestError(
                "invalid_mode", "mode must be one of: auto, full, search"
            )
        max_chars = _bounded_int(
            "max_chars", max_chars, minimum=1, maximum=_CONTEXT_MAX_CHARS
        )
        max_chunks = _bounded_int(
            "max_chunks", max_chunks, minimum=1, maximum=_CONTEXT_MAX_CHUNKS
        )
        max_enrichment_chars = _bounded_int(
            "max_enrichment_chars",
            max_enrichment_chars,
            minimum=0,
            maximum=_CONTEXT_MAX_ENRICHMENT_CHARS,
        )
        scope = self._resolve_context_scope(doc_ids)
        stats = self.store.chunk_corpus_stats(scope)
        if stats["total_chunks"] == 0:
            raise ContextRequestError(
                "l1_not_built", "the selected document scope has no L1 chunks"
            )

        effective_mode = requested_mode
        reason = f"requested_{requested_mode}"
        if requested_mode == "auto":
            if (
                stats["total_chars"] <= max_chars
                and stats["total_chunks"] <= max_chunks
            ):
                effective_mode = "full"
                reason = "corpus_within_budget"
            else:
                effective_mode = "search"
                reason = "corpus_exceeds_budget"
        cursor_data = _decode_context_cursor(cursor) if cursor else None
        normalized_task = (task or "").strip()
        if (
            not normalized_task
            and cursor_data is not None
            and cursor_data.get("kind") == "search"
        ):
            normalized_task = str(cursor_data.get("task") or "").strip()
        if effective_mode == "search" and not normalized_task:
            raise ContextRequestError(
                "task_required",
                "task is required when the selected corpus exceeds the full-context budget",
            )

        request_key = _context_request_key(
            effective_mode=effective_mode,
            task=normalized_task,
            doc_ids=scope,
        )
        if cursor_data is not None:
            if cursor_data.get("snapshot") != stats["snapshot"]:
                raise ContextRequestError(
                    "cursor_expired", "the L1 index changed after this cursor was issued"
                )
            if cursor_data.get("request_key") != request_key:
                raise ContextRequestError(
                    "cursor_mismatch", "cursor does not match this mode, task, or document scope"
                )

        if effective_mode == "full":
            result = self._context_full(
                scope=scope,
                stats=stats,
                max_chars=max_chars,
                max_chunks=max_chunks,
                cursor_data=cursor_data,
                request_key=request_key,
            )
        else:
            result = self._context_search(
                task=normalized_task,
                scope=scope,
                stats=stats,
                max_chars=max_chars,
                max_chunks=max_chunks,
                cursor_data=cursor_data,
                request_key=request_key,
            )

        chunks = result.pop("_chunks")
        enrichments, enrichments_truncated = self._context_enrichments(
            chunks,
            enabled=include_enrichments,
            max_chars=max_enrichment_chars,
        )
        result["selection"].update(
            {
                "requested_mode": requested_mode,
                "reason": reason,
                "enrichments_truncated": enrichments_truncated,
            }
        )
        result["enrichments"] = enrichments
        result["usage_policy"] = (
            "This is a budgeted document view, not an answer or summary. "
            "Only coverage=complete_l1 with l1_complete=true contains the whole "
            "selected L1 scope in this response. Retrieval candidates may omit "
            "relevant content; refine the task, change mode, continue the cursor, "
            "or use docgraph_search_chunks/docgraph_fetch as needed."
            " Chunks already contain complete L1 text; do not fetch every chunk. "
            "Use docgraph_fetch or docgraph_fetch_many only when L0 table cells, "
            "image/layout evidence, or source verification is actually needed."
        )
        return result

    def _resolve_context_scope(self, doc_ids: list[str] | None) -> list[str] | None:
        if doc_ids is None:
            return None
        normalized = sorted({str(doc_id).strip() for doc_id in doc_ids if str(doc_id).strip()})
        if not normalized:
            raise ContextRequestError("empty_scope", "doc_ids cannot be empty")
        available = set(self.store.list_docs())
        missing = [doc_id for doc_id in normalized if doc_id not in available]
        if missing:
            raise ContextRequestError(
                "document_not_found", f"unknown doc_ids: {', '.join(missing)}"
            )
        return normalized

    def _context_full(
        self,
        *,
        scope: list[str] | None,
        stats: dict,
        max_chars: int,
        max_chunks: int,
        cursor_data: dict | None,
        request_key: str,
    ) -> dict:
        after = None
        if cursor_data is not None:
            if cursor_data.get("kind") != "full":
                raise ContextRequestError("cursor_mismatch", "cursor is not a full-mode cursor")
            raw_after = cursor_data.get("after")
            if not isinstance(raw_after, list) or len(raw_after) != 4:
                raise ContextRequestError("invalid_cursor", "full-mode cursor is malformed")
            after = (str(raw_after[0]), int(raw_after[1]), int(raw_after[2]), str(raw_after[3]))

        candidates = self.store.list_chunks_page(
            doc_ids=scope,
            after=after,
            limit=max_chunks + 1,
        )
        selected = []
        returned_chars = 0
        for chunk in candidates:
            if len(selected) >= max_chunks or returned_chars + len(chunk.text) > max_chars:
                break
            selected.append(chunk)
            returned_chars += len(chunk.text)
        if candidates and not selected:
            raise ContextRequestError(
                "budget_too_small",
                f"max_chars={max_chars} cannot fit the next chunk ({len(candidates[0].text)} chars)",
            )
        has_more = len(candidates) > len(selected)
        next_cursor = None
        if has_more and selected:
            next_cursor = _encode_context_cursor(
                {
                    "v": _CONTEXT_CURSOR_VERSION,
                    "kind": "full",
                    "snapshot": stats["snapshot"],
                    "request_key": request_key,
                    "after": list(_chunk_sort_key(selected[-1])),
                }
            )
        complete_in_response = after is None and not has_more and len(selected) == stats["total_chunks"]
        selection = {
            **_context_stats(stats),
            "mode": "full",
            "coverage": "complete_l1" if complete_in_response else "paginated_l1",
            "l1_complete": complete_in_response,
            "returned_chunks": len(selected),
            "returned_chars": returned_chars,
            "candidate_chunks": stats["total_chunks"],
            "unreturned_candidates": max(stats["total_chunks"] - len(selected), 0),
            "corpus_chunks_not_returned": max(stats["total_chunks"] - len(selected), 0),
            "retrieval_methods": [],
            "candidate_pool_truncated": False,
            "truncated": has_more,
            "next_cursor": next_cursor,
        }
        return {
            "selection": selection,
            "chunks": [_chunk_view(chunk) for chunk in selected],
            "_chunks": selected,
        }

    def _context_search(
        self,
        *,
        task: str,
        scope: list[str] | None,
        stats: dict,
        max_chars: int,
        max_chunks: int,
        cursor_data: dict | None,
        request_key: str,
    ) -> dict:
        offset = 0
        if cursor_data is not None:
            if cursor_data.get("kind") != "search":
                raise ContextRequestError("cursor_mismatch", "cursor is not a search-mode cursor")
            offset = int(cursor_data.get("offset", 0))
            if offset < 0:
                raise ContextRequestError("invalid_cursor", "search cursor offset is invalid")

        search = self.search_chunks_with_meta(
            task,
            limit=_CONTEXT_CANDIDATE_LIMIT,
            doc_ids=scope,
            candidate_limit=_CONTEXT_CANDIDATE_LIMIT,
        )
        candidates = search["hits"]
        selected_chunks = []
        selected_hits = []
        returned_chars = 0
        response_chunk_limit = min(max_chunks, _CONTEXT_SEARCH_PAGE_CHUNKS)
        for hit in candidates[offset:]:
            if len(selected_chunks) >= response_chunk_limit:
                break
            chunk = self.store.get_chunk(hit["chunk_id"])
            if chunk is None:
                continue
            if returned_chars + len(chunk.text) > max_chars:
                break
            selected_chunks.append(chunk)
            selected_hits.append(hit)
            returned_chars += len(chunk.text)
        if candidates[offset:] and not selected_chunks:
            first = self.store.get_chunk(candidates[offset]["chunk_id"])
            size = len(first.text) if first is not None else 0
            raise ContextRequestError(
                "budget_too_small",
                f"max_chars={max_chars} cannot fit the next candidate chunk ({size} chars)",
            )

        consumed = offset + len(selected_hits)
        unreturned_candidates = max(len(candidates) - consumed, 0)
        next_cursor = None
        if unreturned_candidates:
            next_cursor = _encode_context_cursor(
                {
                    "v": _CONTEXT_CURSOR_VERSION,
                    "kind": "search",
                    "snapshot": stats["snapshot"],
                    "request_key": request_key,
                    "offset": consumed,
                    "task": task,
                }
            )
        views = []
        for chunk, hit in zip(selected_chunks, selected_hits, strict=True):
            view = _chunk_view(chunk)
            view["score"] = hit["score"]
            view["rank_reasons"] = hit["rank_reasons"]
            views.append(view)
        selection = {
            **_context_stats(stats),
            "mode": "search",
            "coverage": "retrieval_candidates",
            "l1_complete": False,
            "returned_chunks": len(selected_chunks),
            "returned_chars": returned_chars,
            "response_chunk_limit": response_chunk_limit,
            "candidate_chunks": len(candidates),
            "unreturned_candidates": unreturned_candidates,
            "corpus_chunks_not_returned": max(stats["total_chunks"] - len(selected_chunks), 0),
            "retrieval_methods": search["methods"],
            "candidate_pool_truncated": search["candidate_pool_truncated"],
            "truncated": bool(unreturned_candidates or search["candidate_pool_truncated"]),
            "next_cursor": next_cursor,
        }
        return {"selection": selection, "chunks": views, "_chunks": selected_chunks}

    def _context_enrichments(
        self,
        chunks: list,
        *,
        enabled: bool,
        max_chars: int,
    ) -> tuple[list[dict], bool]:
        if not enabled or max_chars == 0:
            return [], False
        nodes: dict[str, Node] = {}
        for chunk in chunks:
            for node in self.store.get_entities_for_chunk(chunk.id):
                nodes.setdefault(node.id, node)
            for block in self.store.get_blocks(chunk.block_ids):
                for node in self.store.get_entities_for_block(block.id):
                    nodes.setdefault(node.id, node)
        enrichments: list[dict] = []
        used = 0
        truncated = False
        for node in sorted(nodes.values(), key=lambda item: item.id):
            item = _entity_enrichment(node)
            size = len(json.dumps(item, ensure_ascii=False, default=str))
            if used + size > max_chars:
                truncated = True
                continue
            enrichments.append(item)
            used += size
        return enrichments, truncated

    def context_with_blocks(self, task: str, max_nodes: int = 20) -> dict:
        """Evidence-first context for agents.

        L2 graph nodes are useful candidates, but agent answers should be grounded
        in L1 chunks and original L0 blocks. This method returns both in one
        bundle so MCP clients do not have to infer how to backtrace evidence.
        """
        bundle = self.context(task, max_nodes=max_nodes)

        chunks: dict[str, dict] = {}
        blocks: dict[str, dict] = {}
        nodes: list[dict] = []
        chunk_hits = self.search_chunks(task, limit=min(max_nodes, 10))

        def add_chunk(chunk_id: str) -> None:
            if chunk_id in chunks:
                return
            chunk = self.store.get_chunk(chunk_id)
            if chunk is None:
                return
            chunks[chunk_id] = {
                "id": chunk.id,
                "kind": chunk.kind,
                "doc_id": chunk.doc_id,
                "page": chunk.page,
                "page_start": chunk.page_start or chunk.page,
                "page_end": chunk.page_end or chunk.page,
                "section_id": chunk.section_id,
                "section_node_id": chunk.section_node_id,
                "text": (chunk.text or "")[:2000],
                "block_ids": chunk.block_ids,
                "attrs": chunk.attrs,
            }
            for block in self.store.get_blocks(chunk.block_ids[:8]):
                blocks.setdefault(block.id, _block_brief(block))

        def add_block_id(block_id: str) -> None:
            if block_id in blocks:
                return
            got = self.store.get_blocks([block_id])
            if got:
                blocks[block_id] = _block_brief(got[0])

        for n in bundle.nodes[:max_nodes]:
            source_block_ids = (
                n.attrs.get("source_block_ids") or n.attrs.get("block_ids") or []
            )
            source_chunk_ids = (
                n.attrs.get("source_chunk_ids") or n.evidence.chunk_ids or []
            )
            n_info = {
                "id": n.id,
                "kind": n.kind.value,
                "name": n.name,
                "qualified_name": n.qualified_name,
                "doc_id": n.doc_id,
                "page": n.location.page,
                "summary": n.summary,
                "source": n.attrs.get("source") or n.evidence.extractor,
                "extraction_confidence": n.attrs.get("extraction_confidence"),
                "needs_source_check": _needs_source_check(n),
                "source_block_ids": source_block_ids,
                "source_chunk_ids": source_chunk_ids,
            }
            nodes.append(n_info)
            for chunk_id in source_chunk_ids[:4]:
                add_chunk(chunk_id)
            for block_id in source_block_ids[:8]:
                add_block_id(block_id)

        # Semantic hits may be node vectors or chunk vectors depending on the
        # configured store; add chunks opportunistically when the ID resolves.
        for hit in chunk_hits:
            add_chunk(hit["chunk_id"])

        for hit in bundle.semantic_hits[:3]:
            chunk_id = hit.get("id") or hit.get("chunk_id")
            if not chunk_id:
                continue
            add_chunk(chunk_id)

        return {
            "task": task,
            "usage_policy": (
                "Treat L2 nodes and edges as candidates. Ground final answers in "
                "the returned L1 chunks / L0 blocks; verify nodes marked "
                "needs_source_check before using them as facts."
            ),
            "nodes": nodes,
            "edges": [e.model_dump() for e in bundle.edges],
            "chunk_hits": chunk_hits,
            "semantic_hits": bundle.semantic_hits,
            "providers": bundle.providers,
            "chunks": list(chunks.values()),
            "blocks": list(blocks.values()),
        }

    def search_chunks(
        self,
        query: str,
        limit: int = 20,
        doc_ids: list[str] | None = None,
    ) -> list[dict]:
        """混合检索 L1 chunk。

        FTS5/LIKE 是确定性入口；若已配置 encoder 且 chunk 向量存在，则补充
        semantic 候选。两路候选统一交给 `_score_chunk_hit` 排序。
        """
        return self.search_chunks_with_meta(
            query,
            limit=limit,
            doc_ids=doc_ids,
            candidate_limit=max(limit * 3, limit),
        )["hits"]

    def search_chunks_with_meta(
        self,
        query: str,
        *,
        limit: int = 20,
        doc_ids: list[str] | None = None,
        candidate_limit: int = 60,
    ) -> dict:
        """Hybrid L1 search with transparent retrieval metadata."""
        candidate_limit = max(limit, candidate_limit)
        scope = set(doc_ids) if doc_ids is not None else None
        candidates: dict[str, dict] = {}
        retrieval_queries = [query, *_retrieval_terms(query)]
        retrieval_queries = list(dict.fromkeys(item for item in retrieval_queries if item))
        per_term_limit = max(20, candidate_limit // max(len(retrieval_queries), 1))
        text_methods: set[str] = set()
        text_pool_truncated = False
        for index, retrieval_query in enumerate(retrieval_queries):
            text_result = self.store.search_chunks_text(
                retrieval_query,
                limit=candidate_limit if index == 0 else per_term_limit,
                doc_ids=doc_ids,
            )
            text_methods.update(text_result["methods"])
            text_pool_truncated = text_pool_truncated or text_result["pool_truncated"]
            for cid, snip in text_result["hits"]:
                candidates.setdefault(
                    cid,
                    {"snippet": snip or "", "semantic_score": None},
                )
        semantic_hits = self._semantic_chunk_search_raw(query, top_k=candidate_limit)
        for cid, semantic_score in semantic_hits:
            candidates.setdefault(cid, {"snippet": "", "semantic_score": semantic_score})
            candidates[cid]["semantic_score"] = semantic_score

        ranked: list[tuple[float, dict]] = []
        for cid, meta in candidates.items():
            c = self.store.get_chunk(cid)
            if c is None:
                continue
            if scope is not None and c.doc_id not in scope:
                continue
            snip = meta.get("snippet") or ""
            score, reasons = _score_chunk_hit(query, c, snip)
            semantic_score = meta.get("semantic_score")
            if semantic_score is not None:
                score += max(0.0, float(semantic_score)) * 2.0
                reasons.append(f"semantic:{float(semantic_score):.3f}")
                if not snip:
                    snip = (c.text or "")[:240]
            ranked.append((score, {
                "chunk_id": c.id, "doc_id": c.doc_id, "kind": c.kind, "page": c.page,
                "page_start": c.page_start or c.page,
                "page_end": c.page_end or c.page,
                "section_id": c.section_id,
                "section_node_id": c.section_node_id,
                "snippet": snip or "",
                "block_ids": c.block_ids,
                "score": round(score, 3),
                "rank_reasons": reasons,
            }))
        ranked.sort(key=lambda item: item[0], reverse=True)
        pool_truncated = (
            text_pool_truncated
            or len(semantic_hits) >= candidate_limit
            or len(ranked) > candidate_limit
        )
        ranked = ranked[:candidate_limit]
        methods = sorted(text_methods)
        if semantic_hits:
            methods.append("semantic")
        return {
            "hits": [item for _, item in ranked[:limit]],
            "methods": methods,
            "candidate_chunks": len(ranked),
            "candidate_pool_truncated": pool_truncated,
        }

    def _semantic_chunk_search_raw(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        if self.vstore is None or self.encoder is None:
            return []
        try:
            vec = self.encoder.encode([query])[0]
            return self.vstore.search_items("chunk", vec, self.encoder.model, top_k=top_k)
        except Exception:
            return []


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _bounded_int(name: str, value: int, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ContextRequestError("invalid_budget", f"{name} must be an integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ContextRequestError("invalid_budget", f"{name} must be an integer") from exc
    if normalized < minimum or normalized > maximum:
        raise ContextRequestError(
            "invalid_budget", f"{name} must be between {minimum} and {maximum}"
        )
    return normalized


def _context_request_key(
    *,
    effective_mode: str,
    task: str,
    doc_ids: list[str] | None,
) -> str:
    raw = json.dumps(
        {"mode": effective_mode, "task": task, "doc_ids": doc_ids},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _encode_context_cursor(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_context_cursor(cursor: str) -> dict:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode((cursor + padding).encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContextRequestError("invalid_cursor", "cursor is malformed") from exc
    if not isinstance(data, dict) or data.get("v") != _CONTEXT_CURSOR_VERSION:
        raise ContextRequestError("invalid_cursor", "cursor version is unsupported")
    return data


def _chunk_sort_key(chunk) -> tuple[str, int, int, str]:
    page_start = chunk.page_start if chunk.page_start is not None else chunk.page or 0
    page_end = chunk.page_end if chunk.page_end is not None else chunk.page or 0
    return chunk.doc_id, int(page_start), int(page_end), chunk.id


def _chunk_view(chunk) -> dict:
    return {
        "id": chunk.id,
        "doc_id": chunk.doc_id,
        "kind": chunk.kind,
        "page": chunk.page,
        "page_start": chunk.page_start if chunk.page_start is not None else chunk.page,
        "page_end": chunk.page_end if chunk.page_end is not None else chunk.page,
        "section_id": chunk.section_id,
        "section_node_id": chunk.section_node_id,
        "text": chunk.text,
        "block_ids": chunk.block_ids,
        "attrs": chunk.attrs,
    }


def _context_stats(stats: dict) -> dict:
    return {
        "total_docs": stats["total_docs"],
        "total_chunks": stats["total_chunks"],
        "total_chars": stats["total_chars"],
    }


def _entity_enrichment(node: Node) -> dict:
    item = _entity_summary(node)
    item.update(
        {
            "doc_id": node.doc_id,
            "attrs": node.attrs,
            "evidence": node.evidence.model_dump(mode="json"),
        }
    )
    return item


def _tokenize_task(task: str) -> list[str]:
    import re
    # 大写下划线的标识符容易是 register/pin 名
    tokens = re.findall(r"[A-Z][A-Z0-9_]{2,}", task)
    return tokens[:6]


def _retrieval_terms(query: str, limit: int = 12) -> list[str]:
    """Extract useful lexical probes from an agent's natural-language task."""
    import re

    stopwords = {
        "all",
        "and",
        "are",
        "containing",
        "description",
        "descriptions",
        "find",
        "for",
        "from",
        "have",
        "into",
        "modeling",
        "need",
        "that",
        "the",
        "their",
        "this",
        "value",
        "values",
        "with",
    }
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_]{1,}|[\u3400-\u9fff]{2,}", query):
        normalized = token.lower()
        if normalized in stopwords or normalized.isdigit():
            continue
        if normalized not in terms:
            terms.append(normalized)
        if len(terms) >= limit:
            break
    return terms


def _score_chunk_hit(query: str, chunk, snippet: str) -> tuple[float, list[str]]:
    import re
    q = query.strip().lower()
    text = (chunk.text or "").lower()
    snippet_l = (snippet or "").lower()
    attrs = chunk.attrs or {}
    table_profile = attrs.get("table_profile") or {}
    reasons: list[str] = []
    score = 0.0
    terms = _retrieval_terms(query)

    matched_terms = [term for term in terms if term in text]
    if matched_terms:
        score += min(len(matched_terms) * 0.45, 4.5)
        reasons.append(f"term-overlap:{len(matched_terms)}")

    if q and q in text:
        score += 1.0
        reasons.append("text")
    if q and q in snippet_l:
        score += 0.4
        reasons.append("snippet")

    first_line = text.splitlines()[0] if text else ""
    heading_matches = sum(1 for term in terms if term in first_line)
    if heading_matches:
        score += min(heading_matches * 0.6, 2.4)
        reasons.append(f"heading-terms:{heading_matches}")
    if q and q in first_line:
        score += 3.0
        reasons.append("heading")
    if re.search(r"^\s*\d+(?:\.\d+){0,5}\s*" + re.escape(q), first_line, re.I):
        score += 2.0
        reasons.append("section-title")

    if chunk.section_id:
        score += 0.5
        reasons.append("section")
    if chunk.page_start and chunk.page_end and chunk.page_end != chunk.page_start:
        score += 0.2
        reasons.append("span")

    if chunk.kind in {"table", "logical_table"} or chunk.chunk_type in {"table", "logical_table"}:
        caption = str(table_profile.get("caption") or "").lower()
        headers = " ".join(str(h).lower() for h in table_profile.get("headers") or [])
        header_matches = sum(1 for term in terms if term in headers)
        if header_matches:
            score += min(header_matches * 0.8, 4.0)
            reasons.append(f"table-header-terms:{header_matches}")
        if q and q in caption:
            score += 2.0
            reasons.append("caption")
        if q and q in headers:
            score += 1.4
            reasons.append("table-header")
        if table_profile.get("kind") and table_profile.get("kind") != "generic_table":
            score += 0.6
            reasons.append(str(table_profile.get("kind")))
        if chunk.chunk_type == "logical_table":
            score += 0.8
            reasons.append("logical-table")

    front_matter = {"document history", "contents", "目录", "version"}
    if any(x in first_line for x in front_matter) or not chunk.section_id:
        score -= 1.0
        reasons.append("front-matter-penalty")

    return score, reasons


def _entity_summary(node: Node) -> dict:
    """Lightweight entity summary for embedding in fetch/search_chunks results.

    Includes source_quality so the agent can decide whether to trust the
    extraction or verify against L0/L1 source blocks.
    """
    source_block_ids = node.attrs.get("source_block_ids") or node.attrs.get("block_ids") or []
    source_chunk_ids = node.attrs.get("source_chunk_ids") or node.evidence.chunk_ids or []
    return {
        "id": node.id,
        "kind": node.kind.value,
        "name": node.name,
        "qualified_name": node.qualified_name,
        "page": node.location.page,
        "summary": node.summary,
        "source_chunk_ids": source_chunk_ids,
        "source_block_ids": source_block_ids,
        "source_quality": {
            "source": node.attrs.get("source") or node.evidence.extractor,
            "extraction_confidence": node.attrs.get("extraction_confidence"),
            "needs_source_check": _needs_source_check(node),
        },
    }


def _needs_source_check(node: Node) -> bool:
    source = str(node.attrs.get("source") or node.evidence.extractor or "").lower()
    confidence = str(node.attrs.get("extraction_confidence") or "").lower()
    if confidence in {"deterministic", "verified"}:
        return False
    # VLM/LLM/figure extraction always needs L0/L1 verification
    if any(tag in source for tag in ("figure@", "vlm", "llm")):
        return True
    # Missing both confidence and source → unknown origin, flag for safety
    if not confidence and not source:
        return True
    # Table normalizer / regex extractor without explicit confidence:
    # trust by default (deterministic extraction from structured data)
    return False


def _block_brief(block: Block) -> dict:
    return {
        "id": block.id,
        "doc_id": block.doc_id,
        "page": block.page,
        "kind": block.kind.value,
        "text": (block.text or "")[:2000] if block.text else None,
        "table": block.table.model_dump() if block.table else None,
        "image_path": block.image_path,
        "section_path": block.section_path,
        "attrs": block.attrs,
    }


def _block_view(block: Block) -> dict:
    return {
        "id": block.id,
        "doc_id": block.doc_id,
        "page": block.page,
        "kind": block.kind.value,
        "text": block.text,
        "table": block.table.model_dump() if block.table else None,
        "image_path": block.image_path,
        "section_path": block.section_path,
        "attrs": block.attrs,
    }
