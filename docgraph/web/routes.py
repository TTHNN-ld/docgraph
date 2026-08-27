"""FastAPI 路由 —— HTML 页面 + JSON API。

所有 HTML 路由用 Jinja2 模板；HTMX 局部刷新走单独的 fragment 视图；
图谱与寄存器位图前端用 d3.js 通过 /api/* 拿数据。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from docgraph.graph.schema import Chunk, EdgeKind, Node, NodeKind
from docgraph.graph.store import EdgeQuery, NodeQuery


def _nav_counts(store) -> dict[str, int]:
    """各导航 kind 的节点计数，供模板按数据有无显示/隐藏导航项。

    计算成本很低（几条 COUNT 查询），每次 render 都算。若未来图变大可缓存。
    """
    try:
        return {
            "registers": store.count_nodes(NodeKind.REGISTER),
            "pins": store.count_nodes(NodeKind.PIN),
            "timing": store.count_nodes(NodeKind.PARAMETER),
            "figures": store.count_nodes(NodeKind.FIGURE),
            "terms": store.count_nodes(NodeKind.TERM),
        }
    except Exception:
        return {}


def register_routes(app: FastAPI) -> None:
    templates_dir = Path(__file__).parent / "templates"
    tpl = Jinja2Templates(directory=str(templates_dir))

    # 全局帮助函数 / 过滤器
    tpl.env.filters["short_id"] = lambda s: s.split("::")[-1].split("#")[0]
    tpl.env.filters["short"] = lambda s, n=80: (
        (str(s)[:n] + "…") if s and len(str(s)) > n else (s or "")
    )
    tpl.env.filters["pretty_text"] = _pretty_text
    # doc_id → 可读文档名：取 family::type:: 之后的文档名部分
    tpl.env.filters["doc_name"] = lambda s: str(s).split("::", 2)[-1] if "::" in str(s) else str(s)

    def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
        """统一用 Starlette 新签名：(request, name, context)。

        自动注入 nav_counts：各导航 kind 的节点计数，供模板按数据有无
        显示/隐藏导航项（如术语在无 term 时隐藏）。
        """
        ctx = dict(context or {})
        ctx.setdefault("nav_counts", _nav_counts(app.state.store))
        return tpl.TemplateResponse(request, name, ctx, status_code=status_code)

    # ----- HTML 页面 -----

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        qe = app.state.qe
        status = qe.status()
        docs = status.docs
        return render(
            request,
            "index.html",
            {
                "status": status,
                "docs": docs,
                "project": app.state.cfg.project.model_dump(),
            },
        )

    @app.get("/registers", response_class=HTMLResponse)
    def registers_page(request: Request, q: str = "", limit: int = 100):
        store = app.state.store
        nodes = store.search_nodes(
            NodeQuery(
                kind=NodeKind.REGISTER,
                fuzzy=q or None,
                limit=limit,
            )
        )
        nodes = sorted(nodes, key=lambda n: (n.doc_id, n.location.page or 0, n.name))
        return render(request, "registers.html", {"q": q, "nodes": nodes})

    @app.get("/registers/{node_id:path}", response_class=HTMLResponse)
    def register_detail_page(request: Request, node_id: str):
        store = app.state.store
        node = store.get_node(node_id)
        if node is None or node.kind != NodeKind.REGISTER:
            return render(
                request, "not_found.html", {"id": node_id, "kind": "register"}, status_code=404
            )
        sub = store.neighbors(node_id, edge_kinds=[EdgeKind.HAS_BITFIELD], depth=1)
        bitfields = [n for n in sub.nodes if n.kind == NodeKind.BITFIELD]
        bitfields.sort(key=lambda n: -int(n.attrs.get("bit_high", 0)))
        return render(
            request,
            "register_detail.html",
            {
                "node": node,
                "bitfields": bitfields,
            },
        )

    @app.get("/pins", response_class=HTMLResponse)
    def pins_page(request: Request, q: str = "", limit: int = 200):
        store = app.state.store
        nodes = store.search_nodes(
            NodeQuery(
                kind=NodeKind.PIN,
                fuzzy=q or None,
                limit=limit,
            )
        )
        nodes = sorted(nodes, key=lambda n: (n.doc_id, n.location.page or 0, n.name))
        return render(request, "pins.html", {"q": q, "nodes": nodes})

    @app.get("/timing", response_class=HTMLResponse)
    def timing_page(request: Request, q: str = "", limit: int = 200):
        store = app.state.store
        nodes = store.search_nodes(
            NodeQuery(
                kind=NodeKind.PARAMETER,
                fuzzy=q or None,
                limit=limit,
            )
        )
        nodes = sorted(nodes, key=lambda n: (n.doc_id, n.location.page or 0, n.name))
        return render(request, "timing.html", {"q": q, "nodes": nodes})

    @app.get("/figures", response_class=HTMLResponse)
    def figures_page(request: Request, q: str = "", limit: int = 100):
        store = app.state.store
        nodes = store.search_nodes(
            NodeQuery(
                kind=NodeKind.FIGURE,
                fuzzy=q or None,
                limit=limit,
            )
        )
        # 按文档分组展示：同文档的图聚在一起，便于按文档浏览
        nodes = sorted(nodes, key=lambda n: (n.doc_id, n.location.page or 0))
        return render(request, "figures.html", {"q": q, "nodes": nodes})

    @app.get("/glossary", response_class=HTMLResponse)
    def glossary_page(request: Request, q: str = "", limit: int = 500):
        store = app.state.store
        nodes = store.search_nodes(
            NodeQuery(
                kind=NodeKind.TERM,
                fuzzy=q or None,
                limit=limit,
            )
        )
        return render(request, "glossary.html", {"q": q, "nodes": nodes})

    @app.get("/sections", response_class=HTMLResponse)
    def sections_page(request: Request):
        return render(request, "sections.html", {})

    @app.get("/search", response_class=HTMLResponse)
    def search_page(
        request: Request,
        q: str = "",
        kind: str = "",
        limit: int = 30,
    ):
        qe = app.state.qe
        nodes: list[Node] = []
        chunks: list[Chunk] = []
        if q:
            kind_enum = NodeKind(kind) if kind else None
            nodes = qe.search(q, kind=kind_enum, limit=limit)
            chunks = qe.search_chunks(q, limit=min(limit, 20)) if not kind else []
        kinds = [k.value for k in NodeKind]
        return render(
            request,
            "search.html",
            {
                "q": q,
                "kind": kind,
                "nodes": nodes,
                "chunks": chunks,
                "kinds": kinds,
            },
        )

    @app.get("/chunks/{chunk_id:path}", response_class=HTMLResponse)
    def chunk_detail_page(request: Request, chunk_id: str):
        data = app.state.qe.fetch(chunk_id)
        if data.get("error"):
            return render(
                request, "not_found.html", {"id": chunk_id, "kind": "chunk"}, status_code=404
            )
        return render(request, "chunk_detail.html", data)

    @app.get("/nodes/{node_id:path}", response_class=HTMLResponse)
    def node_detail_page(request: Request, node_id: str):
        store = app.state.store
        node = store.get_node(node_id)
        if node is None:
            return render(
                request, "not_found.html", {"id": node_id, "kind": "node"}, status_code=404
            )
        sub = store.neighbors(node_id, depth=1, limit=80)
        neighbors = [n for n in sub.nodes if n.id != node.id]
        source_block_ids = node.attrs.get("source_block_ids") or node.attrs.get("block_ids") or []
        source_chunk_ids = node.attrs.get("source_chunk_ids") or node.attrs.get("chunk_ids") or []
        blocks = store.get_blocks(source_block_ids) if source_block_ids else []
        chunks = [store.get_chunk(cid) for cid in source_chunk_ids]
        chunks = [c for c in chunks if c is not None]
        return render(
            request,
            "node_detail.html",
            {
                "node": node,
                "neighbors": neighbors,
                "edges": sub.edges,
                "blocks": blocks,
                "chunks": chunks,
            },
        )

    @app.get("/graph", response_class=HTMLResponse)
    def graph_page(request: Request, seed: str = "", depth: int = 1):
        store = app.state.store
        # 各 kind/edge_kind 计数，供过滤复选框显示"勾选会带多少节点/边"
        node_kind_counts = {}
        for k in NodeKind:
            c = store.count_nodes(k)
            if c:
                node_kind_counts[k.value] = c
        edge_kind_counts = {}
        for ek in EdgeKind:
            c = store.count_edges(ek)
            if c:
                edge_kind_counts[ek.value] = c
        return render(
            request,
            "graph.html",
            {
                "seed": seed,
                "depth": depth,
                "node_kind_counts": node_kind_counts,
                "edge_kind_counts": edge_kind_counts,
            },
        )

    @app.get("/plugins", response_class=HTMLResponse)
    def plugins_page(request: Request):
        from docgraph.core.plugins import discovered

        return render(request, "plugins.html", {"by_group": discovered()})

    # ----- JSON API -----

    @app.get("/api/status")
    def api_status():
        return app.state.qe.status().model_dump()

    @app.get("/api/sections/tree")
    def api_sections_tree() -> dict[str, Any]:
        """返回章节树 JSON：多文档先分组，再按真实章节号推父子。"""
        import re

        _NUM_PREFIX = re.compile(r"^(\d+(?:\.\d+)*)")
        store = app.state.store
        nodes = store.search_nodes(NodeQuery(kind=NodeKind.SECTION, limit=100000))

        docs: dict[str, dict] = {}
        for n in nodes:
            if _is_section_noise(n.name or "", n.location.page):
                continue
            doc = docs.setdefault(
                n.doc_id,
                {
                    "doc_id": n.doc_id,
                    "name": n.doc_id.split("::")[-1],
                    "roots": [],
                    "_by_num": {},
                    "_no_num": [],
                },
            )
            clean_name = _clean_section_name(n.name or "")
            title_num = _NUM_PREFIX.match(clean_name)
            explicit_path = n.location.section_path or n.attrs.get("path")
            if not title_num and not explicit_path and not _keep_unnumbered_section(clean_name):
                continue
            raw = title_num.group(1) if title_num else (explicit_path or clean_name)
            m = _NUM_PREFIX.match(str(raw))
            entry = {
                "id": n.id,
                "name": clean_name,
                "path": m.group(1) if m else raw,
                "raw_path": raw,
                "page": n.location.page,
                "doc_id": n.doc_id,
                "children": [],
            }
            if m:
                # Duplicate section numbers can appear in noisy TOCs; keep the
                # first navigable entry and leave the raw blocks available via L0/L1.
                key = m.group(1)
                _put_section_entry(doc, key, entry)
            elif _keep_unnumbered_section(clean_name):
                doc["_no_num"].append(entry)

        if not docs:
            for c in store.list_chunks(limit=1_000_000):
                if (c.chunk_type or c.kind) != "section" or not c.section_id:
                    continue
                first_line = (c.text or "").strip().splitlines()[0] if c.text else c.section_id
                clean_name = _clean_section_name(first_line or c.section_id)
                if _is_section_noise(clean_name, c.page_start or c.page):
                    continue
                doc = docs.setdefault(
                    c.doc_id,
                    {
                        "doc_id": c.doc_id,
                        "name": c.doc_id.split("::")[-1],
                        "roots": [],
                        "_by_num": {},
                        "_no_num": [],
                    },
                )
                raw = c.section_id
                m = _NUM_PREFIX.match(str(raw))
                if not m:
                    continue
                key = m.group(1)
                _put_section_entry(
                    doc,
                    key,
                    {
                        "id": c.id,
                        "name": clean_name,
                        "path": key,
                        "raw_path": raw,
                        "page": c.page_start or c.page,
                        "doc_id": c.doc_id,
                        "source": "l1",
                        "children": [],
                    },
                )

        for doc in docs.values():
            by_num = doc["_by_num"]
            for path, entry in by_num.items():
                clean_path = str(path).split("#", 1)[0]
                parent_path = clean_path.rsplit(".", 1)[0] if "." in clean_path else None
                if parent_path and parent_path in by_num:
                    by_num[parent_path]["children"].append(entry)
                else:
                    doc["roots"].append(entry)
            doc["roots"].extend(doc["_no_num"])
            del doc["_by_num"]
            del doc["_no_num"]

        def _sort(tree_list):
            tree_list.sort(key=lambda d: _natural_path(d["path"]))
            for d in tree_list:
                _sort(d["children"])

        for doc in docs.values():
            _sort(doc["roots"])
        ordered_docs = sorted(docs.values(), key=lambda d: d["name"])
        return {
            "docs": ordered_docs,
            "roots": [d for doc in ordered_docs for d in doc["roots"]],
            "count": sum(_count_tree(d["roots"]) for d in ordered_docs),
        }

    @app.get("/api/search")
    def api_search(
        q: str,
        kind: str | None = None,
        limit: int = 20,
    ):
        qe = app.state.qe
        kind_enum = NodeKind(kind) if kind else None
        nodes = qe.search(q, kind=kind_enum, limit=limit)
        return {
            "total": len(nodes),
            "results": [
                {
                    "id": n.id,
                    "kind": n.kind.value,
                    "name": n.name,
                    "qualified_name": n.qualified_name,
                    "doc_id": n.doc_id,
                    "page": n.location.page,
                    "summary": n.summary,
                }
                for n in nodes
            ],
        }

    @app.get("/api/chunks/{chunk_id:path}")
    def api_chunk(chunk_id: str):
        data = app.state.qe.fetch(chunk_id)
        if data.get("error"):
            return JSONResponse(data, status_code=404)
        return data

    @app.get("/api/node/{node_id:path}")
    def api_node(node_id: str):
        node = app.state.store.get_node(node_id)
        if node is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return node.model_dump()

    @app.get("/api/neighbors/{node_id:path}")
    def api_neighbors(
        node_id: str,
        depth: int = Query(1, ge=1, le=4),
        limit: int = Query(50, ge=1, le=500),
    ):
        sub = app.state.store.neighbors(node_id, depth=depth, limit=limit)
        return {
            "nodes": [n.model_dump() for n in sub.nodes],
            "edges": [e.model_dump() for e in sub.edges],
        }

    @app.get("/api/graph")
    def api_graph(
        kinds: str | None = None,
        edge_kinds: str | None = None,
        doc_id: str | None = None,
        limit: int = Query(500, ge=10, le=5000),
    ):
        """全图导出（受 limit 控制）—— d3 force graph 数据。"""
        store = app.state.store
        wanted_kinds = None
        if kinds:
            try:
                wanted_kinds = {NodeKind(kind.strip()) for kind in kinds.split(",") if kind.strip()}
            except ValueError:
                return JSONResponse({"error": "invalid_node_kind"}, status_code=400)
        selected_kinds = [kind for kind in NodeKind if wanted_kinds is None or kind in wanted_kinds]
        nodes_list: list[Node] = []
        if selected_kinds:
            # 按类型均分名额，避免 enum 靠前的高基数 kind（如 signal）挤掉其余类型。
            per_kind = max(1, limit // len(selected_kinds))
            taken: dict[NodeKind, int] = {}
            for kind in selected_kinds:
                ns = store.search_nodes(NodeQuery(kind=kind, doc_id=doc_id, limit=per_kind))
                nodes_list.extend(ns)
                taken[kind] = len(ns)
            leftover = limit - len(nodes_list)
            if leftover > 0:
                for kind in selected_kinds:
                    ns = store.search_nodes(
                        NodeQuery(
                            kind=kind,
                            doc_id=doc_id,
                            limit=leftover,
                            offset=taken.get(kind, 0),
                        )
                    )
                    if not ns:
                        continue
                    nodes_list.extend(ns)
                    leftover = limit - len(nodes_list)
                    if leftover <= 0:
                        break

        wanted_edges: list[EdgeKind] | None = None
        if edge_kinds:
            requested = {e.strip() for e in edge_kinds.split(",") if e.strip()}
            unknown = requested - {kind.value for kind in EdgeKind}
            if unknown:
                return JSONResponse(
                    {"error": "invalid_edge_kind", "values": sorted(unknown)},
                    status_code=400,
                )
            wanted_edges = [kind for kind in EdgeKind if kind.value in requested]

        node_ids = {n.id for n in nodes_list}
        edge_limit = max(1, limit * 10)
        edges = store.search_edges(
            EdgeQuery(
                node_ids=sorted(node_ids),
                kinds=wanted_edges,
                limit=edge_limit + 1,
            )
        )
        edges_truncated = len(edges) > edge_limit
        edges = edges[:edge_limit]
        edges_out = [
            {
                "src": edge.src,
                "dst": edge.dst,
                "kind": edge.kind.value,
                "confidence": edge.confidence,
            }
            for edge in edges
        ]

        return {
            "nodes": [
                {
                    "id": n.id,
                    "kind": n.kind.value,
                    "name": n.name,
                    "doc_id": n.doc_id,
                }
                for n in nodes_list
            ],
            "edges": edges_out,
            "edges_truncated": edges_truncated,
        }


def _natural_path(s: str) -> list:
    """让 '1.10' 排在 '1.9' 之后（按数字而非字符串）。"""
    out: list = []
    clean = str(s or "").split("#", 1)[0]
    for part in clean.split("."):
        try:
            out.append((0, int(part)))
        except ValueError:
            out.append((1, part))
    return out


def _clean_section_name(name: str) -> str:
    """Normalize common TOC/OCR glitches for display only."""
    import re

    s = " ".join((name or "").split())
    s = re.sub(
        r"^Chapter\s+(\d+)\s*(.*)$",
        lambda m: f"{m.group(1)} {m.group(2).strip()}".strip(),
        s,
        flags=re.I,
    )
    s = re.sub(
        r"^Appendix\s+([A-Z])\s*(.*)$",
        lambda m: f"{m.group(1).upper()} {m.group(2).strip()}".strip(),
        s,
        flags=re.I,
    )
    # "6FEATURES29" -> "6 FEATURES"; "7APPENDIX.40" -> "7 APPENDIX"
    m = re.match(r"^(\d+)([A-Za-z][A-Za-z ]+?)(?:[. ]*\d+)$", s)
    if m:
        return f"{m.group(1)} {m.group(2).strip()}"
    s = re.sub(r"(\D)\.{2,}\s*\d+$", r"\1", s)
    s = re.sub(r"(\bEL\d|[A-Za-z)])\.(\d+)$", r"\1", s)
    # "5.1System Address Map" -> "5.1 System Address Map"
    s = re.sub(r"^(\d+(?:\.\d+)+)(?=[A-Za-z\u4e00-\u9fff])", r"\1 ", s)
    # "4.5PCIe" -> "4.5 PCIe"
    s = re.sub(r"^(\d+\.\d+)(?=[A-Za-z\u4e00-\u9fff])", r"\1 ", s)
    # "6Features" -> "6 Features"
    s = re.sub(r"^(\d+)(?=[A-Za-z\u4e00-\u9fff])", r"\1 ", s)
    return s


def _pretty_text(text: str | None) -> str:
    """Display-only cleanup for parser spacing glitches."""
    import re

    s = str(text or "")
    s = re.sub(r"^(\d+(?:\.\d+)+)(?=[A-Za-z\u4e00-\u9fff])", r"\1 ", s)
    s = re.sub(r"\n(\d+(?:\.\d+)+)(?=[A-Za-z\u4e00-\u9fff])", r"\n\1 ", s)
    return s


def _keep_unnumbered_section(name: str) -> bool:
    """Section tree is navigational; unnumbered body headings stay in L0/L1."""
    import re

    return bool(re.match(r"^\d+(?:\.\d+)*\b", name or ""))


def _is_section_noise(name: str, page: int | None) -> bool:
    """Hide page furniture and TOC/list artifacts from the section-tree view."""
    import re

    s = " ".join((name or "").split()).strip()
    low = s.lower()
    if not s:
        return True
    exact_noise = {
        "目录",
        "contents",
        "table of contents",
    }
    if low in exact_noise:
        return True
    if re.fullmatch(r"version\s+\S+", low):
        return True
    if low.startswith("table ") or " list of tables" in low or "list of figures" in low:
        return True
    if low.startswith("pcie subsystem spec") or low.startswith("pcie subsystem trs"):
        return True
    if low in {"结论", "结论：", "conclusion", "conclusions"}:
        return True
    if low.startswith("req_") or "?" in s or "？" in s or s.startswith("✓"):
        return True
    if page is not None and page <= 4 and re.match(r"^\d+[A-Za-z].*\d+$", s):
        return True
    return False


def _count_tree(nodes: list[dict]) -> int:
    return sum(1 + _count_tree(n.get("children", [])) for n in nodes)


def _put_section_entry(doc: dict, key: str, entry: dict) -> None:
    current = doc["_by_num"].get(key)
    if current is None or _section_candidate_score(entry) > _section_candidate_score(current):
        doc["_by_num"][key] = entry


def _section_candidate_score(entry: dict) -> int:
    import re

    name = entry.get("name") or ""
    page = entry.get("page")
    score = 0
    if page is not None and page <= 5:
        score -= 4
    if re.search(r"\.{2,}\s*\d+$|[A-Za-z)]\.\d+$", name):
        score -= 3
    if re.match(r"^\d+(?:\.\d+)*\s+\S+", name):
        score += 2
    if re.match(r"^[A-Z](?:\.\d+)*\s+\S+", name):
        score += 2
    if re.match(r"^\d+$|^[A-Z]$", str(entry.get("path") or "")):
        score += 1
    return score
