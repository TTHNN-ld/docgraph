"""联邦挂接 —— 把另一个 .docgraph/ 项目作为只读视图挂入当前项目。

设计：
- 元数据存在 `.docgraph/federations.json`
- 挂接时记录：name + path（绝对）+ family + 添加时间
- 查询时由 FederatedGraphStore 跨多 db 合并结果（只读）
- 写入操作只影响当前项目的 graph.db；远端是只读
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from docgraph.core.config import docgraph_dir
from docgraph.core.logger import get_logger

log = get_logger(__name__)


@dataclass
class FederationEntry:
    name: str
    path: str  # 绝对路径，指向另一个项目根（含 .docgraph/）
    family: str = "unknown"
    added_at: str = ""


@dataclass
class FederationManifest:
    entries: list[FederationEntry] = field(default_factory=list)


def federations_path(root: Path) -> Path:
    return docgraph_dir(root) / "federations.json"


def load_federations(root: Path) -> FederationManifest:
    p = federations_path(root)
    if not p.is_file():
        return FederationManifest()
    data = json.loads(p.read_text("utf-8"))
    return FederationManifest(entries=[FederationEntry(**e) for e in data.get("entries", [])])


def save_federations(root: Path, manifest: FederationManifest) -> None:
    p = federations_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {"entries": [e.__dict__ for e in manifest.entries]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def add_federation(
    root: Path,
    target_path: Path,
    *,
    name: str | None = None,
) -> FederationEntry:
    """挂接 target_path（另一项目根）。"""
    target = target_path.resolve()
    if not (target / ".docgraph" / "graph.db").is_file():
        raise RuntimeError(
            f"Target {target} does not contain .docgraph/graph.db; is it a docgraph project?"
        )

    # 读 target 的 config 拿 family
    from docgraph.core.config import load_config

    family = "unknown"
    try:
        cfg = load_config(target)
        family = cfg.project.family
    except Exception:
        pass

    entry_name = name or family or target.name
    manifest = load_federations(root)
    # 去重
    manifest.entries = [e for e in manifest.entries if e.name != entry_name]
    entry = FederationEntry(
        name=entry_name,
        path=str(target),
        family=family,
        added_at=_utcnow(),
    )
    manifest.entries.append(entry)
    save_federations(root, manifest)
    log.info(f"[federate] added: {entry_name} → {target}")
    return entry


def remove_federation(root: Path, name: str) -> bool:
    manifest = load_federations(root)
    before = len(manifest.entries)
    manifest.entries = [e for e in manifest.entries if e.name != name]
    save_federations(root, manifest)
    return len(manifest.entries) < before


def list_federations(root: Path) -> list[FederationEntry]:
    return load_federations(root).entries


# ---------------------------------------------------------------------------
# Federated store —— 跨 db 只读查询
# ---------------------------------------------------------------------------


class FederatedGraphStore:
    """组合本地 + 远端 GraphStore 的只读视图。

    本地 store 可写；远端只读取且不接受 upsert。
    用于 QueryEngine：search / get_node / neighbors 都返回合并后的结果。
    """

    def __init__(
        self,
        local,
        remotes: list,
    ) -> None:
        self.local = local
        self.remotes = remotes

    # --- 读 ---

    def get_node(self, id: str):
        n = self.local.get_node(id)
        if n is not None:
            return n
        for r in self.remotes:
            n = r.get_node(id)
            if n is not None:
                return n
        return None

    def search_nodes(self, query):
        results = list(self.local.search_nodes(query))
        seen = {n.id for n in results}
        for r in self.remotes:
            for n in r.search_nodes(query):
                if n.id in seen:
                    continue
                seen.add(n.id)
                results.append(n)
                if len(results) >= query.limit:
                    break
            if len(results) >= query.limit:
                break
        return results

    def neighbors(self, id: str, edge_kinds=None, depth: int = 1, limit: int = 50):
        # 简化：先看本地有没有；若没有再轮询远端
        sub = self.local.neighbors(id, edge_kinds=edge_kinds, depth=depth, limit=limit)
        if sub.nodes:
            return sub
        for r in self.remotes:
            sub = r.neighbors(id, edge_kinds=edge_kinds, depth=depth, limit=limit)
            if sub.nodes:
                return sub
        return sub

    def count_nodes(self, kind=None) -> int:
        total = self.local.count_nodes(kind)
        for r in self.remotes:
            total += r.count_nodes(kind)
        return total

    def count_edges(self, kind=None) -> int:
        total = self.local.count_edges(kind)
        for r in self.remotes:
            total += r.count_edges(kind)
        return total

    def list_docs(self) -> list[str]:
        out = list(self.local.list_docs())
        for r in self.remotes:
            for d in r.list_docs():
                if d not in out:
                    out.append(d)
        return out

    # --- 写：仅 delegate 到 local ---

    def upsert_node(self, node):
        return self.local.upsert_node(node)

    def upsert_edge(self, edge):
        return self.local.upsert_edge(edge)

    def delete_node(self, id: str):
        return self.local.delete_node(id)

    def delete_doc(self, doc_id: str):
        return self.local.delete_doc(doc_id)

    def init_schema(self):
        return self.local.init_schema()

    def close(self):
        self.local.close()
        for r in self.remotes:
            r.close()


def open_federated_store(root: Path):
    """打开当前项目 + 所有挂接的远端项目，返回 FederatedGraphStore。"""
    from docgraph.graph.sqlite_store import SQLiteGraphStore

    local = SQLiteGraphStore(docgraph_dir(root) / "graph.db")
    local.init_schema()

    remotes: list = []
    for entry in list_federations(root):
        remote_db = Path(entry.path) / ".docgraph" / "graph.db"
        if not remote_db.is_file():
            log.warning(f"[federate] skip missing remote: {entry.name} ({remote_db})")
            continue
        rs = SQLiteGraphStore(remote_db)
        rs.init_schema()
        remotes.append(rs)

    if not remotes:
        return local
    return FederatedGraphStore(local, remotes)
