"""Typed L2 trust metadata and conservative fact promotion.

Extractors may be permissive when discovering candidates.  This module owns the
separate decision of whether an extracted node is safe to expose as a fact.
Metadata stays in ``attrs`` so existing SQLite databases and plugin contracts
remain compatible.
"""
from __future__ import annotations

from collections.abc import Iterable

from docgraph.graph.schema import (
    Derivation,
    DerivationConfidence,
    DerivationMethod,
    Edge,
    L2Status,
    Node,
    NodeKind,
    ValidationIssue,
    ValidationSeverity,
)

_FACT_ELIGIBLE_KINDS = {
    NodeKind.REGISTER,
    NodeKind.BITFIELD,
    NodeKind.PIN,
    NodeKind.SIGNAL,
    NodeKind.INTERFACE,
    NodeKind.INTERRUPT,
    NodeKind.MEMORY_MAP,
}


def set_l2_metadata(
    item: Node | Edge,
    *,
    status: L2Status,
    method: DerivationMethod,
    extractor: str,
    confidence: DerivationConfidence,
    verified: bool,
    issues: Iterable[ValidationIssue] = (),
) -> None:
    item.attrs["l2_status"] = status.value
    item.attrs["derivation"] = Derivation(
        method=method,
        extractor=extractor,
        confidence=confidence,
        verified=verified,
    ).model_dump(mode="json")
    item.attrs["validation_issues"] = [issue.model_dump(mode="json") for issue in issues]


def classify_extracted_node(
    node: Node,
    *,
    method: DerivationMethod,
    extractor: str,
) -> list[ValidationIssue]:
    """Classify a newly materialized node without losing uncertain output."""

    issues = fact_eligibility_issues(node, method=method)
    can_promote = node.kind in _FACT_ELIGIBLE_KINDS and not any(
        issue.severity == ValidationSeverity.ERROR for issue in issues
    )
    set_l2_metadata(
        node,
        status=L2Status.FACT if can_promote else L2Status.CANDIDATE,
        method=method,
        extractor=extractor,
        confidence=(
            DerivationConfidence.EXACT
            if can_promote
            else DerivationConfidence.MEDIUM
            if method == DerivationMethod.DETERMINISTIC
            else DerivationConfidence.LOW
        ),
        verified=can_promote,
        issues=issues,
    )
    return issues


def fact_eligibility_issues(
    node: Node,
    *,
    method: DerivationMethod,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if method not in {DerivationMethod.DETERMINISTIC, DerivationMethod.MANUAL}:
        issues.append(_error(
            "l2.fact.nondeterministic_source",
            "LLM/VLM inferred output must remain a candidate until independently verified.",
        ))
    if not node.evidence.extractor or node.evidence.extractor == "unknown":
        issues.append(_error("l2.fact.missing_evidence", "Fact requires a real evidence extractor."))
    if not node.attrs.get("source_block_ids"):
        issues.append(_error("l2.fact.missing_source_blocks", "Fact requires source_block_ids."))
    if not (node.attrs.get("source_chunk_ids") or node.evidence.chunk_ids):
        issues.append(_error("l2.fact.missing_source_chunks", "Fact requires source_chunk_ids."))

    if node.kind == NodeKind.BITFIELD:
        high = _as_int(node.attrs.get("bit_high"))
        low = _as_int(node.attrs.get("bit_low"))
        if not node.attrs.get("register_id"):
            issues.append(_error("l2.fact.missing_register_ref", "Bitfield requires register_id.", "register_id"))
        if high is None or low is None or low < 0 or high < low:
            issues.append(_error("l2.fact.invalid_bit_range", "Bitfield range is invalid.", "bit_high"))
    elif node.kind == NodeKind.MEMORY_MAP:
        if not any(node.attrs.get(key) not in (None, "") for key in ("address", "target", "size")):
            issues.append(_error(
                "l2.fact.missing_address_locator",
                "Memory-map fact requires address, target, or size.",
                "address",
            ))
    return issues


def classify_edge(
    edge: Edge,
    *,
    method: DerivationMethod,
    extractor: str,
    verified: bool,
) -> None:
    set_l2_metadata(
        edge,
        status=L2Status.FACT if verified else L2Status.CANDIDATE,
        method=method,
        extractor=extractor,
        confidence=DerivationConfidence.EXACT if verified else DerivationConfidence.LOW,
        verified=verified,
    )


def _error(code: str, message: str, field: str | None = None) -> ValidationIssue:
    return ValidationIssue(code=code, message=message, field=field)


def _as_int(value) -> int | None:
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip(), 0)
    except (TypeError, ValueError):
        return None
