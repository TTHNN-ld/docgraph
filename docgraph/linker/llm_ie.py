"""LLMIELinker —— ADR-015 C 层：LLM 开放 IE 抽语义关系。

在 B 层（确定性关系推断）之后跑，补 B 覆盖不到的语义关系（mapped_to /
drives / clocks / resets / implements 等）。GraphRAG 式但**约束在本体关系类型**
（IP-XACT 对齐），evidence 必填，confidence 门槛。

设计：
- 按文档建实体名索引（normalize(name) -> node），LLM 抽出的实体按名匹配现有节点。
- **保守**：只在已存在的两个实体间建边，不新建实体节点（避免幻觉/重复）。
- **成本控制**：只对提到 ≥2 个已知实体名的文本 chunk 调 LLM；每文档调用上限。
- 受 llm.enabled 控制；缓存 + 成本追踪 + 失败优雅降级（ADR-007）。
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from functools import partial

from pydantic import BaseModel, Field

from docgraph.core.ids import make_node_id, normalize_name
from docgraph.core.concurrency import map_concurrent, llm_concurrency
from docgraph.core.logger import get_logger
from docgraph.graph.schema import Edge, EdgeKind, Evidence, Location, Node, NodeKind
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.graph.store import NodeQuery

log = get_logger(__name__)


# 本体约束：LLM 只能输出这些关系类型（IP-XACT 对齐，ADR-015）。
_RELATION_TO_EDGEKIND: dict[str, EdgeKind] = {
    "belongs_to": EdgeKind.BELONGS_TO,
    "contained_in": EdgeKind.CONTAINED_IN,
    "mapped_to": EdgeKind.MAPPED_TO,
    "drives": EdgeKind.DRIVES,
    "clocks": EdgeKind.CLOCKS,
    "resets": EdgeKind.RESETS,
    "implements": EdgeKind.IMPLEMENTS,
    "connects_to": EdgeKind.CONNECTS_TO,
    "controls": EdgeKind.CONTROLS,
    "depends_on": EdgeKind.DEPENDS_ON,
    "references": EdgeKind.REFERENCES,
}

# 参与 name 索引的实体类型。不含 SECTION（section 归属由 B 层 belongs_to 处理，
# LLM IE 把 section 当端点会产出 "controls → About this document" 这种噪声）。
_ENTITY_KINDS = (
    NodeKind.REGISTER, NodeKind.BITFIELD, NodeKind.SIGNAL, NodeKind.INTERRUPT,
    NodeKind.INTERFACE, NodeKind.MEMORY_MAP, NodeKind.CLOCK, NodeKind.PIN,
    NodeKind.PARAMETER, NodeKind.MODULE,
)

# LLM 输出的 src_type/dst_type 字符串 → NodeKind
_KIND_MAP: dict[str, NodeKind] = {
    "register": NodeKind.REGISTER, "bitfield": NodeKind.BITFIELD,
    "signal": NodeKind.SIGNAL,     "interrupt": NodeKind.INTERRUPT,
    "interface": NodeKind.INTERFACE, "memory_map": NodeKind.MEMORY_MAP,
    "clock": NodeKind.CLOCK,       "pin": NodeKind.PIN,
    "parameter": NodeKind.PARAMETER, "module": NodeKind.MODULE,
}

# 样板章节/通用标题，不该作为语义关系端点。
_BOILERPLATE = {
    "about this document", "introduction", "overview", "preface",
    "contents", "table of contents", "revision history", "references",
    "figures", "tables", "abbreviations", "glossary", "appendix",
    "related documentation", "conventions",
}

_CJK_RE = re.compile(r"[一-鿿]")


def _is_matchable_name(name: str) -> bool:
    """名字是否像真实实体名（过滤掉被误当名字的句子/样板标题）。

    - 长度 2-30
    - 词数 <= 4（句子排除）
    - CJK 字符 <= 8（中文长句排除；真实中文实体名都很短）
    - 不在样板标题黑名单
    """
    n = (name or "").strip()
    if not (2 <= len(n) <= 30):
        return False
    if len(n.split()) > 4:
        return False
    if len(_CJK_RE.findall(n)) > 8:
        return False
    if n.lower() in _BOILERPLATE:
        return False
    return True

_RELATION_NAMES = ", ".join(sorted(_RELATION_TO_EDGEKIND))

# 模糊匹配时忽略的噪声词（冠词/通用词）。
_NOISE_TOKENS = {"the", "a", "an", "of", "for", "and", "to", "in", "on", "with"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _tokens(name: str) -> set[str]:
    """名称拆成小写 token 集合，去噪声词和过短 token。"""
    out: set[str] = set()
    for raw in (name or "").lower().replace("_", " ").split():
        t = "".join(ch for ch in raw if ch.isalnum())
        if len(t) < 2 or t in _NOISE_TOKENS:
            continue
        out.add(t)
    return out


_ENTITY_TYPE_NAMES = ", ".join(sorted(_KIND_MAP))

class LLMIERelation(BaseModel):
    src: str = Field(description="源实体名（原文）")
    src_type: str | None = Field(default=None, description=f"源实体类型，只取: {_ENTITY_TYPE_NAMES}")
    relation: str = Field(description=f"关系类型，取值: {_RELATION_NAMES}")
    dst: str = Field(description="目标实体名（原文）")
    dst_type: str | None = Field(default=None, description=f"目标实体类型，只取: {_ENTITY_TYPE_NAMES}")
    confidence: float = 0.7


class LLMIEResult(BaseModel):
    relations: list[LLMIERelation] = Field(default_factory=list)


def _make_ie_node(
    name: str,
    kind: NodeKind,
    doc_id: str,
    chunk,
    confidence: float,
    *,
    pending: bool,
) -> Node:
    """LLM IE 发现的新实体节点。pending=True 时标记待人工校验。"""
    attrs: dict = {
        "source": "llm_ie@0.1",
        "inferred_from": "llm_ie",
        "llm_confidence": confidence,
        "source_chunk_ids": [chunk.id],
        "name": name,
    }
    if pending:
        attrs["status"] = "pending"
    node_id = make_node_id(
        "ie", kind, normalize_name(name), doc_id=doc_id,
    )
    return Node(
        id=node_id,
        kind=kind,
        name=name,
        qualified_name=name,
        doc_id=doc_id,
        page=chunk.page,
        location=Location(page=chunk.page),
        evidence=Evidence(
            chunk_ids=[chunk.id],
            pages=[chunk.page] if chunk.page else [],
            extractor="llm_ie@0.1",
            raw_snippet=name,
        ),
        attrs=attrs,
        summary=f"LLM-IE {kind.value}: {name}"[:120],
    )


@dataclass
class LLMIEReport:
    llm_calls: int = 0
    edges_created: int = 0
    entities_created: int = 0
    entities_pending: int = 0
    skipped_no_match: int = 0
    skipped_req: int = 0
    failed: int = 0
    fallback_calls: int = 0
    chunks_scanned: int = 0
    duration_s: float = 0.0


class LLMIELinker:
    name = "llm_ie"
    version = "0.1"

    MAX_CHUNKS_PER_DOC = 30
    MIN_CHUNK_CHARS = 80
    MIN_ENTITIES_MENTIONED = 2  # chunk 至少提到 2 个已知实体才调 LLM
    CONFIDENCE_THRESHOLD = 0.6
    # REQ_ 需求条目密集的 chunk 跳过：text_entity 已抽成 requirement 节点，
    # 且需求语句（"X 使用 Y"）映射不上 belongs_to/drives 这类本体关系，
    # deepseek-v4-pro 在这类 chunk 上会纠结推理烧光 max_tokens 返回空。
    REQ_SKIP_THRESHOLD = 3
    _REQ_RE = re.compile(r"REQ_[A-Z0-9_]*\d+", re.I)

    def run(self, store: SQLiteGraphStore, llm_client=None) -> LLMIEReport:
        t0 = time.time()
        rep = LLMIEReport()
        if os.environ.get("DOCGRAPH_LLM_IE", "").lower() in ("off", "0", "false", "no"):
            log.info("[llm_ie] skipped (DOCGRAPH_LLM_IE=off)")
            return rep
        if llm_client is None or getattr(llm_client, "disabled", False):
            log.info("[llm_ie] skipped (no LLM configured)")
            return rep

        name_index = self._build_name_index(store)
        all_chunks = store.list_chunks(limit=1000000)

        for doc_id in store.list_docs():
            doc_chunks = [
                c for c in all_chunks
                if c.doc_id == doc_id
                and (c.kind == "section")
                and len(c.text or "") >= self.MIN_CHUNK_CHARS
            ]
            if not doc_chunks:
                continue
            idx = name_index.get(doc_id, {})
            if not idx:
                continue
            n_calls = 0
            max_chunks = _env_int("DOCGRAPH_LLM_IE_MAX_CHUNKS_PER_DOC", self.MAX_CHUNKS_PER_DOC)
            # Phase 1: 过滤 + 收集要调 LLM 的 chunk（顺序，无 store 写）
            tasks: list = []  # (chunk, entity_names)
            for chunk in doc_chunks:
                if len(tasks) >= max_chunks:
                    break
                rep.chunks_scanned += 1
                if self._is_requirement_heavy(chunk.text or ""):
                    rep.skipped_req += 1
                    continue
                mentioned = self._entities_mentioned(chunk.text or "", idx)
                if len(mentioned) < self.MIN_ENTITIES_MENTIONED:
                    continue
                entity_names = self._entity_names_for_prompt(mentioned, idx)
                tasks.append((chunk, entity_names))
            if not tasks:
                continue
            log.info(
                f"[llm_ie] doc={doc_id[:30]}: {len(tasks)} chunks -> LLM "
                f"(concurrency={llm_concurrency()})"
            )
            # Phase 2: 并发调 LLM（不同 chunk 互相独立，不碰 store）
            results = map_concurrent(
                partial(self._run_extract, llm_client=llm_client), tasks
            )
            # Phase 3: 顺序建边/建实体（store 写入不并发，避免 sqlite 锁冲突）
            for (chunk, _names), res in zip(tasks, results):
                result, err = res
                if err is not None:
                    rep.failed += 1
                    log.warning(f"[llm_ie] extract failed for {chunk.id}: {err}")
                    continue
                rep.llm_calls += 1
                rep.fallback_calls += getattr(result, "_fallback_calls", 0)
                for rel in result.relations:
                    ok, created, pending = self._create_edge(
                        store, rel, chunk, idx, doc_id,
                    )
                    if ok:
                        rep.edges_created += 1
                    else:
                        rep.skipped_no_match += 1
                    rep.entities_created += created
                    rep.entities_pending += pending
                n_calls += 1

        rep.duration_s = round(time.time() - t0, 3)
        log.info(
            f"[llm_ie] done in {rep.duration_s}s — llm_calls={rep.llm_calls} "
            f"fallback_calls={rep.fallback_calls} "
            f"edges_created={rep.edges_created} entities_created={rep.entities_created} "
            f"entities_pending={rep.entities_pending} "
            f"skipped={rep.skipped_no_match} skipped_req={rep.skipped_req} failed={rep.failed}"
        )
        return rep

    @classmethod
    def _is_requirement_heavy(cls, text: str) -> bool:
        """REQ_ 需求条目密集的 chunk（≥ REQ_SKIP_THRESHOLD 个编号）-> 跳过。

        text_entity 已把 REQ_ 抽成 requirement 节点；需求语句映射不上本体关系，
        且 deepseek-v4-pro 在这类 chunk 上会纠结推理烧光 max_tokens 返回空。
        """
        return len(cls._REQ_RE.findall(text)) >= cls.REQ_SKIP_THRESHOLD

    def _run_extract(self, task, *, llm_client):
        """单个 chunk 的 LLM 抽取（供并发）。返回 (result, err)。不碰 store。"""
        chunk, entity_names = task
        try:
            result = self._extract(llm_client, chunk, entity_names)
            return (result, None)
        except Exception as e:
            return (None, e)

    @staticmethod
    def _build_name_index(store: SQLiteGraphStore) -> dict[str, dict[str, list]]:
        """{doc_id: {normalize(name): [node, ...]}}。只索引名字像真实实体的节点。"""
        out: dict[str, dict[str, list]] = {}
        for kind in _ENTITY_KINDS:
            nodes = store.search_nodes(NodeQuery(kind=kind, limit=1000000))
            for n in nodes:
                raw = n.qualified_name or n.name or ""
                if not _is_matchable_name(raw):
                    continue
                nm = normalize_name(raw)
                if not nm:
                    continue
                out.setdefault(n.doc_id, {}).setdefault(nm, []).append(n)
        return out

    @staticmethod
    def _entities_mentioned(text: str, idx: dict[str, list]) -> set[str]:
        """返回 chunk 文本中提到的已知实体名集合（归一化匹配，忽略大小写）。

        单 token 实体（如 CTRL/IRQ）用整词匹配防子串误命中；多 token 实体
        （如 DMA_Engine）保留子串匹配以兼容下划线/空格差异。
        """
        if not text or not idx:
            return set()
        # 整词集合（单 token 匹配用）
        words = {w for w in re.findall(r"\w+", text.lower())}
        low = text.lower().replace(" ", "")
        mentioned: set[str] = set()
        for nm in idx:
            if len(nm) < 3:
                continue
            nml = nm.lower().replace("_", "")
            # 单 token：必须作为完整单词出现
            if "_" not in nm and " " not in nm:
                if nml in words:
                    mentioned.add(nm)
            elif nml in low:
                # 多 token：保留子串匹配
                mentioned.add(nm)
        return mentioned

    @staticmethod
    def _entity_names_for_prompt(mentioned: set[str], idx: dict[str, list]) -> list[str]:
        """Use canonical existing node names to constrain the LLM output."""
        names: list[str] = []
        for nm in sorted(mentioned):
            nodes = idx.get(nm) or []
            if not nodes:
                continue
            raw = nodes[0].qualified_name or nodes[0].name or ""
            if raw:
                names.append(raw)
        return names[:40]

    def _extract(self, llm_client, chunk, entity_names: list[str]) -> LLMIEResult:
        try:
            return self._extract_once(
                llm_client,
                chunk,
                entity_names,
                max_tokens=_env_int("DOCGRAPH_LLM_IE_MAX_TOKENS", 2048),
                fallback=False,
            )
        except Exception as first_error:
            msg = str(first_error).lower()
            retryable = (
                "empty llm response" in msg
                or "no json object" in msg
                or "did not produce valid json" in msg
                or "extra data" in msg
            )
            if not retryable:
                raise
            result = self._extract_once(
                llm_client,
                chunk,
                entity_names,
                max_tokens=_env_int("DOCGRAPH_LLM_IE_FALLBACK_MAX_TOKENS", 4096),
                fallback=True,
            )
            object.__setattr__(result, "_fallback_calls", 1)
            return result

    def _extract_once(
        self,
        llm_client,
        chunk,
        entity_names: list[str],
        *,
        max_tokens: int,
        fallback: bool,
    ) -> LLMIEResult:
        entity_text = "\n".join(f"- {name}" for name in entity_names) or "- (none)"
        fallback_note = (
            "This is a retry after an invalid/empty response. Return the smallest valid JSON object; "
            "use {\"relations\": []} if no relation is explicit.\n"
            if fallback else ""
        )
        prompt = (
            f"{fallback_note}"
            "你是芯片 spec 语义关系抽取器。从下面文本中抽取**已明确出现**的实体间语义关系，"
            "不要臆测。如果实体在候选列表中，请用列表中的原名；如果在文本中明确出现了新实体"
            "（非候选列表），请在 src_type/dst_type 字段指定其类型，系统会自动创建。\n"
            f"关系类型只能取: {_RELATION_NAMES}\n"
            f"实体类型只能取: {_ENTITY_TYPE_NAMES}\n"
            "没有明确关系就返回空数组。\n"
            "严格输出 JSON 对象，不要 markdown、不要解释：\n"
            '{"relations": [{"src": "实体名", "src_type": null, "relation": "关系类型", "dst": "实体名", "dst_type": null, "confidence": 0.0-1.0}]}\n\n'
            f"候选实体：\n{entity_text}\n\n"
            f"文本：\n{chunk.text[:3000]}"
        )
        return llm_client.json(
            prompt, schema=LLMIEResult, extractor=self.name,
            max_tokens=max_tokens, temperature=0.0,
            # DeepSeek V4 等推理模型：关掉 thinking，避免推理吃光 max_tokens 导致 content 空
            extra_body={"enable_thinking": False},
        )

    @staticmethod
    def _find_node(name: str, idx: dict[str, list]):
        """按名找实体节点：先精确 normalize 匹配，再退化到 token 重叠模糊匹配。

        处理 LLM 抽出的 'the DMA engine' 匹到节点 'DMA Engine' 这种情况。
        """
        if not name:
            return None
        nm = normalize_name(name)
        if nm and nm in idx:
            return idx[nm][0]
        target = _tokens(nm)
        if not target:
            return None
        best = None
        best_score = 0.0
        for key, nodes in idx.items():
            cand = _tokens(key)
            if not cand:
                continue
            overlap = len(target & cand)
            if overlap == 0:
                continue
            score = overlap / min(len(target), len(cand))
            # 收紧：要求重叠率 >= 0.75，避免 token 部分重合导致的误连
            if score > best_score and score >= 0.75:
                best_score = score
                best = nodes[0]
        return best

    def _create_edge(
        self, store: SQLiteGraphStore, rel: LLMIERelation,
        chunk, idx: dict[str, list], doc_id: str,
    ) -> tuple[bool, int, int]:
        """建语义边。src/dst 匹配不到时尝试创建新实体。

        Returns (edge_ok, entities_created, entities_pending).
        """
        rel_kind = _RELATION_TO_EDGEKIND.get(rel.relation.strip().lower().replace(" ", "_"))
        if rel_kind is None:
            return False, 0, 0
        if rel.confidence < self.CONFIDENCE_THRESHOLD:
            return False, 0, 0
        src, sc, sp = self._get_or_create_node(
            store, rel.src, rel.src_type, rel.confidence, chunk, doc_id, idx,
        )
        dst, dc, dp = self._get_or_create_node(
            store, rel.dst, rel.dst_type, rel.confidence, chunk, doc_id, idx,
        )
        if src is None or dst is None or src.id == dst.id:
            return False, sc + dc, sp + dp
        store.upsert_edge(Edge(
            src=src.id,
            dst=dst.id,
            kind=rel_kind,
            confidence=max(0.0, min(rel.confidence, 1.0)),
            evidence=Evidence(
                chunk_ids=[chunk.id],
                pages=[chunk.page] if chunk.page else [],
                extractor=f"{self.name}@{self.version}",
                raw_snippet=f"{rel.src} {rel.relation} {rel.dst}",
            ),
            attrs={
                "source": f"{self.name}@{self.version}",
                "inferred_from": "llm_ie",
                "llm_confidence": rel.confidence,
            },
        ))
        return True, sc + dc, sp + dp

    def _get_or_create_node(
        self, store: SQLiteGraphStore, name: str, type_str: str | None,
        confidence: float, chunk, doc_id: str, idx: dict[str, list],
    ) -> tuple[Node | None, int, int]:
        """匹配已有实体，或按 confidence 创建新实体。

        Returns (node, created_count, pending_count).
        - confidence ≥ 0.8: 直接创建实体节点
        - confidence 0.6-0.8: 创建但标记 pending（待人工校验）
        - 匹配到已有实体时直接返回（不重复创建）
        """
        existing = self._find_node(name, idx)
        if existing is not None:
            return existing, 0, 0
        kind = _KIND_MAP.get((type_str or "").strip().lower()) if type_str else None
        if kind is None:
            return None, 0, 0
        # 名去重：同 doc 同 kind 下是否有归一化名相同的节点
        nm = normalize_name(name)
        if nm and nm in idx:
            for n in idx[nm]:
                if getattr(n, "kind", None) == kind and n.doc_id == doc_id:
                    return n, 0, 0
        if confidence >= 0.8:
            node = _make_ie_node(name, kind, doc_id, chunk, confidence, pending=False)
            store.upsert_node(node)
            idx.setdefault(nm, []).append(node)
            return node, 1, 0
        if confidence >= self.CONFIDENCE_THRESHOLD:
            node = _make_ie_node(name, kind, doc_id, chunk, confidence, pending=True)
            store.upsert_node(node)
            idx.setdefault(nm, []).append(node)
            return node, 0, 1
        return None, 0, 0
