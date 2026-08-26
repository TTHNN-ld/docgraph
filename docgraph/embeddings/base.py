"""EmbeddingProvider 协议与注册表。"""

from __future__ import annotations

from typing import Protocol


class EmbeddingProvider(Protocol):
    name: str
    dim: int
    model: str

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class EmbeddingRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, type[EmbeddingProvider]] = {}

    def register(self, cls: type[EmbeddingProvider]) -> None:
        self._providers[cls.name] = cls

    def get(self, name: str) -> type[EmbeddingProvider] | None:
        return self._providers.get(name)

    def list_names(self) -> list[str]:
        return list(self._providers)


registry = EmbeddingRegistry()
