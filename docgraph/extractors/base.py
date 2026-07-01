"""Extractor 协议与注册表。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from docgraph.graph.schema import ExtractResult, NodeKind, ParsedDoc

if TYPE_CHECKING:
    from docgraph.llm.client import LLMClient


class ExtractContext(BaseModel):
    """传给 extractor 的上下文。"""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    family: str = "default"
    cache_dir: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    llm_client: Any = None  # Optional[LLMClient]

    @property
    def has_llm(self) -> bool:
        return self.llm_client is not None


class Extractor(Protocol):
    name: str
    kinds: set[NodeKind]
    requires: set[str]

    def extract(self, doc: ParsedDoc, ctx: ExtractContext) -> ExtractResult: ...


class ExtractorRegistry:
    """运行时 extractor 注册表 + 拓扑排序。"""

    def __init__(self) -> None:
        self._extractors: dict[str, type[Extractor]] = {}

    def register(self, cls: type[Extractor]) -> None:
        self._extractors[cls.name] = cls

    def get(self, name: str) -> type[Extractor] | None:
        return self._extractors.get(name)

    def list_names(self) -> list[str]:
        return list(self._extractors)

    def resolve_order(self, enabled: list[str]) -> list[type[Extractor]]:
        """根据 requires 拓扑排序。"""
        selected = {n for n in enabled if n in self._extractors}
        out: list[type[Extractor]] = []
        done: set[str] = set()

        def visit(name: str, stack: set[str]) -> None:
            if name in done:
                return
            if name in stack:
                raise RuntimeError(f"Cyclic extractor dependency at {name}")
            cls = self._extractors.get(name)
            if cls is None:
                return
            stack.add(name)
            for dep in cls.requires:
                if dep in selected:
                    visit(dep, stack)
            stack.remove(name)
            done.add(name)
            out.append(cls)

        for name in enabled:
            visit(name, set())
        return out


registry = ExtractorRegistry()
