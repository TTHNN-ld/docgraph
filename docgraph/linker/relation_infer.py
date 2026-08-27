"""RelationInferLinker —— ADR-015 B 层：确定性关系推断。

在所有 extractor 落库后跑，查 store 里的实体节点，靠结构推断语义关系，
不调 LLM。当前覆盖：

- ``belongs_to``：实体 → 所属 section / module。section 归属从 source_block_ids
  回溯到 L0 block 的 section_path 恢复（实体节点自身的 section_path 常为空）。
  若同文档存在同名 module 节点（来自 figure VLM），优先连 module，否则连 section。
- ``contained_in``：memory_map → register。按地址前缀匹配（memory_map.base 是
  register.address 的前缀）。

设计原则（docs/architecture/knowledge-graph.md / RFC 0015）：
- 零 LLM 成本，高精度，补大半语义边。
- 失败不影响已有图谱；evidence 非空（ADR-008）。
- 幂等：upsert_edge 对 (src, dst, kind) 去重。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from docgraph.core.ids import normalize_name
from docgraph.core.logger import get_logger
from docgraph.graph.schema import Edge, EdgeKind, Evidence, Node, NodeKind
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.graph.traversal import iter_nodes

log = get_logger(__name__)


# 参与 belongs_to 推断的实体类型（容器/源类型本身不参与）。
_ENTITY_KINDS = (
    NodeKind.REGISTER,
    NodeKind.BITFIELD,
    NodeKind.SIGNAL,
    NodeKind.INTERRUPT,
    NodeKind.INTERFACE,
    NodeKind.MEMORY_MAP,
    NodeKind.CLOCK,
    NodeKind.PIN,
    NodeKind.PARAMETER,
    NodeKind.TERM,
    NodeKind.REQUIREMENT,
    NodeKind.ERRATA,
)


@dataclass
class RelationInferReport:
    belongs_to_edges: int = 0
    contained_in_edges: int = 0
    skipped_no_section: int = 0
    duration_s: float = 0.0


class RelationInferLinker:
    name = "relation_infer"
    version = "0.2"

    def run(self, store: SQLiteGraphStore) -> RelationInferReport:
        t0 = time.time()
        rep = RelationInferReport()

        # 1. 建 section 索引：(doc_id, section_path) -> section_node
        sections = iter_nodes(store, NodeKind.SECTION)
        section_by_key: dict[tuple[str, str], Node] = {}
        section_name_by_key: dict[tuple[str, str], str] = {}
        for s in sections:
            sp = _section_path_of(s)
            if not sp:
                continue
            key = (s.doc_id, sp)
            section_by_key[key] = s
            section_name_by_key[key] = s.name or ""

        # 2. 建 module 索引（来自 figure VLM）：(doc_id, normalize(name)) -> module_node
        modules = iter_nodes(store, NodeKind.MODULE)
        module_by_doc_name: dict[tuple[str, str], Node] = {}
        for m in modules:
            nm = normalize_name(m.qualified_name or m.name or "")
            if nm:
                module_by_doc_name[(m.doc_id, nm)] = m

        # 3. 对每个实体推断 belongs_to
        for kind in _ENTITY_KINDS:
            entities = iter_nodes(store, kind)
            for ent in entities:
                sp = _section_path_of(ent) or _recover_section_path(store, ent)
                if not sp:
                    rep.skipped_no_section += 1
                    continue
                key = (ent.doc_id, sp)
                section = section_by_key.get(key)
                if section is None:
                    rep.skipped_no_section += 1
                    continue
                # 优先连同名 module 节点（更语义），否则连 section
                target = self._match_module(
                    module_by_doc_name, ent.doc_id, section_name_by_key.get(key, "")
                )
                if target is None:
                    target = section
                store.upsert_edge(
                    Edge(
                        src=ent.id,
                        dst=target.id,
                        kind=EdgeKind.BELONGS_TO,
                        confidence=0.85 if target.kind == NodeKind.SECTION else 0.9,
                        evidence=Evidence(
                            pages=[ent.location.page] if ent.location and ent.location.page else [],
                            extractor=f"{self.name}@{self.version}",
                            raw_snippet=f"{ent.kind.value} in section {sp}",
                        ),
                        attrs={
                            "source": f"{self.name}@{self.version}",
                            "inferred_from": "section_context",
                        },
                    )
                )
                rep.belongs_to_edges += 1

        # 4. contained_in：memory_map → register（地址前缀匹配）
        rep.contained_in_edges = self._infer_contained_in(store)

        rep.duration_s = round(time.time() - t0, 3)
        log.info(
            f"[relation_infer] done in {rep.duration_s}s — "
            f"belongs_to={rep.belongs_to_edges} "
            f"contained_in={rep.contained_in_edges} "
            f"skipped(no_section)={rep.skipped_no_section}"
        )
        return rep

    @staticmethod
    def _match_module(
        module_by_doc_name: dict[tuple[str, str], Node],
        doc_id: str,
        section_name: str,
    ) -> Node | None:
        """若 section 标题恰好匹配一个 module 节点名，返回该 module。保守精确匹配。"""
        if not section_name:
            return None
        nm = normalize_name(section_name)
        if not nm:
            return None
        return module_by_doc_name.get((doc_id, nm))

    def _infer_contained_in(self, store: SQLiteGraphStore) -> int:
        """memory_map.base 是 register.address 的前缀 → contained_in。"""
        maps = iter_nodes(store, NodeKind.MEMORY_MAP)
        regs = iter_nodes(store, NodeKind.REGISTER)
        # 按文档分组 register 地址，避免 O(n*m)
        reg_addr_by_doc: dict[str, list[tuple[Node, str]]] = {}
        for r in regs:
            addr = _addr_of(r)
            if addr:
                reg_addr_by_doc.setdefault(r.doc_id, []).append((r, addr))
        n = 0
        for m in maps:
            base = _addr_of(m)
            if not base:
                continue
            for r, addr in reg_addr_by_doc.get(m.doc_id, []):
                if _addr_prefix(base, addr):
                    store.upsert_edge(
                        Edge(
                            src=m.id,
                            dst=r.id,
                            kind=EdgeKind.CONTAINED_IN,
                            confidence=0.95,
                            evidence=Evidence(
                                pages=[m.location.page] if m.location and m.location.page else [],
                                extractor=f"{self.name}@{self.version}",
                                raw_snippet=f"{m.name} base {base} covers {r.name} @ {addr}",
                            ),
                            attrs={
                                "source": f"{self.name}@{self.version}",
                                "inferred_from": "address_join",
                            },
                        )
                    )
                    n += 1
        return n


def _section_path_of(node) -> str | None:
    sp = getattr(node.location, "section_path", None) if node.location else None
    if sp:
        return sp
    attrs = getattr(node, "attrs", None) or {}
    sp = attrs.get("section_path")
    return sp if isinstance(sp, str) and sp else None


def _recover_section_path(store: SQLiteGraphStore, node) -> str | None:
    """恢复实体所属 section_path。

    优先级：
    1. 节点自身 location.section_path / attrs.section_path
    2. evidence.chunk_ids → chunk.section_id（最可靠，chunker 一定设了）
    3. source_block_ids → L0 block.section_path（table block 常为空，兜底）
    """
    sp = _section_path_of(node)
    if sp:
        return sp
    ev = getattr(node, "evidence", None)
    cids = getattr(ev, "chunk_ids", None) or []
    for cid in cids:
        try:
            ch = store.get_chunk(cid)
        except Exception:
            ch = None
            log.debug(f"[relation_infer] get_chunk failed for {cid}; continuing")
        if ch is not None and getattr(ch, "section_id", None):
            return ch.section_id
    attrs = getattr(node, "attrs", None) or {}
    block_ids = attrs.get("source_block_ids") or []
    if not block_ids:
        return None
    try:
        blocks = store.get_blocks(list(block_ids))
    except Exception as e:
        log.warning(
            f"[relation_infer] get_blocks failed for {node.id}: {e}; section recovery skipped"
        )
        return None
    for b in blocks:
        sp = getattr(b, "section_path", None)
        if sp:
            return sp
    return None


def _addr_of(node) -> str | None:
    attrs = getattr(node, "attrs", None) or {}
    for key in ("address", "base_address", "offset"):
        v = attrs.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    return None


def _addr_prefix(base: str, addr: str) -> bool:
    """register.address 落在 memory_map.base 的块内。

    base 的尾部 0 hex 位表示块边界（如 0x13800000 是 64KB 块），
    匹配前 (len - 尾部0数) 位，至少 4 位。
    """
    base = base.lower().replace("0x", "")
    addr = addr.lower().replace("0x", "")
    if len(base) < 4 or len(addr) < 4:
        return False
    trailing = len(base) - len(base.rstrip("0"))
    prefix_len = max(4, len(base) - trailing)
    return addr.startswith(base[:prefix_len])
