"""Parser 协议与注册表。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from docgraph.graph.schema import DocMetadata, ParsedDoc


class ParseContext(BaseModel):
    doc_id: str
    cache_dir: Path | None = None
    metadata: DocMetadata = Field(default_factory=DocMetadata)
    options: dict[str, Any] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}


class Parser(Protocol):
    """Parser 接口。所有 parser 必须实现。"""

    name: str
    supports: set[str]

    def can_parse(self, path: Path) -> bool: ...
    def parse(self, path: Path, ctx: ParseContext) -> ParsedDoc: ...


class ParserRegistry:
    """运行时 parser 注册表。"""

    def __init__(self) -> None:
        self._parsers: dict[str, type[Parser]] = {}

    def register(self, parser_cls: type[Parser]) -> None:
        self._parsers[parser_cls.name] = parser_cls

    def get(self, name: str) -> type[Parser] | None:
        return self._parsers.get(name)

    def pick(self, path: Path, primary: str, fallback: list[str]) -> Parser:
        """按 primary → fallback 顺序找一个能解析的 parser。"""
        candidates = [primary, *fallback]
        for name in candidates:
            cls = self._parsers.get(name)
            if cls is None:
                continue
            inst = cls()
            if inst.can_parse(path):
                return inst
        raise RuntimeError(
            f"No parser available for {path} (tried: {candidates}, "
            f"registered: {list(self._parsers)})"
        )

    def list_names(self) -> list[str]:
        return list(self._parsers)


# 全局注册表
registry = ParserRegistry()
