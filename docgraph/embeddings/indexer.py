"""把节点 / L1 chunk 编码为向量并存入 VectorStore。

策略（M2）：
- 对"有意义的节点"做嵌入：register / bitfield / section / pin / parameter / term / figure
- 编码材料 = name + qualified_name + summary + 关键 attrs
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from docgraph.core.ids import content_hash
from docgraph.core.logger import get_logger
from docgraph.embeddings.base import EmbeddingProvider
from docgraph.embeddings.vector_store import VectorStore
from docgraph.graph.schema import Chunk, Node, NodeKind
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.graph.store import NodeQuery

log = get_logger(__name__)

_EMBED_KINDS = (
    NodeKind.REGISTER,
    NodeKind.BITFIELD,
    NodeKind.SECTION,
    NodeKind.PIN,
    NodeKind.PARAMETER,
    NodeKind.TERM,
    NodeKind.FIGURE,
)


@dataclass
class EmbedReport:
    nodes_embedded: int = 0
    chunks_embedded: int = 0
    duration_s: float = 0.0
    model: str = ""


def text_for_embedding(n: Node) -> str:
    parts: list[str] = [n.name]
    if n.qualified_name and n.qualified_name != n.name:
        parts.append(n.qualified_name)
    if n.summary:
        parts.append(n.summary)
    desc = n.attrs.get("description")
    if desc:
        parts.append(str(desc)[:300])
    full = n.attrs.get("full")  # glossary
    if full:
        parts.append(str(full))
    return " | ".join(parts)


def text_for_chunk_embedding(chunk: Chunk) -> str:
    """L1 chunk 的语义索引文本。

    保留章节号、页码、表格 profile 等轻量上下文，让语义搜索可以命中
    "AXI 接口信号"、"地址映射表" 这类不完全等于原文 token 的查询。
    """
    parts: list[str] = []
    if chunk.section_id:
        parts.append(f"section {chunk.section_id}")
    if chunk.page_start:
        if chunk.page_end and chunk.page_end != chunk.page_start:
            parts.append(f"pages {chunk.page_start}-{chunk.page_end}")
        else:
            parts.append(f"page {chunk.page_start}")
    attrs = chunk.attrs or {}
    table_profile = attrs.get("table_profile") or {}
    if table_profile:
        kind = table_profile.get("kind")
        if kind:
            parts.append(str(kind))
        caption = table_profile.get("caption")
        if caption:
            parts.append(str(caption))
        headers = table_profile.get("headers") or []
        if headers:
            parts.append("headers: " + " | ".join(str(h) for h in headers[:12]))
    parts.append((chunk.text or "")[:4000])
    return "\n".join(p for p in parts if p)


def embed_graph(
    store: SQLiteGraphStore,
    vstore: VectorStore,
    encoder: EmbeddingProvider,
    *,
    batch: int = 64,
    only_missing: bool = True,
) -> EmbedReport:
    t0 = time.time()
    vstore.init_schema()
    stored_hashes = vstore.stored_node_hashes(encoder.model) if only_missing else {}

    n_embedded = 0
    current_node_ids: set[str] = set()
    for kind in _EMBED_KINDS:
        nodes = store.search_nodes(NodeQuery(kind=kind, limit=100000))
        current_node_ids.update(n.id for n in nodes)
        desired_hashes = {
            n.id: content_hash(text_for_embedding(n))
            for n in nodes
        }
        nodes = [
            n for n in nodes
            if not only_missing or stored_hashes.get(n.id) != desired_hashes[n.id]
        ]
        for i in range(0, len(nodes), batch):
            chunk = nodes[i : i + batch]
            texts = [text_for_embedding(n) for n in chunk]
            vecs = encoder.encode(texts)
            for n, v in zip(chunk, vecs, strict=False):
                vstore.upsert(
                    n.id,
                    encoder.model,
                    v,
                    content_hash=desired_hashes[n.id],
                )
                n_embedded += 1

    n_chunks = embed_chunks(store, vstore, encoder, batch=batch, only_missing=only_missing)
    current_chunk_ids = {c.id for c in store.list_chunks(limit=1_000_000)}
    vstore.prune(current_node_ids, "chunk", current_chunk_ids)

    rep = EmbedReport(
        nodes_embedded=n_embedded,
        chunks_embedded=n_chunks,
        duration_s=round(time.time() - t0, 2),
        model=encoder.model,
    )
    log.info(
        f"[embed] {rep.nodes_embedded} nodes, {rep.chunks_embedded} chunks "
        f"embedded with {rep.model} "
        f"({rep.duration_s}s)"
    )
    return rep


def embed_chunks(
    store: SQLiteGraphStore,
    vstore: VectorStore,
    encoder: EmbeddingProvider,
    *,
    batch: int = 64,
    only_missing: bool = True,
) -> int:
    """给 L1 chunks 建语义索引。

    namespace 固定为 `chunk`；后续如果接段落、图片语义、外部文档片段，
    仍然走同一套 VectorStore item 接口。
    """
    stored_hashes = (
        vstore.stored_item_hashes("chunk", encoder.model) if only_missing else {}
    )
    all_chunks = store.list_chunks(limit=1_000_000)
    desired_hashes = {
        chunk.id: content_hash(text_for_chunk_embedding(chunk))
        for chunk in all_chunks
    }
    chunks = [
        chunk for chunk in all_chunks
        if not only_missing or stored_hashes.get(chunk.id) != desired_hashes[chunk.id]
    ]
    n_embedded = 0
    for i in range(0, len(chunks), batch):
        group = chunks[i : i + batch]
        texts = [text_for_chunk_embedding(c) for c in group]
        vecs = encoder.encode(texts)
        for chunk, vec in zip(group, vecs, strict=False):
            vstore.upsert_item(
                "chunk",
                chunk.id,
                encoder.model,
                vec,
                content_hash=desired_hashes[chunk.id],
            )
            n_embedded += 1
    return n_embedded
