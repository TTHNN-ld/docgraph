"""Agent 模式对比测试：DocGraph vs Base (纯文本搜索)。

模拟 Agent 工作流：
  1. 检索阶段：从知识库中查找相关上下文
  2. 生成阶段：LLM 基于检索上下文回答问题

对比指标：
  - 检索耗时 / 工具调用次数
  - LLM token 消耗 (输入=检索上下文大小, 输出=答案长度)
  - LLM 调用次数
  - 总耗时

运行：
  python tests/e2e/test_agent_comparison.py
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_project_root))

from docgraph.core.config import project_root_from_cwd, load_config
from docgraph.core.dotenv import autoload_env
from docgraph.core.ids import file_hash
from docgraph.core.logger import get_logger, set_level
from docgraph.embeddings.factory import build_encoder
from docgraph.embeddings.vector_factory import build_vector_store
from docgraph.graph.schema import NodeKind
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.llm.client import CostTracker, LLMClient, make_provider
from docgraph.query.engine import QueryEngine

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console(highlight=False)
log = get_logger(__name__)
_HD = "─" * 72

# ═══════════════════════════════════════════════════════════════════════════════
# 10 个生产场景测试用例
# ═══════════════════════════════════════════════════════════════════════════════

CASES: list[dict[str, str]] = [
    {
        "id": "1",
        "title": "模块定位",
        "question": "FTB 模块的功能是什么？它有哪些关键设计参数？",
        "dg_query": "FTB 模块 功能 设计参数 表项",
        "base_query": "FTB 模块 功能 设计参数",
    },
    {
        "id": "2",
        "title": "寄存器配置",
        "question": "怎样配置 sbpctl 寄存器来禁用 TAGE 预测器？",
        "dg_query": "sbpctl TAGE 禁用 位域",
        "base_query": "sbpctl TAGE 预测器 禁用",
    },
    {
        "id": "3",
        "title": "时序理解",
        "question": "BPU 的 s1/s2/s3 三级流水各自做什么？什么情况会导致流水线冲刷？",
        "dg_query": "BPU s1 s2 s3 流水 冲刷",
        "base_query": "BPU s1 s2 s3 流水级 冲刷",
    },
    {
        "id": "4",
        "title": "接口协议",
        "question": "BPU 的对外接口是什么协议？有哪些握手信号？",
        "dg_query": "BPU 接口 协议 握手 信号",
        "base_query": "BPU 接口 协议 valid ready 握手",
    },
    {
        "id": "5",
        "title": "异常处理",
        "question": "BPU 预测错误时怎么恢复？有哪些异常处理路径？",
        "dg_query": "BPU 预测错误 恢复 异常 重定向",
        "base_query": "BPU 预测错误 恢复 异常 重定向",
    },
    {
        "id": "6",
        "title": "配置参数",
        "question": "BPU 的 ICache 可以配置哪些参数？各自的范围和默认值是什么？",
        "dg_query": "ICache BPU 参数 配置 nSets nWay",
        "base_query": "ICache 参数 默认值 配置",
    },
    {
        "id": "7",
        "title": "性能限制",
        "question": "TAGE 预测器在什么情况下准确率会下降？有哪些已知的性能限制？",
        "dg_query": "TAGE 准确率 下降 性能 限制 altpred",
        "base_query": "TAGE 准确率 性能 限制",
    },
    {
        "id": "8",
        "title": "综合理解",
        "question": "从取指到执行，一条指令在香山处理器中经过的完整流水线是怎样的？各阶段可能出现的异常有哪些？",
        "dg_query": "取指 执行 流水线 IFU IDU EXU MEM WB FTQ",
        "base_query": "取指 执行 流水线 异常",
    },
    {
        "id": "9",
        "title": "信号追踪",
        "question": "信号 'pc' 在 BPU 中是怎么产生的？它被哪些模块使用了？",
        "dg_query": "pc 信号 BPU Composer FTQ 预测",
        "base_query": "pc 信号 BPU 产生",
    },
    {
        "id": "10",
        "title": "架构差异",
        "question": "昆明湖架构的 RAS 预测器和南湖架构有什么不同？",
        "dg_query": "RAS 预测器 昆明湖 南湖 差异 持久化队列",
        "base_query": "RAS 预测器 昆明湖 南湖 不同",
    },
]

SYSTEM_PROMPT = """你是一个芯片设计验证工程师的助手。你的任务是回答关于芯片设计文档的问题。

规则：
- 只能基于提供的上下文回答，不要编造。
- 如果上下文不足以回答，诚实说明"根据现有文档无法确定"。
- 给出具体的信号名、寄存器地址、参数值等可操作信息。
- 用中文回答，技术术语保留英文原名。"""


# ═══════════════════════════════════════════════════════════════════════════════
# 检索后端
# ═══════════════════════════════════════════════════════════════════════════════

class DocGraphBackend:
    """DocGraph 检索：实体 + 语义 chunk + 图关系。"""

    def __init__(self, qe: QueryEngine, store: SQLiteGraphStore):
        self.qe = qe
        self.store = store

    def retrieve(self, query: str, max_entity: int = 8, max_chunk: int = 8) -> dict[str, Any]:
        metrics = {"tool_calls": 0, "time_ms": 0.0}
        t0 = time.time()

        # Tool call 1: 实体搜索（语义搜索可能因模型下载失败，自动 fallback）
        metrics["tool_calls"] += 1
        try:
            entities = self.qe.search(query, limit=max_entity, use_semantic=True)
        except Exception:
            entities = self.qe.search(query, limit=max_entity, use_semantic=False)

        # Tool call 2: chunk 混合检索
        metrics["tool_calls"] += 1
        chunks = self.qe.search_chunks(query, limit=max_chunk)

        # Tool calls 3+: 对前 3 个实体拉关系子图
        neighbors: list[dict] = []
        for ent in entities[:3]:
            metrics["tool_calls"] += 1
            try:
                sub = self.qe.neighbors(ent.id, depth=1)
                for n in sub.nodes:
                    neighbors.append({
                        "of": ent.name,
                        "kind": n.kind.value,
                        "name": n.name,
                        "summary": n.summary or "",
                    })
            except Exception:
                pass

        # Tool call N+1: 对前 2 个寄存器查 bitfield
        for ent in entities[:2]:
            if ent.kind == NodeKind.REGISTER:
                metrics["tool_calls"] += 1
                try:
                    detail = self.qe.register(ent.name)
                    if detail and detail.bitfields:
                        for bf in detail.bitfields:
                            neighbors.append({
                                "of": ent.name,
                                "kind": "bitfield",
                                "name": bf.name,
                                "summary": f"bit[{bf.attrs.get('bit_high', '?')}:{bf.attrs.get('bit_low', '?')}] {bf.summary or ''}",
                            })
                except Exception:
                    pass

        metrics["time_ms"] = (time.time() - t0) * 1000
        return {
            "entities": [
                {"kind": e.kind.value, "name": e.name, "page": e.location.page if e.location else None, "summary": e.summary or ""}
                for e in entities
            ],
            "chunks": [
                {"snippet": c.get("snippet", "") or c.get("text", "")}
                for c in chunks[:max_chunk]
            ],
            "neighbors": neighbors[:12],
            "metrics": metrics,
        }


class BaseBackend:
    """纯文本检索：SQLite LIKE 多词 OR。"""

    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row

    def retrieve(self, query: str, max_chunk: int = 12) -> dict[str, Any]:
        metrics = {"tool_calls": 0, "time_ms": 0.0}
        t0 = time.time()

        tokens = [t.strip() for t in re.split(r"[，。、\s]+", query) if len(t.strip()) >= 2]
        if not tokens:
            tokens = [query]

        metrics["tool_calls"] = min(len(tokens), 6)  # 每个 token 一次搜索
        seen: set[str] = set()
        chunks: list[dict] = []

        for token in tokens[:6]:
            rows = self.conn.execute(
                "SELECT id AS chunk_id, substr(text, 1, 400) AS snippet "
                "FROM chunks WHERE text LIKE ? LIMIT ?",
                [f"%{token}%", max_chunk],
            ).fetchall()
            for r in rows:
                cid = r["chunk_id"]
                if cid in seen:
                    continue
                seen.add(cid)
                chunks.append({"snippet": r["snippet"]})
                if len(chunks) >= max_chunk:
                    break
            if len(chunks) >= max_chunk:
                break

        metrics["time_ms"] = (time.time() - t0) * 1000
        return {
            "entities": [],
            "chunks": chunks,
            "neighbors": [],
            "metrics": metrics,
        }

    def close(self):
        self.conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 上下文构建 + LLM 回答
# ═══════════════════════════════════════════════════════════════════════════════

def build_context(retrieved: dict, max_tokens: int = 3000) -> str:
    """将检索结果序列化为 LLM 上下文字符串。"""
    parts: list[str] = []

    if retrieved["entities"]:
        parts.append("## 匹配到的实体")
        for e in retrieved["entities"]:
            parts.append(f"- [{e['kind']}] **{e['name']}** (p{e.get('page','?')}): {e.get('summary','')}")

    if retrieved["neighbors"]:
        parts.append("\n## 关联实体")
        for nb in retrieved["neighbors"]:
            parts.append(f"- [{nb['kind']}] {nb['name']} ← {nb['of']}: {nb.get('summary','')}")

    if retrieved["chunks"]:
        parts.append("\n## 相关文档片段")
        for i, c in enumerate(retrieved["chunks"], 1):
            snippet = c.get("snippet", "")
            if snippet:
                parts.append(f"\n[{i}] {snippet}")

    context = "\n".join(parts)
    # 粗略截断 (1 token ≈ 2 chars for CJK)
    if len(context) > max_tokens * 2:
        context = context[:max_tokens * 2]
    return context


def answer_with_llm(
    question: str,
    context: str,
    llm_client: LLMClient,
    tracker: CostTracker,
) -> dict[str, Any]:
    """LLM 基于上下文回答问题，返回答案和 token 指标。"""
    prompt = f"# 上下文\n{context}\n\n# 问题\n{question}\n\n请基于上下文回答问题："

    t0 = time.time()
    response = llm_client.complete(
        prompt,
        tier="balanced",
        max_tokens=1024,
        temperature=0.0,
        system=SYSTEM_PROMPT,
        extractor="agent_test",
    )
    elapsed = time.time() - t0

    tracker.record(response, extractor="agent_test")

    return {
        "answer": response.text,
        "tokens_in": response.tokens_in,
        "tokens_out": response.tokens_out,
        "cost_usd": response.cost_usd,
        "cache_hit": response.cache_hit,
        "llm_time_s": elapsed,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 单 Case 对比
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class CaseResult:
    case_id: str
    title: str
    question: str

    # DocGraph 指标
    dg_retrieval_ms: float = 0.0
    dg_tool_calls: int = 0
    dg_llm_tokens_in: int = 0
    dg_llm_tokens_out: int = 0
    dg_llm_cost: float = 0.0
    dg_llm_time_s: float = 0.0
    dg_cache_hit: bool = False
    dg_answer: str = ""
    _dg_retrieved: dict | None = None

    # Base 指标
    base_retrieval_ms: float = 0.0
    base_tool_calls: int = 0
    base_llm_tokens_in: int = 0
    base_llm_tokens_out: int = 0
    base_llm_cost: float = 0.0
    base_llm_time_s: float = 0.0
    base_cache_hit: bool = False
    base_answer: str = ""
    _base_retrieved: dict | None = None


def run_one_case(
    case: dict,
    dg_backend: DocGraphBackend,
    base_backend: BaseBackend,
    llm_client: LLMClient,
) -> CaseResult:
    r = CaseResult(case_id=case["id"], title=case["title"], question=case["question"])

    # ── DocGraph ──
    dg_ret = dg_backend.retrieve(case["dg_query"])
    dg_ctx = build_context(dg_ret)
    dg_tracker = CostTracker()
    dg_ans = answer_with_llm(case["question"], dg_ctx, llm_client, dg_tracker)

    r.dg_retrieval_ms = dg_ret["metrics"]["time_ms"]
    r.dg_tool_calls = dg_ret["metrics"]["tool_calls"]
    r.dg_llm_tokens_in = dg_ans["tokens_in"]
    r.dg_llm_tokens_out = dg_ans["tokens_out"]
    r.dg_llm_cost = dg_ans["cost_usd"]
    r.dg_llm_time_s = dg_ans["llm_time_s"]
    r.dg_cache_hit = dg_ans["cache_hit"]
    r.dg_answer = dg_ans["answer"]
    # 保留完整检索上下文
    r._dg_retrieved = {"entities": dg_ret["entities"], "chunks": dg_ret["chunks"], "neighbors": dg_ret["neighbors"]}

    # ── Base ──
    base_ret = base_backend.retrieve(case["base_query"])
    base_ctx = build_context(base_ret)
    base_tracker = CostTracker()
    base_ans = answer_with_llm(case["question"], base_ctx, llm_client, base_tracker)

    r.base_retrieval_ms = base_ret["metrics"]["time_ms"]
    r.base_tool_calls = base_ret["metrics"]["tool_calls"]
    r.base_llm_tokens_in = base_ans["tokens_in"]
    r.base_llm_tokens_out = base_ans["tokens_out"]
    r.base_llm_cost = base_ans["cost_usd"]
    r.base_llm_time_s = base_ans["llm_time_s"]
    r.base_cache_hit = base_ans["cache_hit"]
    r.base_answer = base_ans["answer"]
    r._base_retrieved = {"entities": base_ret["entities"], "chunks": base_ret["chunks"], "neighbors": base_ret["neighbors"]}

    return r


# ═══════════════════════════════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════════════════════════════

def print_case_detail(r: CaseResult):
    console.print(Panel(f"[bold]Case {r.case_id}: {r.title}[/bold]\n{r.question}"))
    console.print(f"[bold cyan]▎DocGraph[/bold cyan]  "
                   f"检索={r.dg_retrieval_ms:.0f}ms 工具调用={r.dg_tool_calls}  "
                   f"tokens_in={r.dg_llm_tokens_in} tokens_out={r.dg_llm_tokens_out}  "
                   f"LLM耗时={r.dg_llm_time_s:.1f}s 成本=${r.dg_llm_cost:.4f}  "
                   f"缓存={'✅' if r.dg_cache_hit else '❌'}")
    console.print(f"  [dim]{r.dg_answer}[/dim]")

    console.print(f"[bold yellow]▎Base (FTS)[/bold yellow]  "
                   f"检索={r.base_retrieval_ms:.0f}ms 工具调用={r.base_tool_calls}  "
                   f"tokens_in={r.base_llm_tokens_in} tokens_out={r.base_llm_tokens_out}  "
                   f"LLM耗时={r.base_llm_time_s:.1f}s 成本=${r.base_llm_cost:.4f}  "
                   f"缓存={'✅' if r.base_cache_hit else '❌'}")
    console.print(f"  [dim]{r.base_answer}[/dim]")
    console.print(_HD)


def print_summary_table(results: list[CaseResult]):
    tbl = Table(title="Agent 模式对比汇总", show_header=True, header_style="bold")
    cols = [
        ("Case", 6), ("场景", 10),
        ("DG 检索ms", 10), ("Base 检索ms", 11),
        ("DG 工具", 7), ("Base 工具", 8),
        ("DG Token入", 10), ("Base Token入", 11),
        ("DG 耗时s", 8), ("Base 耗时s", 9),
        ("DG 成本$", 9), ("Base 成本$", 10),
    ]
    for name, w in cols:
        tbl.add_column(name, style="dim" if "Base" in name else "", width=w)

    for r in results:
        tbl.add_row(
            f"#{r.case_id}", r.title,
            f"{r.dg_retrieval_ms:.0f}", f"{r.base_retrieval_ms:.0f}",
            str(r.dg_tool_calls), str(r.base_tool_calls),
            str(r.dg_llm_tokens_in), str(r.base_llm_tokens_in),
            f"{r.dg_llm_time_s:.1f}", f"{r.base_llm_time_s:.1f}",
            f"${r.dg_llm_cost:.4f}", f"${r.base_llm_cost:.4f}",
        )

    console.print(tbl)

    # 合计
    total_dg_time = sum(r.dg_retrieval_ms + r.dg_llm_time_s * 1000 for r in results)
    total_base_time = sum(r.base_retrieval_ms + r.base_llm_time_s * 1000 for r in results)
    total_dg_tokens = sum(r.dg_llm_tokens_in + r.dg_llm_tokens_out for r in results)
    total_base_tokens = sum(r.base_llm_tokens_in + r.base_llm_tokens_out for r in results)
    total_dg_cost = sum(r.dg_llm_cost for r in results)
    total_base_cost = sum(r.base_llm_cost for r in results)
    total_dg_tools = sum(r.dg_tool_calls for r in results)
    total_base_tools = sum(r.base_tool_calls for r in results)

    console.print()
    console.print("[bold]总计[/bold]")
    console.print(f"  DocGraph: 总耗时={total_dg_time/1000:.1f}s  工具调用={total_dg_tools}  Token={total_dg_tokens}  成本=${total_dg_cost:.4f}")
    console.print(f"  Base:     总耗时={total_base_time/1000:.1f}s  工具调用={total_base_tools}  Token={total_base_tokens}  成本=${total_base_cost:.4f}")
    console.print(f"  Token 节省: {(1 - total_dg_tokens / max(total_base_tokens, 1)) * 100:.0f}%  成本节省: {(1 - total_dg_cost / max(total_base_cost, 0.0001)) * 100:.0f}%")


# ═══════════════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    root = project_root_from_cwd()
    dg_dir = root / ".docgraph"
    if not dg_dir.is_dir():
        console.print("[red]No .docgraph/ found.[/red]")
        sys.exit(1)

    autoload_env(root)
    cfg = load_config(root)
    set_level(cfg.logging.level)

    # ── 初始化 DocGraph ──
    store = SQLiteGraphStore(dg_dir / "graph.db")
    store.init_schema()
    vstore = build_vector_store(cfg.storage, dg_dir, create=False)
    encoder = None
    if vstore:
        vstore.init_schema()
        encoder = build_encoder(cfg.embeddings)
    qe = QueryEngine(store, vstore=vstore, encoder=encoder)
    dg_backend = DocGraphBackend(qe, store)

    # ── 初始化 Base ──
    base_backend = BaseBackend(dg_dir / "graph.db")

    # ── 初始化 LLM ──
    llm_client = _build_llm(root, cfg, dg_dir)

    console.print(f"[bold]Agent 对比测试[/bold]")
    console.print(f"  文档数: {len(store.list_docs())}  节点: {store.count_nodes()}  边: {store.count_edges()}")
    console.print(f"  语义检索: {'✅' if encoder else '❌'}  LLM: {cfg.llm.provider}/{cfg.llm.tiers.balanced}")
    if llm_client is None:
        console.print("[red]LLM 未配置，无法运行对比测试。请先配置 llm.enabled + provider/API key。[/red]")
        store.close()
        base_backend.close()
        sys.exit(1)
    console.print(_HD)

    results: list[CaseResult] = []
    for case in CASES:
        console.print(f"[dim]测试 #{case['id']} ({case['title']})...[/dim]", end="\r")
        r = run_one_case(case, dg_backend, base_backend, llm_client)
        results.append(r)
        console.print(" " * 40, end="\r")

    for r in results:
        print_case_detail(r)
    print_summary_table(results)

    # ── 导出完整 JSON ──
    export_path = root / ".docgraph" / "agent_comparison_results.json"
    export_data = {
        "config": {
            "provider": cfg.llm.provider,
            "model": cfg.llm.tiers.balanced,
            "semantic_search": encoder is not None,
            "docs": store.list_docs(),
            "total_nodes": store.count_nodes(),
            "total_edges": store.count_edges(),
        },
        "cases": [],
        "totals": {
            "dg_total_time_s": sum(r.dg_retrieval_ms + r.dg_llm_time_s * 1000 for r in results) / 1000,
            "base_total_time_s": sum(r.base_retrieval_ms + r.base_llm_time_s * 1000 for r in results) / 1000,
            "dg_total_tools": sum(r.dg_tool_calls for r in results),
            "base_total_tools": sum(r.base_tool_calls for r in results),
            "dg_total_tokens": sum(r.dg_llm_tokens_in + r.dg_llm_tokens_out for r in results),
            "base_total_tokens": sum(r.base_llm_tokens_in + r.base_llm_tokens_out for r in results),
            "dg_total_cost": sum(r.dg_llm_cost for r in results),
            "base_total_cost": sum(r.base_llm_cost for r in results),
        },
    }
    for r in results:
        export_data["cases"].append({
            "id": r.case_id,
            "title": r.title,
            "question": r.question,
            "docgraph": {
                "retrieval_ms": r.dg_retrieval_ms,
                "tool_calls": r.dg_tool_calls,
                "tokens_in": r.dg_llm_tokens_in,
                "tokens_out": r.dg_llm_tokens_out,
                "llm_time_s": r.dg_llm_time_s,
                "cost_usd": r.dg_llm_cost,
                "cache_hit": r.dg_cache_hit,
                "retrieved": getattr(r, "_dg_retrieved", None),
                "answer": r.dg_answer,
            },
            "base": {
                "retrieval_ms": r.base_retrieval_ms,
                "tool_calls": r.base_tool_calls,
                "tokens_in": r.base_llm_tokens_in,
                "tokens_out": r.base_llm_tokens_out,
                "llm_time_s": r.base_llm_time_s,
                "cost_usd": r.base_llm_cost,
                "cache_hit": r.base_cache_hit,
                "retrieved": getattr(r, "_base_retrieved", None),
                "answer": r.base_answer,
            },
        })
    export_path.write_text(json.dumps(export_data, ensure_ascii=False, indent=2), "utf-8")
    console.print(f"\n[green]完整结果已导出: {export_path}[/green]")

    store.close()
    base_backend.close()


def _build_llm(root: Path, cfg, dg_dir: Path) -> LLMClient | None:
    """从 config 构建 LLM 客户端，逻辑同 pipeline._build_llm_client。"""
    if not cfg.llm.enabled:
        return None
    autoload_env(root)
    provider_name = cfg.llm.provider
    provider_cfg = cfg.llm.providers.get(provider_name)
    if provider_cfg is None:
        from docgraph.core.config import LLMProviderConfig
        if provider_name in ("openai_compat", "openai", "volces", "deepseek"):
            provider_cfg = LLMProviderConfig(api_key_env="OPENAI_API_KEY", base_url_env="OPENAI_BASE_URL")
        elif provider_name == "anthropic":
            provider_cfg = LLMProviderConfig(api_key_env="ANTHROPIC_API_KEY")
        else:
            console.print(f"[red]LLM provider '{provider_name}' not configured[/red]")
            return None
    api_key = provider_cfg.api_key or os.environ.get(provider_cfg.api_key_env)
    if not api_key:
        console.print(f"[red]{provider_cfg.api_key_env} not set[/red]")
        return None
    kwargs: dict[str, Any] = {"api_key_env": provider_cfg.api_key_env, "api_key": provider_cfg.api_key}
    if provider_name in ("openai", "openai_compat", "volces", "deepseek"):
        kwargs["base_url_env"] = provider_cfg.base_url_env
        kwargs["base_url"] = provider_cfg.base_url
    try:
        provider = make_provider(provider_name, **kwargs)
    except Exception as e:
        console.print(f"[red]LLM provider init failed: {e}[/red]")
        return None
    return LLMClient(
        provider,
        tiers={
            "fast": cfg.llm.tiers.fast,
            "balanced": cfg.llm.tiers.balanced,
            "accurate": cfg.llm.tiers.accurate,
        },
        cache_dir=dg_dir / "cache" / "llm",
        tracker=CostTracker(),
        budget_usd=None,
    )


if __name__ == "__main__":
    main()
