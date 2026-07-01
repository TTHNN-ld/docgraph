"""ID 与 hash 工具。

DocGraph 的节点 ID 全局形式：

    <family>::<kind>:<qualified_name>[#<doc_id>]

例：
    stm32f407::reg:TIM1.CR1
    stm32f407::reg:TIM1.CR1#errata@rev3
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from docgraph.graph.schema import NodeKind

_KIND_SHORT: dict[NodeKind, str] = {
    NodeKind.DOCUMENT: "doc",
    NodeKind.SECTION: "sec",
    NodeKind.REGISTER: "reg",
    NodeKind.BITFIELD: "bf",
    NodeKind.PIN: "pin",
    NodeKind.SIGNAL: "sig",
    NodeKind.MODULE: "mod",
    NodeKind.INTERFACE: "if",
    NodeKind.PARAMETER: "param",
    NodeKind.FIGURE: "fig",
    NodeKind.TABLE: "tbl",
    NodeKind.FORMULA: "formula",
    NodeKind.CODEBLOCK: "code",
    NodeKind.TERM: "term",
    NodeKind.CHUNK: "chunk",
}

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.\-]+")


def normalize_name(name: str) -> str:
    """归一名称：去空白、统一大小写策略由调用方决定。"""
    return _SAFE_NAME.sub("_", name.strip())


def make_node_id(
    family: str,
    kind: NodeKind,
    qualified_name: str,
    doc_id: str | None = None,
) -> str:
    short = _KIND_SHORT.get(kind, kind.value)
    qn = normalize_name(qualified_name)
    base = f"{family}::{short}:{qn}"
    if doc_id:
        return f"{base}#{doc_id}"
    return base


def make_doc_id(family: str, doc_type: str, version: str | None = None) -> str:
    if version:
        return f"{family}::{doc_type}@{version}"
    return f"{family}::{doc_type}"


def file_hash(path: Path, algo: str = "sha256") -> str:
    """流式计算文件哈希。"""
    h = hashlib.new(algo)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return f"{algo}:{h.hexdigest()}"


def content_hash(data: str | bytes, algo: str = "sha256") -> str:
    h = hashlib.new(algo)
    h.update(data.encode("utf-8") if isinstance(data, str) else data)
    return f"{algo}:{h.hexdigest()}"
