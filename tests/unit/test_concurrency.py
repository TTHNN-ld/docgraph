from __future__ import annotations

import time

import pytest

from docgraph.core.concurrency import llm_concurrency, map_concurrent


def test_map_concurrent_empty():
    assert map_concurrent(lambda x: x, []) == []


def test_map_concurrent_single():
    assert map_concurrent(lambda x: x * 2, [3]) == [6]


def test_map_concurrent_preserves_order():
    xs = list(range(20))
    assert map_concurrent(lambda x: x, xs) == xs


def test_map_concurrent_exception_propagates():
    def fail(x):
        if x == 3:
            raise ValueError("boom")
        return x

    with pytest.raises(ValueError, match="boom"):
        map_concurrent(fail, list(range(10)))


def test_map_concurrent_actually_concurrent():
    """Multiple sleep tasks should complete faster than sequential sum."""
    def sleepy(x):
        time.sleep(0.05)
        return x

    t0 = time.time()
    results = map_concurrent(sleepy, list(range(6)))
    elapsed = time.time() - t0
    assert results == list(range(6))
    # 6 × 50ms sequential = 300ms. Concurrent with 4 workers = ~100ms.
    # Use generous threshold to avoid flakiness.
    assert elapsed < 0.25, f"Concurrent 6-sleep took {elapsed:.2f}s, expected <0.25s"


def test_llm_concurrency_default():
    assert llm_concurrency() == 4


def test_llm_concurrency_from_env(monkeypatch):
    monkeypatch.setenv("DOCGRAPH_LLM_CONCURRENCY", "8")
    assert llm_concurrency() == 8


def test_llm_concurrency_non_integer_falls_back(monkeypatch):
    monkeypatch.setenv("DOCGRAPH_LLM_CONCURRENCY", "abc")
    assert llm_concurrency() == 4


def test_llm_concurrency_negative_floor_to_1(monkeypatch):
    monkeypatch.setenv("DOCGRAPH_LLM_CONCURRENCY", "-1")
    assert llm_concurrency() == 1  # max(1, -1) floors to 1


def test_llm_concurrency_zero_falls_back(monkeypatch):
    monkeypatch.setenv("DOCGRAPH_LLM_CONCURRENCY", "0")
    assert llm_concurrency() == 1  # max(1, 0) -> 1 (sequential, but not 0)


def test_map_concurrent_respects_explicit_workers():
    """Explicit max_workers=1 should be sequential (matching single-thread path)."""
    results = map_concurrent(lambda x: x, list(range(5)), max_workers=1)
    assert results == list(range(5))
