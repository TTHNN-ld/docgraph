"""Typer CLI —— docgraph init / build / status / query / register / watch / serve ..."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from docgraph.core.bootstrap import bootstrap
from docgraph.core.config import (
    DEFAULT_CONFIG_YAML,
    config_path,
    docgraph_dir,
    load_config,
    project_root_from_cwd,
)
from docgraph.core.dotenv import autoload_env
from docgraph.core.logger import get_logger, set_level
from docgraph.core.manifest import load_manifest
from docgraph.core.pipeline import build as run_build
from docgraph.embeddings.vector_factory import build_vector_store
from docgraph.graph.schema import NodeKind
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.quality.layers import audit_l0_l1
from docgraph.quality.l2 import audit_l2_candidates
from docgraph.query.engine import QueryEngine
from docgraph.version import __version__
from docgraph.watcher import run_watch_loop

app = typer.Typer(
    name="docgraph",
    help="Document Knowledge Graph for chip specs — like codegraph for docs.",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()
log = get_logger("docgraph.cli")


def _open_project() -> tuple[Path, SQLiteGraphStore, QueryEngine]:
    root = project_root_from_cwd()
    if not docgraph_dir(root).is_dir():
        console.print("[red]No .docgraph/ found.[/red] Run docgraph init first.")
        raise typer.Exit(code=1)
    autoload_env(root)  # 在加载 config 前先把 .env 灌入环境
    cfg = load_config(root)
    set_level(cfg.logging.level)
    bootstrap()
    store = SQLiteGraphStore(docgraph_dir(root) / "graph.db")
    store.init_schema()
    vstore = build_vector_store(cfg.storage, docgraph_dir(root), create=False)
    encoder = None
    if vstore:
        vstore.init_schema()
        # 与 pipeline 一致：从 config 解析 encoder
        from docgraph.embeddings.factory import build_encoder
        encoder = build_encoder(cfg.embeddings)
    qe = QueryEngine(store, vstore=vstore, encoder=encoder)
    return root, store, qe


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@app.command()
def init(
    name: str = typer.Option(None, help="Project name"),
    family: str = typer.Option(None, help="Chip family (e.g. stm32f407)"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing config"),
) -> None:
    root = Path.cwd()
    dg = docgraph_dir(root)
    dg.mkdir(parents=True, exist_ok=True)
    cfg_path = config_path(root)
    if cfg_path.exists() and not force:
        console.print(f"[yellow]Config already exists:[/yellow] {cfg_path}")
    else:
        content = DEFAULT_CONFIG_YAML
        if name:
            content = content.replace("name: my-chip-spec", f"name: {name}")
        if family:
            content = content.replace("family: default", f"family: {family}")
        cfg_path.write_text(content, encoding="utf-8")
        console.print(f"[green]Created[/green] {cfg_path}")
    for sub in ("cache", "entities", "logs"):
        (dg / sub).mkdir(exist_ok=True)
    # 初始化存储
    bootstrap()
    store = SQLiteGraphStore(dg / "graph.db")
    store.init_schema()
    store.close()
    console.print(f"[bold green]DocGraph initialized[/bold green] at {dg}")


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


@app.command()
def build(
    doc: Path = typer.Option(None, "--doc", help="Only build a specific file"),
    force: bool = typer.Option(False, "--force", help="Ignore manifest cache"),
    quality: str | None = typer.Option(
        None,
        "--quality",
        help="Parser quality profile: fast, balanced, or accurate",
    ),
) -> None:
    root, store, _qe = _open_project()
    cfg = load_config(root)
    manifest = load_manifest(root)
    r = run_build(root, cfg, store, manifest, force=force, file_filter=doc, quality=quality)
    store.close()
    table = Table(title="Build summary", show_header=True, header_style="bold")
    for col in ("metric", "value"):
        table.add_column(col)
    for row in [
        ("files", str(r.total_files)),
        ("quality", r.quality),
        ("parsed", str(r.parsed)),
        ("skipped", str(r.skipped)),
        ("errors", str(r.errors)),
        ("nodes_added", str(r.nodes_total)),
        ("edges_added", str(r.edges_total)),
        ("blocks_added", str(r.blocks_total)),
        ("chunks_added", str(r.chunks_total)),
        ("linker_edges", str(r.linker_edges)),
        ("embedded_nodes", str(r.embedded_nodes)),
        ("embedded_chunks", str(r.embedded_chunks)),
        ("llm_calls", str(r.llm_calls)),
        ("llm_cost_usd", f"${r.llm_cost_usd:.4f}"),
        ("duration_s", str(r.duration_s)),
    ]:
        table.add_row(row[0], row[1])
    console.print(table)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command()
def status(
    cost: bool = typer.Option(False, "--cost", help="Show cost info"),
) -> None:
    root, store, qe = _open_project()
    s = qe.status()
    store.close()
    console.print(f"[bold]Nodes:[/bold] {s.nodes_total}    [bold]Edges:[/bold] {s.edges_total}")
    console.print(f"[bold]Documents:[/bold] {len(s.docs)}")
    for d in s.docs:
        console.print(f"  · {d}")
    if s.vector_count:
        console.print(f"[bold]Vector entries:[/bold] {s.vector_count}")
    if s.by_kind:
        tbl = Table(title="By kind", show_header=True, header_style="bold")
        tbl.add_column("kind")
        tbl.add_column("count", justify="right")
        for k, c in sorted(s.by_kind.items(), key=lambda kv: -kv[1]):
            tbl.add_row(k, str(c))
        console.print(tbl)
    if s.by_edge_kind:
        tbl = Table(title="By edge kind", show_header=True, header_style="bold")
        tbl.add_column("kind")
        tbl.add_column("count", justify="right")
        for k, c in sorted(s.by_edge_kind.items(), key=lambda kv: -kv[1]):
            tbl.add_row(k, str(c))
        console.print(tbl)
    if cost:
        m = load_manifest(root)
        total_cost = 0.0
        for rec in m.files.values():
            for sr in rec.stage_log.values():
                total_cost += getattr(sr, "cost_usd", 0.0)
        console.print(f"[bold]Estimated total cost:[/bold] ${total_cost:.4f}")


@app.command("doctor")
def doctor(
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON"),
    strict: bool = typer.Option(False, "--strict", help="Return non-zero on warnings too"),
) -> None:
    """Validate DocGraph health. Currently focused on L0/L1 production invariants."""
    _run_layer_doctor(json_output=json_output, strict=strict)


@app.command("l2-audit")
def l2_audit(
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON"),
    schema: list[str] = typer.Option(None, "--schema", help="Limit to one or more L2 schemas"),
    strict: bool = typer.Option(False, "--strict", help="Return non-zero on warnings too"),
) -> None:
    """Audit L2 candidate coverage without calling LLM/VLM."""
    import json

    _root, store, _qe = _open_project()
    report = audit_l2_candidates(store, schema_names=schema or None)
    store.close()
    if json_output:
        console.print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        totals = report.totals
        console.print("[bold]L2 candidate audit[/bold] " + ("[green]OK[/green]" if report.ok else "[red]FAILED[/red]"))
        table = Table(show_header=True, header_style="bold")
        table.add_column("metric")
        table.add_column("value", justify="right")
        for key in (
            "docs", "chunks", "table_chunks", "text_chunks", "figure_chunks",
            "candidates_total", "table_candidates", "text_candidates",
            "figure_candidates", "table_schema_hits", "text_schema_hits",
            "schemas_with_candidates", "l2_nodes",
        ):
            table.add_row(key, str(totals.get(key, 0)))
        console.print(table)

        schema_table = Table(title="Schema candidates", show_header=True, header_style="bold")
        for col in ("schema", "table_seen", "table_hit", "text_seen", "text_hit", "docs", "l2_nodes"):
            schema_table.add_column(col)
        for row in report.by_schema:
            schema_table.add_row(
                row["schema"],
                str(row["table_candidates_seen"]),
                str(row["table_candidates_matched"]),
                str(row["text_candidates_seen"]),
                str(row["text_candidates_matched"]),
                str(row["candidate_doc_count"]),
                str(row["l2_nodes"]),
            )
        console.print(schema_table)

        if report.issues:
            issue_table = Table(title="Issues", show_header=True, header_style="bold")
            for col in ("severity", "code", "doc", "message", "sample"):
                issue_table.add_column(col)
            for issue in report.issues:
                issue_table.add_row(
                    issue.severity,
                    issue.code,
                    issue.doc_id or "",
                    issue.message,
                    ", ".join(issue.sample_ids[:3]),
                )
            console.print(issue_table)
    has_errors = any(i.severity == "error" for i in report.issues)
    has_warnings = any(i.severity == "warning" for i in report.issues)
    if has_errors or (strict and has_warnings):
        raise typer.Exit(code=1)


def _run_layer_doctor(*, json_output: bool, strict: bool) -> None:
    import json

    _root, store, _qe = _open_project()
    report = audit_l0_l1(store)
    store.close()
    if json_output:
        console.print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        totals = report.totals
        console.print("[bold]Layer quality[/bold] " + ("[green]OK[/green]" if report.ok else "[red]FAILED[/red]"))
        table = Table(show_header=True, header_style="bold")
        table.add_column("metric")
        table.add_column("value", justify="right")
        for key in (
            "docs", "blocks", "chunks", "chunks_fts", "tables",
            "tables_with_cells", "tables_with_evidence",
            "figures", "figures_with_image", "figures_with_evidence",
            "chunks_with_block_ids", "chunks_with_section_id",
            "chunks_with_section_node_id", "multi_page_chunks",
            "l2_nodes", "l2_nodes_with_source_blocks",
            "l2_nodes_with_source_chunks", "l2_nodes_with_evidence",
        ):
            table.add_row(key, str(totals.get(key, 0)))
        console.print(table)
        if report.issues:
            issue_table = Table(title="Issues", show_header=True, header_style="bold")
            for col in ("severity", "code", "doc", "message", "sample"):
                issue_table.add_column(col)
            for issue in report.issues:
                issue_table.add_row(
                    issue.severity,
                    issue.code,
                    issue.doc_id or "",
                    issue.message,
                    ", ".join(issue.sample_ids[:3]),
                )
            console.print(issue_table)
    has_errors = any(i.severity == "error" for i in report.issues)
    has_warnings = any(i.severity == "warning" for i in report.issues)
    if has_errors or (strict and has_warnings):
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# search / query
# ---------------------------------------------------------------------------


@app.command()
def search(
    query: str,
    kind: str = typer.Option(None, help="Filter by node kind"),
    limit: int = typer.Option(20, help="Max results"),
) -> None:
    root, store, qe = _open_project()
    kind_enum = NodeKind(kind) if kind else None
    results = qe.search(query, kind=kind_enum, limit=limit)
    store.close()
    if not results:
        console.print("[yellow]No matches.[/yellow]")
        return
    tbl = Table(show_header=True, header_style="bold")
    for col in ("id", "kind", "name", "doc_id", "page"):
        tbl.add_column(col)
    for n in results:
        tbl.add_row(n.id, n.kind.value, n.name, n.doc_id, str(n.location.page or ""))
    console.print(tbl)


@app.command()
def query(text: str) -> None:
    search(text)


# ---------------------------------------------------------------------------
# register / pin / timing / figure / section / glossary
# ---------------------------------------------------------------------------


@app.command()
def register(name: str) -> None:
    root, store, qe = _open_project()
    d = qe.register(name)
    store.close()
    if d is None:
        console.print(f"[yellow]Register not found:[/yellow] {name}")  ; return
    n = d.node
    console.print(f"[bold green]{n.name}[/bold green]  [dim]({n.id})[/dim]")
    a = n.attrs
    console.print(f"  address={a.get('address')}  offset={a.get('offset')}  "
                  f"width={a.get('width')}  access={a.get('access')}  "
                  f"reset={a.get('reset_value')}  source={a.get('source')}")
    console.print(f"  doc={n.doc_id}  page={n.location.page}")
    if n.summary:
        console.print(f"  [italic]{n.summary}[/italic]")
    if not d.bitfields:
        console.print("  [yellow](no bitfields parsed)[/yellow]")
        return
    tbl = Table(title="Bitfields", show_header=True, header_style="bold")
    for col in ("bits", "name", "access", "reset", "description"):
        tbl.add_column(col)
    for bf in d.bitfields:
        ba = bf.attrs
        bits = f"{ba.get('bit_high')}:{ba.get('bit_low')}"
        tbl.add_row(bits, bf.name, str(ba.get("access") or ""), str(ba.get("reset") or ""),
                     (ba.get("description") or "")[:80])
    console.print(tbl)


@app.command()
def pin(name: str) -> None:
    root, store, qe = _open_project()
    d = qe.pin(name)
    store.close()
    if d is None:
        console.print(f"[yellow]Pin not found:[/yellow] {name}")  ; return
    _print_node(d.node)


@app.command()
def timing(name: str) -> None:
    root, store, qe = _open_project()
    d = qe.timing(name)
    store.close()
    if d is None:
        console.print(f"[yellow]Timing param not found:[/yellow] {name}")  ; return
    _print_node(d.node)


@app.command()
def figure(id_or_name: str) -> None:
    root, store, qe = _open_project()
    d = qe.figure(id_or_name)
    store.close()
    if d is None:
        console.print(f"[yellow]Figure not found:[/yellow] {id_or_name}")
        return
    n = d.node
    console.print(f"[bold]{n.name}[/bold]  type={n.attrs.get('figure_type')}  doc={n.doc_id} page={n.location.page}")
    if n.attrs.get("caption"):
        console.print(f"  caption: {n.attrs['caption']}")
    if n.attrs.get("vlm_desc"):
        console.print(f"  vlm_desc: {n.attrs['vlm_desc']}")
    if n.attrs.get("mermaid"):
        console.print(f"  mermaid: {n.attrs['mermaid'][:200]}...")


@app.command()
def section(path_or_id: str) -> None:
    root, store, qe = _open_project()
    d = qe.section(path_or_id)
    store.close()
    if d is None:
        console.print(f"[yellow]Section not found:[/yellow] {path_or_id}")
        return
    console.print(f"[bold]{d.node.name}[/bold]  path={d.node.qualified_name}  page={d.node.location.page}")
    if d.children:
        console.print("  Children:")
        for c in d.children:
            console.print(f"    · {c.name}")


@app.command()
def glossary(term: str) -> None:
    root, store, qe = _open_project()
    items = qe.glossary(term)
    store.close()
    if not items:
        console.print(f"[yellow]No term found:[/yellow] {term}")
        return
    for it in items:
        n = it.node
        full = (n.aliases[0] if n.aliases else None) or n.attrs.get("full", "")
        console.print(f"[bold]{n.name}[/bold]  →  {full}")


# ---------------------------------------------------------------------------
# context / trace / impact
# ---------------------------------------------------------------------------


@app.command()
def context(task: str) -> None:
    root, store, qe = _open_project()
    cb = qe.context(task)
    store.close()
    console.print(f"[bold]Context[/bold] for: {task}")
    console.print(f"  providers: {', '.join(cb.providers)}")
    console.print(f"  nodes: {len(cb.nodes)}  edges: {len(cb.edges)}  semantic_hits: {len(cb.semantic_hits)}")
    if cb.nodes:
        tbl = Table(show_header=True, header_style="bold")
        for col in ("id", "kind", "name"):
            tbl.add_column(col)
        for n in cb.nodes[:15]:
            tbl.add_row(n.id, n.kind.value, n.name)
        console.print(tbl)


@app.command()
def trace(from_id: str, to_id: str) -> None:
    root, store, qe = _open_project()
    paths = qe.trace(from_id, to_id)
    store.close()
    if not paths:
        console.print("[yellow]No path found.[/yellow]")
        return
    console.print(f"[bold]Path[/bold] (length {paths[0].length}):")
    for i, nid in enumerate(paths[0].nodes):
        arrow = " → " if i < len(paths[0].nodes) - 1 else ""
        console.print(f"  {i}. {nid}{arrow}")


@app.command()
def impact(id: str, depth: int = typer.Option(2, "--depth", help="Influence depth")) -> None:
    root, store, qe = _open_project()
    rep = qe.impact(id, depth=depth)
    store.close()
    if rep is None:
        console.print(f"[yellow]Node not found:[/yellow] {id}")
        return
    console.print(f"[bold]Impact[/bold] for {rep.root.name} (depth={rep.depth}):")
    for n in rep.affected[:20]:
        console.print(f"  · {n.id} ({n.kind.value})")
    if len(rep.affected) > 20:
        console.print(f"  ... and {len(rep.affected) - 20} more")


# ---------------------------------------------------------------------------
# node
# ---------------------------------------------------------------------------


@app.command()
def node(id: str) -> None:
    root, store, qe = _open_project()
    n = qe.node(id)
    store.close()
    if n is None:
        console.print(f"[red]Not found:[/red] {id}")

        raise typer.Exit(code=1)
    _print_node(n)


def _print_node(n) -> None:
    import json as _json
    console.print_json(_json.dumps(n.model_dump(), ensure_ascii=False, default=str))


# ---------------------------------------------------------------------------
# watch
# ---------------------------------------------------------------------------


@app.command()
def watch(paths: list[str] = typer.Option(None, "--path", help="Watch directory")) -> None:
    run_watch_loop(paths)


# ---------------------------------------------------------------------------
# serve --mcp
# ---------------------------------------------------------------------------


@app.command()
def serve(
    mcp: bool = typer.Option(False, "--mcp/--no-mcp", help="Start MCP stdio server"),
    web: bool = typer.Option(False, "--web/--no-web", help="Start Web UI"),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host (web only)"),
    port: int = typer.Option(8000, "--port", help="Bind port (web only)"),
) -> None:
    """Start a server. Default = Web UI (--web). Use --mcp for MCP stdio."""
    if not (mcp or web):
        web = True  # 默认 web，给非 agent 用户更友好
    if mcp:
        from docgraph.mcp.server import run_stdio
        run_stdio()
        return
    from docgraph.web.server import run as run_web
    run_web(host=host, port=port)


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


@app.command()
def version() -> None:
    console.print(f"docgraph {__version__}")


# ---------------------------------------------------------------------------
# plugins
# ---------------------------------------------------------------------------


plugins_app = typer.Typer(help="Plugin management")
app.add_typer(plugins_app, name="plugins")


@plugins_app.command("ls")
def plugins_ls(
    kind: str = typer.Option(None, "--kind", help="Filter by group: parsers/extractors/embeddings/stores/llm"),
) -> None:
    """List installed plugins (builtin + entry_points)."""
    from docgraph.core.plugins import discovered

    bootstrap()
    by_group = discovered()
    tbl = Table(show_header=True, header_style="bold")
    for col in ("group", "name", "kind", "target", "dist", "version", "enabled"):
        tbl.add_column(col)
    for group, plugins in sorted(by_group.items()):
        if kind and kind not in group:
            continue
        for p in plugins:
            tbl.add_row(
                group.replace("docgraph.", ""),
                p.name,
                "builtin" if p.builtin else "ext",
                p.target,
                p.dist or "-",
                p.version or "-",
                "✓" if p.enabled else "✗",
            )
    console.print(tbl)


@plugins_app.command("info")
def plugins_info(name: str) -> None:
    """Show detail of a plugin (by entry-point name)."""
    from docgraph.core.plugins import discovered

    bootstrap()
    for group, plugins in discovered().items():
        for p in plugins:
            if p.name == name:
                console.print(
                    f"[bold]{p.name}[/bold]  group={group}\n"
                    f"  target  : {p.target}\n"
                    f"  dist    : {p.dist}\n"
                    f"  version : {p.version}\n"
                    f"  builtin : {p.builtin}\n"
                    f"  enabled : {p.enabled}"
                )
                return
    console.print(f"[yellow]Plugin not found:[/yellow] {name}")


# ---------------------------------------------------------------------------
# migrate
# ---------------------------------------------------------------------------


@app.command()
def migrate(
    dry_run: bool = typer.Option(False, "--dry-run", help="Only show what would be done"),
) -> None:
    """Apply pending schema migrations on the graph database."""
    from docgraph.graph.migrations import (
        CURRENT_VERSION,
        current_db_version,
        run_migrations,
    )

    root = project_root_from_cwd()
    if not docgraph_dir(root).is_dir():
        console.print("[red]No .docgraph/ found.[/red]")
        raise typer.Exit(code=1)
    db = docgraph_dir(root) / "graph.db"
    cur = current_db_version(db)
    console.print(f"DB version: v{cur}    Target: v{CURRENT_VERSION}")
    if cur >= CURRENT_VERSION:
        console.print("[green]Up to date.[/green]")
        return
    applied = run_migrations(db, dry_run=dry_run)
    if dry_run:
        console.print(f"Would apply: {applied}")
    else:
        console.print(f"[green]Applied:[/green] {applied}")


# ---------------------------------------------------------------------------
# federate
# ---------------------------------------------------------------------------


federate_app = typer.Typer(help="Federation management — mount other docgraph projects as read-only.")
app.add_typer(federate_app, name="federate")


@federate_app.command("add")
def federate_add(
    path: Path = typer.Argument(..., help="Path to another docgraph project root"),
    name: str = typer.Option(None, "--name", help="Federation name (defaults to family)"),
) -> None:
    """Mount another project as a read-only federation."""
    from docgraph.linker.federate_mount import add_federation

    root = project_root_from_cwd()
    if not docgraph_dir(root).is_dir():
        console.print("[red]No .docgraph/ found.[/red]")
        raise typer.Exit(code=1)
    try:
        entry = add_federation(root, path, name=name)
        console.print(f"[green]Added[/green] {entry.name} → {entry.path}")
    except Exception as e:
        console.print(f"[red]Failed:[/red] {e}")
        raise typer.Exit(code=1)


@federate_app.command("ls")
def federate_ls() -> None:
    """List all federated projects."""
    from docgraph.linker.federate_mount import list_federations

    root = project_root_from_cwd()
    entries = list_federations(root)
    if not entries:
        console.print("[yellow]No federations yet.[/yellow]")
        return
    tbl = Table(show_header=True, header_style="bold")
    for col in ("name", "family", "path", "added_at"):
        tbl.add_column(col)
    for e in entries:
        tbl.add_row(e.name, e.family, e.path, e.added_at)
    console.print(tbl)


@federate_app.command("rm")
def federate_rm(name: str) -> None:
    """Remove a federation by name."""
    from docgraph.linker.federate_mount import remove_federation

    root = project_root_from_cwd()
    ok = remove_federation(root, name)
    if ok:
        console.print(f"[green]Removed:[/green] {name}")
    else:
        console.print(f"[yellow]Not found:[/yellow] {name}")


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------


@app.command()
def review(
    min_confidence: float = typer.Option(0.85, "--min-confidence",
                                          help="Show items below this confidence"),
) -> None:
    """Interactive TUI to review low-confidence nodes/edges."""
    from docgraph.review import run_review_tui

    root, store, _qe = _open_project()
    stats = run_review_tui(root, store, min_confidence=min_confidence)
    store.close()
    console.print(f"[bold]Done.[/bold] {stats}")


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


export_app = typer.Typer(help="Export to industry formats (IP-XACT, SystemRDL).")
app.add_typer(export_app, name="export")


@export_app.command("ipxact")
def export_ipxact_cmd(
    out: Path = typer.Argument(..., help="Output XML path"),
    register: str = typer.Option(None, "--register", help="Filter to one register"),
    component: str = typer.Option("spec", "--component"),
) -> None:
    """Export registers to IP-XACT (IEEE 1685-2014) XML."""
    from docgraph.export import export_ipxact

    root, store, _qe = _open_project()
    cfg = load_config(root)
    r = export_ipxact(
        store, out, family=cfg.project.family,
        component=component, register_name=register,
    )
    store.close()
    console.print(
        f"[green]Wrote[/green] {r.output_path}  "
        f"({r.registers} registers, {r.bitfields} bitfields)"
    )


@export_app.command("systemrdl")
def export_systemrdl_cmd(
    out: Path = typer.Argument(..., help="Output .rdl path"),
    register: str = typer.Option(None, "--register", help="Filter to one register"),
    component: str = typer.Option("spec", "--component"),
) -> None:
    """Export registers to SystemRDL 2.0."""
    from docgraph.export import export_systemrdl

    root, store, _qe = _open_project()
    cfg = load_config(root)
    r = export_systemrdl(
        store, out, family=cfg.project.family,
        component=component, register_name=register,
    )
    store.close()
    console.print(
        f"[green]Wrote[/green] {r.output_path}  "
        f"({r.registers} registers, {r.bitfields} bitfields)"
    )


if __name__ == "__main__":  # pragma: no cover
    app()
