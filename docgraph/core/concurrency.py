"""并发执行辅助 -- LLM/VLM 调用并发提速。

LLM/VLM 调用是 I/O 密集（等远端响应），同一 extractor 内不同表/图/chunk 的
调用互相独立，可用线程池并发。CostTracker.record 已加锁，文件缓存 per-key 安全，
OpenAI SDK client 线程安全，因此并发是安全的。

并发度由 DOCGRAPH_LLM_CONCURRENCY 环境变量控制（默认 4），受 provider 限速约束。
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def llm_concurrency() -> int:
    """LLM/VLM 并发度。DOCGRAPH_LLM_CONCURRENCY，默认 4。<=1 表示顺序执行。"""
    try:
        v = int(os.environ.get("DOCGRAPH_LLM_CONCURRENCY", "4"))
    except ValueError:
        return 4
    return max(1, v)


def map_concurrent(
    fn: Callable[[T], R], items: list[T], max_workers: int | None = None
) -> list[R]:
    """并发执行 fn(item)，结果按输入顺序返回。max_workers<=1 或单元素时顺序执行。

    任何 item 抛异常会向上抛出（ThreadPoolExecutor.map 语义）。调用方如需容错，
    在 fn 内部 try/except 返回哨兵值。
    """
    if not items:
        return []
    workers = max_workers if max_workers is not None else llm_concurrency()
    if workers <= 1 or len(items) <= 1:
        return [fn(i) for i in items]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))
