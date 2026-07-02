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
    NodeKind.INTERRUPT: "irq",
    NodeKind.CLOCK: "clk",
    NodeKind.POWER_DOMAIN: "pwr",
    NodeKind.MEMORY_MAP: "mmap",
    NodeKind.REQUIREMENT: "req",
    NodeKind.ERRATA: "err",
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


# ---------------------------------------------------------------------------
# chip_model 推断 —— 跨文档消歧判断"同一实例"用
# ---------------------------------------------------------------------------

# 文档名里的型号 token → 归一化小写标识符。
_CHIP_MODEL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"cortex[-_ ]]?m(\d+\+?)", re.I), r"cortex-m\1"),
    (re.compile(r"cortex[-_ ]]?r(\d+)", re.I), r"cortex-r\1"),
    (re.compile(r"cortex[-_ ]]?a(\d+)", re.I), r"cortex-a\1"),
    (re.compile(r"pcie[-_ ]?subsystem", re.I), r"pcie-subsystem"),
    (re.compile(r"pcie[-_ ]?spec", re.I), r"pcie-subsystem"),
    (re.compile(r"stm32f(\d+)", re.I), r"stm32f\1"),
]


def infer_chip_model(stem: str) -> str | None:
    """从文档名（或 doc_id 里的文档名段）推断芯片型号/IP 实例。

    用于跨文档消歧判断"同一实例"：同名实体只有 chip_model 相同才合并。
    推断不出返回 None；调用方应回退到 family（兼容旧项目）。
    """
    s = stem.lower()
    for pat, repl in _CHIP_MODEL_PATTERNS:
        m = pat.search(s)
        if m:
            return pat.sub(repl, m.group(0)).replace(" ", "").replace("_", "-")
    return None


def doc_name_from_doc_id(doc_id: str) -> str:
    """从 doc_id 提取文档名段。

    doc_id 形如 ``family::type::Doc Name`` 或 ``family::type@version``；
    返回最后一个 ``::`` 之后的部分（文档名）。
    """
    if not doc_id:
        return ""
    return doc_id.split("::")[-1]
