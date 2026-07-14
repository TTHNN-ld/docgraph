"""MCP server —— 精简工具集，按 L0/L1/L2 层次暴露数据模型。

L1 发现层：search_chunks / section
L0 原文层：fetch (chunk + blocks + entities)
L2 提示层：search (带 source_quality 标注 + 验证路径)
图谱浏览：neighbors
元信息：status / files

设计原则：
- Agent 自己决定检索策略和验证深度，工具不做预聚合或判断
- L2 实体始终带 source_quality，agent 自行决定是否信任
- 默认路径：L1 定位 → fetch 读原文(含 entities) → L2 search 加速
"""
from __future__ import annotations

import json
import sys
from typing import Any

from docgraph.core.bootstrap import bootstrap
from docgraph.core.config import docgraph_dir, load_config, project_root_from_cwd
from docgraph.core.dotenv import autoload_env
from docgraph.embeddings.factory import build_encoder
from docgraph.embeddings.vector_factory import build_vector_store
from docgraph.graph.schema import EdgeKind, NodeKind
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.query.engine import ContextRequestError, QueryEngine
from docgraph.version import __version__

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "docgraph_status",
        "description": (
            "Graph statistics: total nodes/edges, docs, per-kind counts. "
            "Use this to understand what entity types are available and "
            "how much to trust L2 extraction coverage."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "docgraph_files",
        "description": "List all indexed documents (doc_ids).",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "docgraph_search_chunks",
        "description": (
            "PRIMARY DISCOVERY TOOL. Full-text + semantic search across L1 chunks. "
            "Returns chunk IDs with snippets, page numbers, section paths, and "
            "block_ids for one-hop fetch. Use this FIRST to locate relevant "
            "sections, tables, and figures before reading original content."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
                "doc_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional document scope; omit to search the whole index.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "docgraph_fetch",
        "description": (
            "READ ORIGINAL CONTENT. Given a chunk_id from search_chunks, returns "
            "the complete chunk text, all original L0 blocks (tables/figures/text "
            "with full row data, NOT truncated), and any L2 entities that reference "
            "these chunks/blocks. Each entity includes source_quality so you can "
            "judge whether to trust it or verify against the original blocks. "
            "This is the authoritative reading path — the chunk and blocks are "
            "the ground truth (L0/L1); entities are extraction candidates (L2)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"chunk_id": {"type": "string"}},
            "required": ["chunk_id"],
        },
    },
    {
        "name": "docgraph_fetch_many",
        "description": (
            "READ MULTIPLE ORIGINAL CHUNKS. Given up to 20 chunk_ids from "
            "docgraph_context or docgraph_search_chunks, returns complete L1 chunk "
            "text, deduplicated L0 blocks, related L2 entities, and links from each "
            "chunk to its blocks/entities. Prefer this over many docgraph_fetch calls "
            "when validating broad cross-document evidence."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "chunk_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 20,
                }
            },
            "required": ["chunk_ids"],
        },
    },
    {
        "name": "docgraph_context",
        "description": (
            "DEFAULT DOCUMENT VIEW. Transparently chooses between complete L1 "
            "and L1 retrieval according to the selected corpus size and response "
            "budget. Returns original chunk text without summarizing it, reports "
            "coverage/completeness, retrieval methods, candidate counts, omitted "
            "content, rank reasons, and a continuation cursor. L2/VLM results are "
            "returned separately as enrichments. The agent remains responsible "
            "for deciding what is relevant and whether to continue or verify L0. "
            "The returned chunks already contain complete L1 text; call fetch only "
            "when table cells, image/layout evidence, or source verification is needed; "
            "use fetch_many for several chunks."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": ["auto", "full", "search"],
                    "default": "auto",
                },
                "doc_ids": {"type": "array", "items": {"type": "string"}},
                "max_chars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 80000,
                    "default": 40000,
                },
                "max_chunks": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 160,
                    "default": 80,
                },
                "include_enrichments": {"type": "boolean", "default": True},
                "max_enrichment_chars": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 20000,
                    "default": 8000,
                },
                "cursor": {
                    "type": "string",
                    "description": (
                        "Opaque continuation cursor. A search cursor retains the original "
                        "task, so follow-up calls do not need to repeat it."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "docgraph_search",
        "description": (
            "Search L2 graph entities by name/alias/kind. Returns candidates with "
            "source_quality metadata: deterministic/verified = reliable "
            "(table-based extraction); vlm/llm or needs_source_check=true = "
            "verify with the returned source_chunk_ids via docgraph_fetch "
            "before using. This is an acceleration path — skip it and go "
            "directly through search_chunks+fetch if entity coverage is low."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "kind": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "docgraph_section",
        "description": (
            "Navigate document structure. Returns a section node with its immediate "
            "child sections — useful for understanding document organization "
            "and finding tables/figures by their chapter context."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"path_or_id": {"type": "string"}},
            "required": ["path_or_id"],
        },
    },
    {
        "name": "docgraph_neighbors",
        "description": (
            "Walk the graph from a node. Returns neighboring nodes and edges "
            "up to depth N. Useful for exploring module hierarchies and signal/interface "
            "connections. Treat returned nodes as candidates — verify important ones "
            "through their source_chunk_ids."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "edge_kinds": {"type": "array", "items": {"type": "string"}},
                "depth": {"type": "integer", "default": 1},
            },
            "required": ["id"],
        },
    },
]


# ---------------------------------------------------------------------------
# Engine context
# ---------------------------------------------------------------------------


def _open_engine() -> QueryEngine:
    root = project_root_from_cwd()
    if not docgraph_dir(root).is_dir():
        raise RuntimeError("No .docgraph/ found in cwd or parents.")
    autoload_env(root)
    cfg = load_config(root)
    bootstrap()
    store = SQLiteGraphStore(docgraph_dir(root) / "graph.db")
    store.init_schema()

    vstore = None
    encoder = None
    vstore = build_vector_store(cfg.storage, docgraph_dir(root), create=False)
    if vstore is not None:
        vstore.init_schema()
        encoder = build_encoder(cfg.embeddings)
    return QueryEngine(store, vstore=vstore, encoder=encoder)


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def _tool_status(qe, args):
    return qe.status().model_dump()


def _tool_files(qe, args):
    return {"docs": qe.store.list_docs()}


def _tool_search_chunks(qe, args):
    hits = qe.search_chunks(
        args["query"],
        limit=args.get("limit", 20),
        doc_ids=args.get("doc_ids"),
    )
    return {
        "hits": hits,
        "usage_policy": (
            "These are L1 chunk candidates with snippets, page numbers, and "
            "block_ids. Use docgraph_fetch(chunk_id) to read one complete "
            "original chunk, or docgraph_fetch_many(chunk_ids) to verify several "
            "related chunks without repeating shared blocks/entities."
        ),
    }


def _tool_fetch(qe, args):
    return qe.fetch(args["chunk_id"])


def _tool_fetch_many(qe, args):
    return qe.fetch_many(args.get("chunk_ids", []))


def _tool_context(qe, args):
    return qe.document_context(
        task=args.get("task"),
        mode=args.get("mode", "auto"),
        doc_ids=args.get("doc_ids"),
        max_chars=args.get("max_chars", 40_000),
        max_chunks=args.get("max_chunks", 80),
        include_enrichments=args.get("include_enrichments", True),
        max_enrichment_chars=args.get("max_enrichment_chars", 8_000),
        cursor=args.get("cursor"),
    )


def _tool_search(qe, args):
    q = args["query"]
    kind = NodeKind(args["kind"]) if args.get("kind") else None
    limit = args.get("limit", 20)
    nodes = qe.search(q, kind=kind, limit=limit)
    results = []
    for n in nodes:
        source_block_ids = n.attrs.get("source_block_ids") or n.attrs.get("block_ids") or []
        source_chunk_ids = n.attrs.get("source_chunk_ids") or n.evidence.chunk_ids or []
        results.append({
            "id": n.id,
            "kind": n.kind.value,
            "name": n.name,
            "qualified_name": n.qualified_name,
            "doc_id": n.doc_id,
            "page": n.location.page,
            "summary": n.summary,
            "source_chunk_ids": source_chunk_ids,
            "source_block_ids": source_block_ids,
            "source_quality": {
                "source": n.attrs.get("source") or n.evidence.extractor,
                "extraction_confidence": n.attrs.get("extraction_confidence"),
                "needs_source_check": _needs_source_check(n),
            },
        })
    return {
        "results": results,
        "total": len(results),
        "usage_policy": (
            "L2 entities are extraction candidates, NOT authoritative facts. "
            "Check source_quality.needs_source_check on each result: "
            "false (deterministic) = reliable table-based extraction. "
            "true (vlm/llm/empty) = verify via docgraph_fetch(source_chunk_ids[0]) "
            "before using in engineering deliverables."
        ),
    }


def _tool_section(qe, args):
    d = qe.section(args["path_or_id"])
    if d is None:
        return {"error": "not_found"}
    return {
        "section": d.node.model_dump(),
        "children": [c.model_dump() for c in d.children],
    }


def _tool_neighbors(qe, args):
    edge_kinds = (
        [EdgeKind(k) for k in args.get("edge_kinds", [])]
        if args.get("edge_kinds")
        else None
    )
    sub = qe.neighbors(args["id"], edge_kinds=edge_kinds, depth=args.get("depth", 1))
    return {
        "nodes": [n.model_dump() for n in sub.nodes],
        "edges": [e.model_dump() for e in sub.edges],
        "usage_policy": (
            "Graph neighbors show structural/semantic relationships. "
            "Verify important nodes by checking their source_quality and "
            "following source_chunk_ids back to original text."
        ),
    }


HANDLERS = {
    "docgraph_status": _tool_status,
    "docgraph_files": _tool_files,
    "docgraph_search_chunks": _tool_search_chunks,
    "docgraph_fetch": _tool_fetch,
    "docgraph_fetch_many": _tool_fetch_many,
    "docgraph_context": _tool_context,
    "docgraph_search": _tool_search,
    "docgraph_section": _tool_section,
    "docgraph_neighbors": _tool_neighbors,
}


def _needs_source_check(node) -> bool:
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


# ---------------------------------------------------------------------------
# JSON-RPC loop
# ---------------------------------------------------------------------------


def _send(msg: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _handle_request(qe, req: dict[str, Any]) -> dict[str, Any]:
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params", {}) or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "docgraph", "version": __version__},
                "capabilities": {"tools": {}},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments", {}) or {}
        handler = HANDLERS.get(tool_name)
        if handler is None:
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }
        try:
            result = handler(qe, args)
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, ensure_ascii=False, default=str),
                        }
                    ]
                },
            }
        except ContextRequestError as e:
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {
                    "code": -32010,
                    "message": str(e),
                    "data": {"context_error": e.code},
                },
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32000, "message": str(e)},
            }

    return {
        "jsonrpc": "2.0",
        "id": rid,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def run_stdio() -> None:
    qe = _open_engine()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        _send(_handle_request(qe, req))
