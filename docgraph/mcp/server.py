"""MCP server (M2) —— 暴露 13 个 docgraph_* 工具。

stdio JSON-RPC 协议，兼容 Claude Code 等 MCP host。
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
from docgraph.query.engine import QueryEngine
from docgraph.version import __version__

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "docgraph_status",
        "description": "Return graph statistics: total nodes, edges, docs, per-kind counts.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "docgraph_files",
        "description": "List all indexed documents (doc_ids).",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "docgraph_search",
        "description": "Search L2 graph nodes by name / alias / qualified_name / fuzzy / semantic. Returns source metadata; verify uncertain hits with docgraph_sources.",
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
        "name": "docgraph_node",
        "description": "Get full detail of a node by ID, including source quality metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "docgraph_neighbors",
        "description": "Walk the graph: return neighbors of a node up to depth N.",
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
    {
        "name": "docgraph_context",
        "description": "Evidence-first primary entry: return relevant L2 candidates plus L1 chunks and L0 blocks for grounding.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "max_nodes": {"type": "integer", "default": 20},
            },
            "required": ["task"],
        },
    },
    {
        "name": "docgraph_trace",
        "description": "Find a path from one node to another (BFS).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_id": {"type": "string"},
                "to_id": {"type": "string"},
                "max_depth": {"type": "integer", "default": 5},
            },
            "required": ["from_id", "to_id"],
        },
    },
    {
        "name": "docgraph_impact",
        "description": "List nodes downstream of the given node, up to depth N.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "depth": {"type": "integer", "default": 2},
            },
            "required": ["id"],
        },
    },
    {
        "name": "docgraph_register",
        "description": "Register details + all bitfields.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "docgraph_pin",
        "description": "Pin details.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "docgraph_timing",
        "description": "Timing / electrical parameter details.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "docgraph_figure",
        "description": "Figure details (incl. mermaid/wavejson/desc if VLM ran).",
        "inputSchema": {
            "type": "object",
            "properties": {"id_or_name": {"type": "string"}},
            "required": ["id_or_name"],
        },
    },
    {
        "name": "docgraph_section",
        "description": "Section details with immediate children.",
        "inputSchema": {
            "type": "object",
            "properties": {"path_or_id": {"type": "string"}},
            "required": ["path_or_id"],
        },
    },
    {
        "name": "docgraph_glossary",
        "description": "Lookup term / acronym.",
        "inputSchema": {
            "type": "object",
            "properties": {"term": {"type": "string"}},
            "required": ["term"],
        },
    },
    # M7-P4: L0/L1 回溯接口
    {
        "name": "docgraph_blocks",
        "description": "Retrieve original L0 blocks (tables/figures/text) by their IDs for source tracing.",
        "inputSchema": {
            "type": "object",
            "properties": {"block_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["block_ids"],
        },
    },
    {
        "name": "docgraph_fetch",
        "description": "Fetch a chunk + its original L0 blocks — agent's primary path to retrieve spec source text.",
        "inputSchema": {
            "type": "object",
            "properties": {"chunk_id": {"type": "string"}},
            "required": ["chunk_id"],
        },
    },
    {
        "name": "docgraph_search_chunks",
        "description": "Full-text search across L1 chunks (FTS5 + LIKE fallback). Returns chunk IDs with snippets.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "docgraph_sources",
        "description": "Fetch source chunks and original L0 blocks for a graph node. Use this before treating L2 nodes as facts.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
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


def _tool_search(qe, args):
    q = args["query"]
    kind = NodeKind(args["kind"]) if args.get("kind") else None
    limit = args.get("limit", 20)
    nodes = qe.search(q, kind=kind, limit=limit)
    return {
        "results": [
            {
                "id": n.id,
                "kind": n.kind.value,
                "name": n.name,
                "qualified_name": n.qualified_name,
                "doc_id": n.doc_id,
                "page": n.location.page,
                "summary": n.summary,
                "source_quality": _source_quality(n),
            }
            for n in nodes
        ],
        "usage_policy": "Search returns L2 candidates. Use docgraph_sources/docgraph_fetch/docgraph_blocks to verify before answering.",
        "total": len(nodes),
    }


def _tool_node(qe, args):
    n = qe.node(args["id"])
    if n is None:
        return {"error": "not_found", "id": args["id"]}
    out = n.model_dump()
    out["source_quality"] = _source_quality(n)
    return out


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
        "usage_policy": "Graph neighbors are relation candidates. Verify important nodes/edges through source_block_ids/source_chunk_ids.",
    }


def _tool_context(qe, args):
    return qe.context_with_blocks(args["task"], max_nodes=args.get("max_nodes", 20))


def _tool_trace(qe, args):
    paths = qe.trace(
        args["from_id"], args["to_id"], max_depth=args.get("max_depth", 5)
    )
    return {"paths": [p.model_dump() for p in paths]}


def _tool_impact(qe, args):
    rep = qe.impact(args["id"], depth=args.get("depth", 2))
    if rep is None:
        return {"error": "not_found", "id": args["id"]}
    return rep.model_dump()


def _tool_register(qe, args):
    d = qe.register(args["name"])
    if d is None:
        return {"error": "not_found", "name": args["name"]}
    return {
        "register": _node_with_source_quality(d.node),
        "bitfields": [_node_with_source_quality(bf) for bf in d.bitfields],
        "usage_policy": "Register data is still grounded by source_block_ids/source_chunk_ids; fetch sources for implementation-critical decisions.",
    }


def _tool_pin(qe, args):
    d = qe.pin(args["name"])
    return _node_with_source_quality(d.node) if d else {"error": "not_found"}


def _tool_timing(qe, args):
    d = qe.timing(args["name"])
    return _node_with_source_quality(d.node) if d else {"error": "not_found"}


def _tool_figure(qe, args):
    d = qe.figure(args["id_or_name"])
    return _node_with_source_quality(d.node) if d else {"error": "not_found"}


def _tool_section(qe, args):
    d = qe.section(args["path_or_id"])
    if d is None:
        return {"error": "not_found"}
    return {
        "section": d.node.model_dump(),
        "children": [c.model_dump() for c in d.children],
    }


def _tool_glossary(qe, args):
    items = qe.glossary(args["term"])
    return {"results": [it.node.model_dump() for it in items]}


def _tool_blocks(qe, args):
    bs = qe.blocks(args.get("block_ids", []))
    return {"blocks": [b.model_dump() for b in bs]}


def _tool_fetch(qe, args):
    return qe.fetch(args["chunk_id"])


def _tool_search_chunks(qe, args):
    return {"hits": qe.search_chunks(args["query"], limit=args.get("limit", 20))}


def _tool_sources(qe, args):
    n = qe.node(args["id"])
    if n is None:
        return {"error": "not_found", "id": args["id"]}
    source_chunk_ids = n.attrs.get("source_chunk_ids") or n.evidence.chunk_ids or []
    source_block_ids = n.attrs.get("source_block_ids") or n.attrs.get("block_ids") or []
    chunks = []
    blocks_by_id = {b.id: b.model_dump() for b in qe.blocks(source_block_ids)}
    for cid in source_chunk_ids:
        fetched = qe.fetch(cid)
        chunk = fetched.get("chunk")
        if chunk:
            chunks.append(chunk)
        for block in fetched.get("blocks", []):
            blocks_by_id.setdefault(block["id"], block)
    return {
        "node": _node_with_source_quality(n),
        "chunks": chunks,
        "blocks": list(blocks_by_id.values()),
        "usage_policy": "Use these L1/L0 sources as the factual basis; the L2 node is a candidate summary.",
    }


HANDLERS = {
    "docgraph_status": _tool_status,
    "docgraph_files": _tool_files,
    "docgraph_search": _tool_search,
    "docgraph_node": _tool_node,
    "docgraph_neighbors": _tool_neighbors,
    "docgraph_context": _tool_context,
    "docgraph_trace": _tool_trace,
    "docgraph_impact": _tool_impact,
    "docgraph_register": _tool_register,
    "docgraph_pin": _tool_pin,
    "docgraph_timing": _tool_timing,
    "docgraph_figure": _tool_figure,
    "docgraph_section": _tool_section,
    "docgraph_glossary": _tool_glossary,
    "docgraph_blocks": _tool_blocks,
    "docgraph_fetch": _tool_fetch,
    "docgraph_search_chunks": _tool_search_chunks,
    "docgraph_sources": _tool_sources,
}


def _source_quality(node) -> dict:
    source = node.attrs.get("source") or node.evidence.extractor
    confidence = node.attrs.get("extraction_confidence")
    source_block_ids = node.attrs.get("source_block_ids") or node.attrs.get("block_ids") or []
    source_chunk_ids = node.attrs.get("source_chunk_ids") or node.evidence.chunk_ids or []
    needs_check = _needs_source_check(source, confidence)
    return {
        "source": source,
        "extraction_confidence": confidence,
        "needs_source_check": needs_check,
        "source_block_ids": source_block_ids,
        "source_chunk_ids": source_chunk_ids,
        "risk": "verify_with_l0_l1" if needs_check else "grounded_candidate",
    }


def _node_with_source_quality(node) -> dict:
    out = node.model_dump()
    out["source_quality"] = _source_quality(node)
    return out


def _needs_source_check(source: str | None, confidence: str | None) -> bool:
    conf = (confidence or "").lower()
    src = (source or "").lower()
    if conf in {"deterministic", "verified"}:
        return False
    return any(tag in src for tag in ("figure@", "vlm", "llm")) or not conf


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
