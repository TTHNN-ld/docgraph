"""端到端对比测试：DocGraph vs Base (纯 FTS) 在芯片 spec 检索场景。

运行方式：
    python tests/e2e/test_docgraph_vs_base.py

输出：
    - 每个 case 的 DocGraph 和 Base 结果（摘要）
    - 对比矩阵 (CSV)
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

# 确保项目根在 sys.path
_project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_project_root))

from docgraph.core.dotenv import autoload_env
from docgraph.core.config import project_root_from_cwd, load_config
from docgraph.core.logger import set_level
from docgraph.embeddings.factory import open_query_embeddings
from docgraph.graph.schema import NodeKind
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.query.engine import QueryEngine
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console(highlight=False)
_HD = "─" * 72


# ═══════════════════════════════════════════════════════════════════════════════
# 10 个测试用例
# ═══════════════════════════════════════════════════════════════════════════════

CASES = [
    # 1 ─ 模块定位
    {
        "id": 1,
        "title": "模块定位",
        "question": "FTB 模块的功能是什么？它有哪些关键设计参数？",
        "docgraph_query": "FTB 模块功能 设计参数 表项",
        "base_query": "FTB 模块 功能 设计参数",
    },
    # 2 ─ 寄存器配置
    {
        "id": 2,
        "title": "寄存器配置",
        "question": "怎样配置 sbpctl 寄存器来禁用 TAGE 预测器？",
        "docgraph_query": "sbpctl TAGE 禁用",
        "base_query": "sbpctl TAGE 预测器 禁用",
    },
    # 3 ─ 时序理解
    {
        "id": 3,
        "title": "时序理解",
        "question": "BPU 的 s1/s2/s3 三级流水各自做什么？什么情况会导致流水线冲刷？",
        "docgraph_query": "BPU s1 s2 s3 流水 冲刷",
        "base_query": "BPU s1 s2 s3 流水级 冲刷",
    },
    # 4 ─ 接口协议
    {
        "id": 4,
        "title": "接口协议",
        "question": "BPU 的对外接口是什么协议？有哪些握手信号？",
        "docgraph_query": "BPU 接口 协议 握手信号",
        "base_query": "BPU 接口 协议 valid ready",
    },
    # 5 ─ 异常处理
    {
        "id": 5,
        "title": "异常处理",
        "question": "BPU 预测错误时怎么恢复？有哪些异常处理路径？",
        "docgraph_query": "BPU 预测错误 恢复 异常处理 冲刷",
        "base_query": "BPU 预测错误 恢复 异常 重定向",
    },
    # 6 ─ 配置参数
    {
        "id": 6,
        "title": "配置参数",
        "question": "BPU 的 ICache 可以配置哪些参数？各自的范围和默认值是什么？",
        "docgraph_query": "ICache BPU 参数 配置 默认值",
        "base_query": "ICache 参数 默认值 配置",
    },
    # 7 ─ 性能限制
    {
        "id": 7,
        "title": "性能限制",
        "question": "TAGE 预测器在什么情况下准确率会下降？有哪些已知的性能限制？",
        "docgraph_query": "TAGE 准确率 下降 性能 限制",
        "base_query": "TAGE 准确率 性能限制",
    },
    # 8 ─ 综合理解
    {
        "id": 8,
        "title": "综合理解",
        "question": "从取指到执行，一条指令经过的完整流水线是怎样的？各阶段可能出现的异常有哪些？",
        "docgraph_query": "取指 执行 流水线 IFU IDU EXU MEM WB",
        "base_query": "取指 执行 流水线 异常",
    },
    # 9 ─ 信号追踪
    {
        "id": 9,
        "title": "信号追踪",
        "question": "信号 'pc' 在 BPU 中是怎么产生的？它被哪些模块使用了？",
        "docgraph_query": "pc 信号 BPU 产生",
        "base_query": "pc 信号 BPU",
    },
    # 10 ─ 架构差异
    {
        "id": 10,
        "title": "架构差异",
        "question": "昆明湖架构的 RAS 预测器和南湖架构有什么不同？",
        "docgraph_query": "RAS 预测器 昆明湖 南湖 差异 持久化队列",
        "base_query": "RAS 预测器 昆明湖 南湖",
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# 检索后端
# ═══════════════════════════════════════════════════════════════════════════════

class BaseRetriever:
    """纯 FTS 全文检索 —— 模拟无 DocGraph 时的传统搜索。

    中文查询按空格/标点拆词后做 OR 匹配（LIKE），模拟 FTS 分词的最简替代。
    """

    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        import re
        q = query.strip()
        if not q:
            return []
        # 中文按空格 + 常见标点拆成独立词，每个词独立 LIKE 取 OR
        tokens = [t.strip() for t in re.split(r"[，。、\s]+", q) if t.strip() and len(t.strip()) >= 2]
        if not tokens:
            tokens = [q]
        # 逐词 OR 查询
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for token in tokens[:6]:  # 最多 6 个搜索词
            rows = self.conn.execute(
                "SELECT id AS chunk_id, substr(text, 1, 300) AS snip "
                "FROM chunks WHERE text LIKE ? LIMIT ?",
                [f"%{token}%", limit],
            ).fetchall()
            for r in rows:
                cid = r["chunk_id"]
                if cid in seen:
                    continue
                seen.add(cid)
                out.append({"chunk_id": cid, "snippet": r["snip"]})
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
        return out

    def entity_search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Base 没有实体索引，回退到全文匹配。"""
        return self.search(query, limit=limit)

    def close(self):
        self.conn.close()


class DocGraphRetriever:
    """DocGraph 完整检索：实体精确匹配 + 向量语义 + FTS 混合。"""

    def __init__(self, qe: QueryEngine):
        self.qe = qe

    def entity_search(self, query: str, kind: NodeKind | None = None, limit: int = 10) -> list[dict[str, Any]]:
        nodes = self.qe.search(query, kind=kind, limit=limit, use_semantic=True)
        return [
            {
                "id": n.id,
                "kind": n.kind.value,
                "name": n.name,
                "page": n.location.page if n.location else None,
                "summary": n.summary or "",
            }
            for n in nodes
        ]

    def chunk_search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return self.qe.search_chunks(query, limit=limit)

    def neighbors(self, node_id: str, depth: int = 1):
        return self.qe.neighbors(node_id, depth=depth)


# ═══════════════════════════════════════════════════════════════════════════════
# 运行器
# ═══════════════════════════════════════════════════════════════════════════════

def _truncate(text: str, n: int = 120) -> str:
    return text[:n] + "…" if len(text) > n else text


def run_case(case: dict, dg: DocGraphRetriever, base: BaseRetriever) -> dict:
    """对单个 case 同时跑 DocGraph 和 Base，返回结构化结果。"""
    result: dict[str, Any] = {"case": case}
    limit = 10

    # ── DocGraph ──
    dg_entities = dg.entity_search(case["docgraph_query"], limit=limit)
    dg_chunks = dg.chunk_search(case["docgraph_query"], limit=limit)

    # 对实体结果展开关系子图（取前 2 个 entity 的 neighbors）
    dg_neighbors: list[dict] = []
    for ent in dg_entities[:2]:
        try:
            sub = dg.neighbors(ent["id"], depth=1)
            for n in sub.nodes:
                dg_neighbors.append({
                    "of": ent["name"],
                    "kind": n.kind.value,
                    "name": n.name,
                })
        except Exception:
            pass

    result["docgraph"] = {
        "entities": dg_entities,
        "entity_count": len(dg_entities),
        "chunks": dg_chunks,
        "chunk_count": len(dg_chunks),
        "neighbors": dg_neighbors[:10],
    }

    # ── Base ──
    base_chunks = base.search(case["base_query"], limit=limit)
    base_entities = base.entity_search(case["base_query"], limit=limit)

    result["base"] = {
        "chunks": base_chunks,
        "chunk_count": len(base_chunks),
        "entities": base_entities,
        "entity_count": len(base_entities),
    }

    return result


def print_case_result(result: dict) -> None:
    """Rich 渲染单个 case 的对比结果。"""
    case = result["case"]
    dg = result["docgraph"]
    bs = result["base"]

    console.print(Panel(f"[bold]Case {case['id']}: {case['title']}[/bold]\n{case['question']}"))

    # DocGraph 结果
    console.print("[bold cyan]▎DocGraph[/bold cyan]")
    if dg["entities"]:
        console.print(f"  [dim]实体匹配 ({dg['entity_count']}):[/dim]")
        for e in dg["entities"][:5]:
            console.print(f"    [{e['kind']}] [bold]{e['name']}[/bold]  p{e.get('page','?')}  {_truncate(e.get('summary',''))}")
    if dg["neighbors"]:
        console.print(f"  [dim]关系子图 ({len(dg['neighbors'])}):[/dim]")
        for nb in dg["neighbors"][:6]:
            console.print(f"    [{nb['kind']}] {nb['name']}  ← {nb['of']}")
    if dg["chunks"]:
        console.print(f"  [dim]Chunk 检索 ({dg['chunk_count']}):[/dim]")
        for c in dg["chunks"][:3]:
            console.print(f"    {_truncate(c.get('snippet','') or c.get('text',''), 100)}")

    console.print()

    # Base 结果
    console.print("[bold yellow]▎Base (FTS only)[/bold yellow]")
    if bs["chunks"]:
        console.print(f"  [dim]Chunk 检索 ({bs['chunk_count']}):[/dim]")
        for c in bs["chunks"][:5]:
            console.print(f"    {_truncate(c.get('snippet',''), 100)}")
    else:
        console.print("  [red]无结果[/red]")

    console.print(_HD)
    console.print()


def print_summary(results: list[dict]) -> None:
    """汇总矩阵。"""
    tbl = Table(title="对比矩阵", show_header=True, header_style="bold")
    tbl.add_column("Case", style="dim")
    tbl.add_column("场景", style="dim")
    tbl.add_column("DG 实体", justify="right")
    tbl.add_column("DG Chunk", justify="right")
    tbl.add_column("DG 关系", justify="right")
    tbl.add_column("Base Chunk", justify="right")
    tbl.add_column("Base 实体", justify="right")

    for r in results:
        c = r["case"]
        dg = r["docgraph"]
        bs = r["base"]
        tbl.add_row(
            f"#{c['id']}",
            c["title"],
            str(dg["entity_count"]),
            str(dg["chunk_count"]),
            str(len(dg["neighbors"])),
            str(bs["chunk_count"]),
            str(bs["entity_count"]),
        )

    console.print(tbl)
    console.print()
    console.print("[dim]评分指南 (每个 case 分别对 DG 和 Base 打分 0-3):[/dim]")
    console.print("  0 = 找不到  1 = 有片段但需大量翻原文  2 = 部分可用  3 = 直接可用")
    console.print("  额外记录: [red]幻觉[/red] (编造) / [yellow]遗漏[/yellow] (明显相关但未召回)")
    console.print()
    console.print("[dim]请在每个 case 的输出里观察并填入分数。[/dim]")


# ═══════════════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    root = project_root_from_cwd()
    dg_dir = root / ".docgraph"
    if not dg_dir.is_dir():
        console.print("[red]No .docgraph/ found. Run docgraph build first.[/red]")
        sys.exit(1)

    autoload_env(root)
    cfg = load_config(root)
    set_level(cfg.logging.level)

    store = SQLiteGraphStore(dg_dir / "graph.db")
    store.init_schema()
    vstore, encoder = open_query_embeddings(cfg.embeddings, cfg.storage, dg_dir)
    qe = QueryEngine(store, vstore=vstore, encoder=encoder)
    dg = DocGraphRetriever(qe)
    base = BaseRetriever(dg_dir / "graph.db")

    console.print(f"[bold]DocGraph vs Base — 芯片文档检索对比[/bold]")
    console.print(f"  文档数: {len(store.list_docs())}")
    console.print(f"  总节点: {store.count_nodes()}  总边: {store.count_edges()}")
    console.print(f"  语义检索: {'✅' if encoder else '❌'}")
    console.print(_HD)

    results = []
    for case in CASES:
        console.print(f"[dim]查询 #{case['id']}...[/dim]", end="\r")
        r = run_case(case, dg, base)
        results.append(r)
        console.print(" " * 30, end="\r")

    for r in results:
        print_case_result(r)

    print_summary(results)

    store.close()
    base.close()


if __name__ == "__main__":
    main()
