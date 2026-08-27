from __future__ import annotations

import threading

from docgraph.llm.client import CostTracker, LLMClient, LLMResponse


class CountingProvider:
    name = "counting"

    def __init__(self, base_url: str = "https://first.example/v1") -> None:
        self.calls = 0
        self.base_url = base_url

    def complete(self, prompt: str, *, model: str, **_kwargs) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            text=f"answer:{prompt}",
            model=model,
            tokens_in=10,
            tokens_out=5,
            cost_usd=0.01,
        )


def test_cached_response_remains_available_after_budget_is_spent(tmp_path) -> None:
    provider = CountingProvider()
    tracker = CostTracker()
    client = LLMClient(
        provider,
        {"balanced": "test-model"},
        cache_dir=tmp_path / "cache",
        tracker=tracker,
        budget_usd=0.1,
        max_retries=0,
    )

    first = client.complete("same prompt")
    tracker.cost_usd = 0.1
    second = client.complete("same prompt")

    assert provider.calls == 1
    assert first.cache_hit is False
    assert second.cache_hit is True


def test_cost_tracker_admits_only_one_concurrent_reservation() -> None:
    tracker = CostTracker()
    barrier = threading.Barrier(2)
    results: list[float | None] = []

    def reserve() -> None:
        barrier.wait()
        results.append(tracker.reserve(0.1, 0.06))

    threads = [threading.Thread(target=reserve) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(value is not None for value in results) == [False, True]
    assert tracker.reserved_usd == 0.06


def test_cache_is_scoped_to_provider_endpoint(tmp_path) -> None:
    first_provider = CountingProvider("https://first.example/v1")
    first = LLMClient(
        first_provider,
        {"balanced": "same-model"},
        cache_dir=tmp_path / "cache",
        max_retries=0,
    )
    first.complete("same prompt")

    second_provider = CountingProvider("https://second.example/v1")
    second = LLMClient(
        second_provider,
        {"balanced": "same-model"},
        cache_dir=tmp_path / "cache",
        max_retries=0,
    )
    response = second.complete("same prompt")

    assert response.cache_hit is False
    assert second_provider.calls == 1
