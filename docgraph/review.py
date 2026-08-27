"""低置信关系的人工审核 TUI。

审核决定写入 ``.docgraph/entities/reviewed.jsonl`` 作为审计记录。拒绝会
立即删除当前索引中的关系，但决定尚不会在后续重建时自动重放。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from docgraph.core.config import docgraph_dir
from docgraph.graph.schema import EdgeKind
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.graph.store import EdgeQuery

console = Console()


@dataclass
class ReviewItem:
    target: dict
    confidence: float


def gather_low_confidence(
    store: SQLiteGraphStore,
    *,
    min_confidence: float = 0.85,
    limit: int = 50,
) -> list[ReviewItem]:
    """Return reviewable edges ordered from lowest confidence upward."""
    edges = store.search_edges(EdgeQuery(confidence_lt=min_confidence, limit=limit))
    return [
        ReviewItem(
            target=edge.model_dump(mode="json"),
            confidence=edge.confidence,
        )
        for edge in edges
    ]


def review_path(root: Path) -> Path:
    return docgraph_dir(root) / "entities" / "reviewed.jsonl"


def append_review(root: Path, item: dict) -> None:
    p = review_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def run_review_tui(
    root: Path,
    store: SQLiteGraphStore,
    *,
    min_confidence: float = 0.85,
) -> dict:
    items = gather_low_confidence(store, min_confidence=min_confidence, limit=200)
    if not items:
        console.print(f"[green]No low-confidence items below {min_confidence}.[/green]")
        return {"reviewed": 0}

    console.print(f"[bold]{len(items)} items below confidence {min_confidence}[/bold]")
    stats = {"accepted": 0, "rejected": 0, "skipped": 0}

    for idx, it in enumerate(items, start=1):
        console.rule(f"[{idx}/{len(items)}] confidence={it.confidence:.2f}")
        tbl = Table(show_header=False)
        for k, v in it.target.items():
            tbl.add_row(k, json.dumps(v, ensure_ascii=False)[:140])
        console.print(tbl)
        choice = Prompt.ask(
            "[a]ccept / [r]eject / [s]kip / [q]uit",
            choices=["a", "r", "s", "q"],
            default="s",
        )
        if choice == "q":
            break
        if choice == "a":
            stats["accepted"] += 1
            append_review(
                root,
                {
                    "decision": "accept",
                    "item": it.target,
                    "confidence": it.confidence,
                },
            )
        elif choice == "r":
            stats["rejected"] += 1
            try:
                store.delete_edge(
                    it.target["src"],
                    it.target["dst"],
                    EdgeKind(it.target["kind"]),
                )
            except Exception as e:
                console.print(f"[red]Delete failed:[/red] {e}")
            append_review(
                root,
                {
                    "decision": "reject",
                    "item": it.target,
                    "confidence": it.confidence,
                },
            )
        else:
            stats["skipped"] += 1
    stats["reviewed"] = stats["accepted"] + stats["rejected"]
    return stats
