"""人工审核 TUI —— 列出低置信节点/边，交互式 accept/reject/edit。

M4 简化实现：用 rich.prompt 做选择式审核。
审核结果写到 `.docgraph/entities/reviewed.jsonl`，下次 build 自动保留。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from docgraph.core.config import docgraph_dir
from docgraph.graph.sqlite_store import SQLiteGraphStore

console = Console()


@dataclass
class ReviewItem:
    kind: str           # "node" | "edge"
    target: dict        # 完整 dump
    confidence: float
    reason: str = ""


def gather_low_confidence(
    store: SQLiteGraphStore, *, min_confidence: float = 0.85, limit: int = 50,
) -> list[ReviewItem]:
    """边的 confidence 显式存了；节点没有，所以只过边。"""
    conn = store._connect()  # type: ignore[attr-defined]
    rows = conn.execute(
        """
        SELECT src, dst, kind, confidence, evidence, attrs, created_at, schema_version
        FROM edges
        WHERE confidence IS NOT NULL AND confidence < ?
        ORDER BY confidence ASC
        LIMIT ?
        """,
        (min_confidence, limit),
    ).fetchall()
    out: list[ReviewItem] = []
    for r in rows:
        out.append(ReviewItem(
            kind="edge",
            target={
                "src": r["src"], "dst": r["dst"], "kind": r["kind"],
                "confidence": r["confidence"],
                "evidence": json.loads(r["evidence"]) if r["evidence"] else {},
            },
            confidence=r["confidence"] or 0.0,
        ))
    return out


def review_path(root: Path) -> Path:
    return docgraph_dir(root) / "entities" / "reviewed.jsonl"


def append_review(root: Path, item: dict) -> None:
    p = review_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def run_review_tui(
    root: Path, store: SQLiteGraphStore, *, min_confidence: float = 0.85,
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
            choices=["a", "r", "s", "q"], default="s",
        )
        if choice == "q":
            break
        if choice == "a":
            stats["accepted"] += 1
            append_review(root, {
                "decision": "accept", "item": it.target,
                "confidence": it.confidence,
            })
        elif choice == "r":
            stats["rejected"] += 1
            # 同时从图中删除这条边
            try:
                conn = store._connect()  # type: ignore[attr-defined]
                conn.execute(
                    "DELETE FROM edges WHERE src=? AND dst=? AND kind=?",
                    (it.target["src"], it.target["dst"], it.target["kind"]),
                )
                conn.commit()
            except Exception as e:
                console.print(f"[red]Delete failed:[/red] {e}")
            append_review(root, {
                "decision": "reject", "item": it.target,
                "confidence": it.confidence,
            })
        else:
            stats["skipped"] += 1
    stats["reviewed"] = stats["accepted"] + stats["rejected"]
    return stats
