"""Golden-set evaluation for L2 entities.

The evaluator compares expected entity names against persisted L2 nodes. It is
deliberately lightweight so teams can start with small hand-written JSON files
before investing in a full benchmark harness.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from docgraph.graph.schema import NodeKind
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.graph.store import NodeQuery


_KIND_ALIASES = {
    "registers": "register",
    "register": "register",
    "bitfields": "bitfield",
    "bitfield": "bitfield",
    "pins": "pin",
    "pin": "pin",
    "signals": "signal",
    "signal": "signal",
    "interfaces": "interface",
    "interface": "interface",
    "parameters": "parameter",
    "parameter": "parameter",
    "timing": "parameter",
    "interrupts": "interrupt",
    "interrupt": "interrupt",
    "memory_maps": "memory_map",
    "memory_map": "memory_map",
    "requirements": "requirement",
    "requirement": "requirement",
    "errata": "errata",
}


@dataclass(frozen=True)
class ExpectedEntity:
    kind: str
    name: str
    doc_id: str | None = None
    source: str | None = None

    @property
    def key(self) -> tuple[str, str, str | None]:
        return (self.kind, _norm_name(self.name), self.doc_id)

    @property
    def loose_key(self) -> tuple[str, str]:
        return (self.kind, _norm_name(self.name))


@dataclass
class KindEval:
    kind: str
    expected: int = 0
    actual: int = 0
    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    missing: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "expected": self.expected,
            "actual": self.actual,
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "false_negative": self.false_negative,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "missing": self.missing,
            "unexpected": self.unexpected,
        }


@dataclass
class L2EvalReport:
    ok: bool
    golden_path: str
    totals: dict[str, Any]
    by_kind: list[KindEval]
    expected_files: list[str]
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "golden_path": self.golden_path,
            "totals": self.totals,
            "by_kind": [row.as_dict() for row in self.by_kind],
            "expected_files": self.expected_files,
            "warnings": self.warnings,
        }


def eval_l2_golden(
    store: SQLiteGraphStore,
    golden_path: Path,
    *,
    kinds: list[str] | None = None,
    min_precision: float = 0.0,
    min_recall: float = 0.0,
) -> L2EvalReport:
    expected, files, warnings = load_expected_entities(golden_path, kinds=kinds)
    selected_kinds = sorted({entity.kind for entity in expected} | {_canonical_kind(k) for k in kinds or []})
    actual = _load_actual_entities(store, selected_kinds, expected)

    by_kind: list[KindEval] = []
    totals = {
        "expected": 0,
        "actual": 0,
        "true_positive": 0,
        "false_positive": 0,
        "false_negative": 0,
    }
    for kind in selected_kinds:
        row = _eval_kind(kind, expected, actual)
        by_kind.append(row)
        totals["expected"] += row.expected
        totals["actual"] += row.actual
        totals["true_positive"] += row.true_positive
        totals["false_positive"] += row.false_positive
        totals["false_negative"] += row.false_negative

    totals["precision"] = _safe_div(totals["true_positive"], totals["true_positive"] + totals["false_positive"])
    totals["recall"] = _safe_div(totals["true_positive"], totals["true_positive"] + totals["false_negative"])
    totals["f1"] = _f1(totals["precision"], totals["recall"])
    ok = totals["precision"] >= min_precision and totals["recall"] >= min_recall
    if not expected:
        ok = False
        warnings.append("no expected L2 entities found")
    return L2EvalReport(
        ok=ok,
        golden_path=str(golden_path),
        totals=totals,
        by_kind=by_kind,
        expected_files=[str(path) for path in files],
        warnings=warnings,
    )


def load_expected_entities(
    golden_path: Path,
    *,
    kinds: list[str] | None = None,
) -> tuple[list[ExpectedEntity], list[Path], list[str]]:
    allowed = {_canonical_kind(kind) for kind in kinds or []}
    warnings: list[str] = []
    files = _expected_files(golden_path)
    out: list[ExpectedEntity] = []
    for path in files:
        try:
            data = json.loads(path.read_text("utf-8"))
        except Exception as exc:
            warnings.append(f"failed to read {path}: {exc}")
            continue
        inferred_kind = _kind_from_filename(path)
        out.extend(_entities_from_json(data, inferred_kind=inferred_kind, source=path, warnings=warnings))
    if allowed:
        out = [entity for entity in out if entity.kind in allowed]
    return out, files, warnings


def _expected_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.exists():
        return []
    files = list(path.rglob("expected_*.json"))
    files.extend(path.rglob("l2_expected.json"))
    return sorted(dict.fromkeys(files))


def _entities_from_json(
    data: Any,
    *,
    inferred_kind: str | None,
    source: Path,
    warnings: list[str],
) -> list[ExpectedEntity]:
    out: list[ExpectedEntity] = []
    if isinstance(data, list):
        if inferred_kind is None:
            warnings.append(f"{source} is a list but kind cannot be inferred")
            return out
        for item in data:
            entity = _entity_from_item(item, inferred_kind, source)
            if entity:
                out.append(entity)
        return out
    if not isinstance(data, dict):
        warnings.append(f"{source} must contain a JSON object or list")
        return out

    if "kind" in data and ("name" in data or "symbol" in data):
        entity = _entity_from_item(data, _canonical_kind(str(data["kind"])), source)
        return [entity] if entity else []

    for key, value in data.items():
        kind = _canonical_kind(key)
        if kind not in {item.value for item in NodeKind}:
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            entity = _entity_from_item(item, kind, source)
            if entity:
                out.append(entity)
    return out


def _entity_from_item(item: Any, kind: str, source: Path) -> ExpectedEntity | None:
    if isinstance(item, str):
        name = item
        doc_id = None
    elif isinstance(item, dict):
        name = item.get("name") or item.get("symbol") or item.get("qualified_name") or item.get("id")
        doc_id = item.get("doc_id")
    else:
        return None
    if not name:
        return None
    return ExpectedEntity(kind=kind, name=str(name), doc_id=doc_id, source=str(source))


def _load_actual_entities(
    store: SQLiteGraphStore,
    kinds: list[str],
    expected: list[ExpectedEntity],
) -> list[ExpectedEntity]:
    expected_docs_by_kind: dict[str, set[str]] = defaultdict(set)
    for entity in expected:
        if entity.doc_id:
            expected_docs_by_kind[entity.kind].add(entity.doc_id)

    actual: list[ExpectedEntity] = []
    for kind in kinds:
        node_kind = NodeKind(kind)
        nodes = store.search_nodes(NodeQuery(kind=node_kind, limit=100000))
        scoped_docs = expected_docs_by_kind.get(kind) or set()
        for node in nodes:
            if scoped_docs and node.doc_id not in scoped_docs:
                continue
            actual.append(ExpectedEntity(kind=kind, name=node.name, doc_id=node.doc_id))
    return actual


def _eval_kind(kind: str, expected: list[ExpectedEntity], actual: list[ExpectedEntity]) -> KindEval:
    expected_rows = [row for row in expected if row.kind == kind]
    actual_rows = [row for row in actual if row.kind == kind]
    expected_exact = {row.key for row in expected_rows if row.doc_id}
    actual_exact = {row.key for row in actual_rows if row.doc_id}
    expected_loose = {row.loose_key for row in expected_rows if not row.doc_id}
    actual_loose = {row.loose_key for row in actual_rows}

    exact_tp = expected_exact & actual_exact
    loose_tp = expected_loose & actual_loose
    tp = len(exact_tp) + len(loose_tp)
    expected_total = len(expected_exact) + len(expected_loose)
    actual_keys = actual_exact if expected_exact and not expected_loose else actual_loose
    actual_total = len(actual_keys)
    fp = max(0, actual_total - tp)
    fn = max(0, expected_total - tp)
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    row = KindEval(
        kind=kind,
        expected=expected_total,
        actual=actual_total,
        true_positive=tp,
        false_positive=fp,
        false_negative=fn,
        precision=precision,
        recall=recall,
        f1=_f1(precision, recall),
    )
    missing = _missing_samples(expected_rows, actual_exact, actual_loose)
    unexpected = _unexpected_samples(actual_rows, expected_exact, expected_loose)
    row.missing = missing[:20]
    row.unexpected = unexpected[:20]
    return row


def _missing_samples(
    expected: list[ExpectedEntity],
    actual_exact: set[tuple[str, str, str | None]],
    actual_loose: set[tuple[str, str]],
) -> list[str]:
    out: list[str] = []
    for entity in expected:
        matched = entity.key in actual_exact if entity.doc_id else entity.loose_key in actual_loose
        if not matched:
            out.append(_display(entity))
    return out


def _unexpected_samples(
    actual: list[ExpectedEntity],
    expected_exact: set[tuple[str, str, str | None]],
    expected_loose: set[tuple[str, str]],
) -> list[str]:
    out: list[str] = []
    for entity in actual:
        if expected_exact and not expected_loose:
            matched = entity.key in expected_exact
        else:
            matched = entity.loose_key in expected_loose
        if not matched:
            out.append(_display(entity))
    return out


def _display(entity: ExpectedEntity) -> str:
    return f"{entity.name} ({entity.doc_id})" if entity.doc_id else entity.name


def _kind_from_filename(path: Path) -> str | None:
    stem = path.stem
    if stem.startswith("expected_"):
        return _canonical_kind(stem.removeprefix("expected_"))
    return None


def _canonical_kind(value: str) -> str:
    key = value.strip().lower().replace("-", "_")
    key = re.sub(r"[^a-z0-9_]+", "_", key)
    return _KIND_ALIASES.get(key, key)


def _norm_name(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


def _safe_div(num: int | float, den: int | float) -> float:
    return round(float(num) / float(den), 4) if den else 0.0


def _f1(precision: float, recall: float) -> float:
    return round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0
