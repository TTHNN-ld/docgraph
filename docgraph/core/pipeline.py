"""Build pipeline for L0/L1 construction and optional L2 enrichment."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from docgraph.core.config import DocGraphConfig
from docgraph.core.dependencies import (
    DependencyPolicy,
    DependencyResult,
    ensure_parser_dependency,
    parser_dependency,
)
from docgraph.core.dotenv import autoload_env
from docgraph.core.ids import file_hash, infer_chip_model, make_doc_id
from docgraph.core.logger import get_logger
from docgraph.core.manifest import FileRecord, Manifest, StageRecord, save_manifest
from docgraph.embeddings.factory import build_encoder
from docgraph.embeddings.indexer import embed_graph
from docgraph.embeddings.vector_factory import build_vector_store
from docgraph.extractors.base import ExtractContext
from docgraph.extractors.base import registry as extractor_registry
from docgraph.graph.schema import DocMetadata, DocType, ExtractResult, ParsedDoc
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.linker.runner import run_linker
from docgraph.llm.client import CostTracker, LLMClient, make_provider
from docgraph.llm.vlm import VLMClient, make_vlm_provider
from docgraph.parsers.base import ParseContext
from docgraph.parsers.base import registry as parser_registry
from docgraph.parsers.pdf_router import assess_pdf_parse, inspect_pdf, pdf_parser_chain

log = get_logger(__name__)


@dataclass
class BuildReport:
    quality: str = "balanced"
    total_files: int = 0
    skipped: int = 0
    parsed: int = 0
    extracted: int = 0
    errors: int = 0
    degraded: int = 0
    nodes_total: int = 0
    edges_total: int = 0
    blocks_total: int = 0
    chunks_total: int = 0
    linker_edges: int = 0
    embedded_nodes: int = 0
    embedded_chunks: int = 0
    llm_calls: int = 0
    llm_cost_usd: float = 0.0
    duration_s: float = 0.0
    per_file: list[dict] = field(default_factory=list)


class ParserExhaustedError(RuntimeError):
    def __init__(self, message: str, attempts: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.attempts = attempts


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def discover_files(root: Path, cfg: DocGraphConfig) -> list[Path]:
    found: set[Path] = set()
    for pat in cfg.docs.include:
        for p in root.glob(pat):
            if p.is_file():
                found.add(p.resolve())
    for pat in cfg.docs.exclude:
        for p in root.glob(pat):
            if p.is_file():
                found.discard(p.resolve())
    return sorted(found)


def _infer_doc_metadata(path: Path, cfg: DocGraphConfig, root: Path) -> DocMetadata:
    rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
    meta_raw = cfg.docs.metadata.get(rel) or cfg.docs.metadata.get(str(path)) or {}
    name_lower = path.stem.lower()
    if meta_raw.get("type"):
        doc_type = DocType(meta_raw["type"])
    elif "errata" in name_lower:
        doc_type = DocType.ERRATA
    elif "trm" in name_lower or "reference" in name_lower or "manual" in name_lower:
        doc_type = DocType.REFERENCE_MANUAL
    elif "datasheet" in name_lower:
        doc_type = DocType.DATASHEET
    elif "user" in name_lower and "guide" in name_lower:
        doc_type = DocType.USER_GUIDE
    elif "app" in name_lower and "note" in name_lower:
        doc_type = DocType.APP_NOTE
    elif any(token in name_lower for token in ("protocol", "subsystem spec", "interface spec", " spec", "_spec", "-spec")):
        doc_type = DocType.PROTOCOL
    else:
        doc_type = DocType.UNKNOWN
    # chip_model：优先 config metadata 显式配置，其次文档名启发式推断。
    chip_model = meta_raw.get("chip_model") or infer_chip_model(path.stem)
    return DocMetadata(
        title=meta_raw.get("title") or path.stem,
        family=cfg.project.family,
        chip_model=chip_model,
        type=doc_type,
        version=meta_raw.get("version"),
        priority=meta_raw.get("priority", 10),
        supersedes=meta_raw.get("supersedes", []),
    )


def _build_llm_client(root: Path, cfg: DocGraphConfig, cache_dir: Path) -> LLMClient | None:
    if not cfg.llm.enabled:
        return None
    # 先加载 .env / .env.local
    autoload_env(root)
    provider_name = cfg.llm.provider
    provider_cfg = cfg.llm.providers.get(provider_name)
    if provider_cfg is None:
        # 允许 fallback：用一份默认 provider 配置
        from docgraph.core.config import LLMProviderConfig
        if provider_name in ("openai_compat", "openai", "volces", "deepseek"):
            provider_cfg = LLMProviderConfig(
                api_key_env="OPENAI_API_KEY",
                base_url_env="OPENAI_BASE_URL",
            )
        elif provider_name == "anthropic":
            provider_cfg = LLMProviderConfig(api_key_env="ANTHROPIC_API_KEY")
        else:
            log.warning(f"[pipeline] LLM provider '{provider_name}' not configured")
            return None
    api_key = provider_cfg.api_key or os.environ.get(provider_cfg.api_key_env)
    if not api_key:
        log.warning(
            f"[pipeline] {provider_cfg.api_key_env} not set and provider api_key is empty; "
            f"LLM disabled"
        )
        return None

    # 不同 provider 接受的参数不同
    kwargs: dict[str, Any] = {
        "api_key_env": provider_cfg.api_key_env,
        "api_key": provider_cfg.api_key,
    }
    if provider_name in ("openai", "openai_compat", "volces", "deepseek"):
        kwargs["base_url_env"] = provider_cfg.base_url_env
        kwargs["base_url"] = provider_cfg.base_url
    elif provider_name == "anthropic" and provider_cfg.base_url:
        kwargs["base_url"] = provider_cfg.base_url

    try:
        provider = make_provider(provider_name, **kwargs)
    except Exception as e:
        log.warning(f"[pipeline] LLM provider init failed: {e}")
        return None

    return LLMClient(
        provider,
        tiers={
            "fast": cfg.llm.tiers.fast,
            "balanced": cfg.llm.tiers.balanced,
            "accurate": cfg.llm.tiers.accurate,
        },
        cache_dir=cache_dir / "llm",
        tracker=CostTracker(),
        budget_usd=cfg.cost.budget_per_build_usd if cfg.cost.budget_per_build_usd > 0 else None,
        prompt_version="v2",
    )


def _build_vlm_client(root: Path, cfg: DocGraphConfig, cache_dir: Path, tracker: CostTracker | None = None) -> Any | None:
    """构造 VLM 客户端。

    优先级：
    1. config 中的 llm.vlm（推荐，允许和文本 LLM 使用不同 provider/model）
    2. `.env` 中的 VLM_API_KEY / VLM_BASE_URL / VLM_MODEL_NAME（兼容）
    3. config 中的 llm.vlm_model + 当前 llm provider
    4. config 中的 llm.tiers.accurate

    这样用户可以同时用 DeepSeek 做文本抽取、用 Doubao/Qwen/GLM/GPT-4o 做视觉。
    """
    if not cfg.llm.enabled:
        return None

    if cfg.llm.vlm.api_key and cfg.llm.vlm.base_url and cfg.llm.vlm.model:
        provider_name = cfg.llm.vlm.provider or "openai_compat"
        kwargs: dict[str, Any] = {
            "api_key_env": cfg.llm.vlm.api_key_env,
            "api_key": cfg.llm.vlm.api_key,
            "base_url_env": cfg.llm.vlm.base_url_env,
            "base_url": cfg.llm.vlm.base_url,
        }
        model = cfg.llm.vlm.model
    else:
        # .env 专用 VLM 配置兼容旧用法，不污染文本 LLM 的 OPENAI_* 配置
        vlm_api_key = os.environ.get("VLM_API_KEY")
        vlm_base_url = os.environ.get("VLM_BASE_URL")
        vlm_model = os.environ.get("VLM_MODEL_NAME")
        if vlm_api_key and vlm_base_url and vlm_model:
            provider_name = "openai_compat"
            kwargs = {
                "api_key_env": "VLM_API_KEY",
                "base_url_env": "VLM_BASE_URL",
                "base_url": vlm_base_url,
            }
            model = vlm_model
        else:
            provider_name = cfg.llm.provider
            provider_cfg = cfg.llm.providers.get(provider_name)
            if provider_cfg is None:
                log.info(f"[pipeline] VLM skipped: no provider config for {provider_name}")
                return None
            if not (provider_cfg.api_key or os.environ.get(provider_cfg.api_key_env)):
                log.info(
                    f"[pipeline] VLM skipped: {provider_cfg.api_key_env} not set "
                    "and provider api_key is empty"
                )
                return None
            kwargs = {
                "api_key_env": provider_cfg.api_key_env,
                "api_key": provider_cfg.api_key,
            }
            if provider_name in ("openai", "openai_compat", "volces", "deepseek", "qwen", "glm"):
                kwargs["base_url_env"] = provider_cfg.base_url_env
                kwargs["base_url"] = provider_cfg.base_url
            model = getattr(cfg.llm, "vlm_model", None) or cfg.llm.tiers.accurate

    try:
        provider = make_vlm_provider(provider_name, **kwargs)
    except Exception as e:
        log.warning(f"[pipeline] VLM init failed: {e}")
        return None

    log.info(f"[pipeline] VLM enabled: provider={provider_name} model={model}")
    return VLMClient(
        provider,
        model=model,
        cache_dir=cache_dir / "vlm",
        tracker=tracker,
    )


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build(
    root: Path,
    cfg: DocGraphConfig,
    store: SQLiteGraphStore,
    manifest: Manifest,
    *,
    force: bool = False,
    file_filter: Path | None = None,
    quality: str | None = None,
    dependency_policy: DependencyPolicy | None = None,
    parser_failure_policy: str | None = None,
) -> BuildReport:
    t0 = time.time()
    report = BuildReport()
    store.init_schema()
    report.quality = _normalize_quality(quality or cfg.parsers.pdf.quality)
    effective_dependency_policy = dependency_policy or cfg.runtime.dependency_policy
    effective_failure_policy = parser_failure_policy or cfg.runtime.parser_failure

    files = discover_files(root, cfg)
    if file_filter is not None:
        files = [f for f in files if f.resolve() == file_filter.resolve()]
    report.total_files = len(files)

    dg_dir = root / ".docgraph"
    cache_dir = dg_dir / "cache"

    llm_client = None
    vlm_tracker = CostTracker()
    vlm_client = None
    model_clients_initialized = False

    log.info(
        f"[bold]Build start[/bold] — {len(files)} files, "
        f"LLM={'lazy' if cfg.llm.enabled else 'no'} "
        f"VLM={'lazy' if cfg.llm.enabled else 'no'} "
        f"budget={cfg.cost.budget_per_build_usd:.2f}"
    )

    active_doc_ids: set[str] = set()
    active_paths: set[str] = set()
    index_changed = False
    dependency_cache: dict[str, DependencyResult] = {}
    for path in files:
        rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        active_paths.add(rel)
        rec = manifest.files.get(rel) or FileRecord(path=rel)
        h = file_hash(path)

        if not force and rec.hash == h and rec.status in ("extracted", "linked", "embedded"):
            log.info(f"[cyan]skip[/cyan]    {rel}")
            report.skipped += 1
            if rec.doc_id:
                active_doc_ids.add(rec.doc_id)
            report.per_file.append({"path": rel, "status": "skipped"})
            continue

        rec.hash = h
        rec.mtime = path.stat().st_mtime
        rec.size = path.stat().st_size
        rec.last_run = _utcnow()

        try:
            parsed = _stage_parse(
                path,
                cfg,
                root,
                rec,
                quality=report.quality,
                dependency_policy=effective_dependency_policy,
                parser_failure_policy=effective_failure_policy,
                dependency_cache=dependency_cache,
            )
            if not model_clients_initialized:
                llm_client = _build_llm_client(root, cfg, cache_dir)
                # VLM cost 计入同一个 tracker（如果文本 LLM 开启）
                vlm_tracker = llm_client.tracker if llm_client is not None else CostTracker()
                vlm_client = _build_vlm_client(root, cfg, cache_dir, tracker=vlm_tracker)
                model_clients_initialized = True
            extract_res = _stage_extract(
                parsed, cfg, rec, llm_client, vlm_client, root, doc_id=parsed.doc_id
            )
            with store.transaction():
                _stage_store_blocks(parsed, store, rec)  # L0 无损版面落库
                n_chunks = _stage_store_chunks(parsed, store, rec)  # L1 切块 + FTS 落库
                _stage_store(extract_res, store, rec)
            rec.status = "extracted"
            rec.doc_id = parsed.doc_id
            active_doc_ids.add(parsed.doc_id)
            rec.parser = parsed.parser
            rec.error = None
            report.parsed += 1
            index_changed = True
            if rec.quality_status == "degraded":
                report.degraded += 1
            report.extracted += 1
            report.nodes_total += len(extract_res.nodes)
            report.edges_total += len(extract_res.edges)
            report.blocks_total += sum(len(p.blocks) for p in parsed.pages)
            report.chunks_total += n_chunks
            report.llm_calls += extract_res.stats.llm_calls
            report.per_file.append({
                "path": rel,
                "status": "extracted",
                "parser": parsed.parser,
                "quality_status": rec.quality_status,
                "fallback_reason": rec.fallback_reason,
                "nodes": len(extract_res.nodes),
                "edges": len(extract_res.edges),
            })
            log.info(f"[green]ok[/green]      {rel}  ({len(extract_res.nodes)} nodes / {len(extract_res.edges)} edges)")
        except Exception as e:
            rec.status = "error"
            rec.error = str(e)
            report.errors += 1
            if rec.doc_id:
                active_doc_ids.add(rec.doc_id)
            report.per_file.append({"path": rel, "status": "error", "error": str(e)})
            log.error(f"[red]error[/red]   {rel}  — {e}")

        manifest.files[rel] = rec
        save_manifest(root, manifest)

    if file_filter is None:
        for doc_id in store.list_docs():
            if doc_id not in active_doc_ids:
                store.delete_doc(doc_id)
                index_changed = True
        for rel in list(manifest.files):
            if rel not in active_paths:
                manifest.files.pop(rel, None)
                index_changed = True
        save_manifest(root, manifest)

    # Linker stage
    if cfg.extractors.enabled and report.nodes_total > 0:
        try:
            link_rep = run_linker(root, cfg, store, manifest, llm_client=llm_client)
            report.linker_edges += link_rep.belongs_to_edges + link_rep.contained_in_edges + link_rep.llm_ie_edges + link_rep.xref_edges + link_rep.alias_edges + link_rep.supersedes_edges + link_rep.fed_alias_edges
        except Exception as e:
            log.warning(f"[link] linker failed: {e}")

    # Embedding stage
    embedding_needed = index_changed
    vstore = None
    if not embedding_needed:
        vstore = build_vector_store(cfg.storage, dg_dir, create=True)
        if vstore is not None:
            try:
                vstore.init_schema()
                embedding_needed = _embedding_missing_for_config(store, vstore, cfg)
            except Exception as e:
                log.warning(f"[embed] vector store check failed: {e}")
                embedding_needed = True

    if embedding_needed:
        try:
            vstore = vstore or build_vector_store(cfg.storage, dg_dir, create=True)
            if vstore is None:
                raise RuntimeError("vector store is disabled")
            vstore.init_schema()
            encoder = build_encoder(cfg.embeddings)
            emb_rep = embed_graph(store, vstore, encoder)
            report.embedded_nodes = emb_rep.nodes_embedded
            report.embedded_chunks = emb_rep.chunks_embedded
        except Exception as e:
            log.warning(f"[embed] embedding failed: {e}")
    else:
        log.info("Embedding skip — no index changes and configured vectors are present")

    if llm_client:
        report.llm_cost_usd = round(llm_client.tracker.cost_usd, 4)
    elif vlm_tracker:
        report.llm_cost_usd = round(vlm_tracker.cost_usd, 4)

    report.duration_s = round(time.time() - t0, 2)
    log.info(
        f"[bold]Build done[/bold] in {report.duration_s}s — "
        f"parsed={report.parsed} degraded={report.degraded} "
        f"skipped={report.skipped} errors={report.errors} "
        f"nodes+={report.nodes_total} edges+={report.edges_total} "
        f"blocks+={report.blocks_total} chunks+={report.chunks_total} "
        f"linker+={report.linker_edges} "
        f"embedded_nodes+={report.embedded_nodes} embedded_chunks+={report.embedded_chunks} "
        f"llm_calls={report.llm_calls} llm_cost=${report.llm_cost_usd:.4f}"
    )
    return report


def _embedding_missing_for_config(store: SQLiteGraphStore, vstore: Any, cfg: DocGraphConfig) -> bool:
    """Return true when the current embedding model has no vectors yet.

    This keeps an all-skipped build fast while still rebuilding vectors when a
    user changes the embedding provider/model/dimension.
    """
    model = _expected_embedding_model(cfg)
    if store.count_nodes() > 0 and not vstore.stored_node_hashes(model):
        return True
    if store.list_chunks(limit=1) and not vstore.stored_item_hashes("chunk", model):
        return True
    return False


def _expected_embedding_model(cfg: DocGraphConfig) -> str:
    provider = (cfg.embeddings.provider or "hash").strip().lower()
    if provider == "hash":
        return f"hash-{cfg.embeddings.dim or 256}"
    if provider == "bge_m3":
        return cfg.embeddings.model or "BAAI/bge-m3"
    if provider in {"openai", "openai_compat"}:
        return cfg.embeddings.model or "text-embedding-3-small"
    return provider


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


def _stage_parse(
    path: Path,
    cfg: DocGraphConfig,
    root: Path,
    rec: FileRecord,
    *,
    quality: str | None = None,
    dependency_policy: DependencyPolicy | None = None,
    parser_failure_policy: str | None = None,
    dependency_cache: dict[str, DependencyResult] | None = None,
) -> ParsedDoc:
    return _stage_parse_with_quality(
        path,
        cfg,
        root,
        rec,
        quality=quality,
        dependency_policy=dependency_policy,
        parser_failure_policy=parser_failure_policy,
        dependency_cache=dependency_cache,
    )


def _stage_parse_with_quality(
    path: Path,
    cfg: DocGraphConfig,
    root: Path,
    rec: FileRecord,
    *,
    quality: str | None,
    dependency_policy: DependencyPolicy | None = None,
    parser_failure_policy: str | None = None,
    dependency_cache: dict[str, DependencyResult] | None = None,
) -> ParsedDoc:
    t0 = time.time()
    ext = path.suffix.lower()
    if ext == ".pdf":
        pcfg = cfg.parsers.pdf
    elif ext == ".docx":
        pcfg = cfg.parsers.docx
    elif ext == ".xlsx":
        pcfg = cfg.parsers.xlsx
    elif ext in {".md", ".markdown"}:
        pcfg = cfg.parsers.md
    else:
        raise RuntimeError(f"No parser config for extension {ext}")

    parse_quality = _normalize_quality(quality or pcfg.quality)
    profile = inspect_pdf(path) if ext == ".pdf" else None
    primary, fallback = _parser_chain_for_quality(
        ext,
        pcfg.primary,
        pcfg.fallback,
        parse_quality,
        path=path,
        profile=profile,
    )
    failure_policy = parser_failure_policy or cfg.runtime.parser_failure
    parser_names = [primary] if failure_policy == "error" else [primary, *fallback]
    if ext == ".pdf" and failure_policy == "fallback" and "pymupdf" not in parser_names:
        parser_names.append("pymupdf")
    metadata = _infer_doc_metadata(path, cfg, root)
    doc_id = make_doc_id(
        cfg.project.family,
        metadata.type.value if metadata.type != DocType.UNKNOWN else "doc",
        metadata.version,
    )
    if not metadata.version:
        doc_id = f"{doc_id}::{path.stem}"

    cache_dir = root / ".docgraph" / "cache" / rec.hash.split(":")[-1][:16] if rec.hash else None
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        parsed, attempts = _parse_with_fallback(
            path,
            doc_id=doc_id,
            cache_dir=cache_dir,
            metadata=metadata,
            quality=parse_quality,
            device=pcfg.device,
            ocr_device=pcfg.ocr_device,
            pdf_profile=profile,
            parser_names=parser_names,
            dependency_policy=dependency_policy or cfg.runtime.dependency_policy,
            parser_failure_policy=failure_policy,
            dependency_cache=dependency_cache,
            return_attempts=True,
        )
    except ParserExhaustedError as exc:
        rec.requested_parser = primary
        rec.parser_attempts = exc.attempts
        rec.quality_status = "failed"
        rec.fallback_reason = _fallback_reason(exc.attempts)
        rec.stage_log["parse"] = StageRecord(
            duration_s=round(time.time() - t0, 3),
            ok=False,
            error=str(exc),
        )
        raise
    rec.requested_parser = primary
    rec.parser = parsed.parser
    rec.parser_attempts = attempts
    rec.quality_status = "ok" if parsed.parser == primary else "degraded"
    rec.fallback_reason = _fallback_reason(attempts) if parsed.parser != primary else None
    if rec.fallback_reason:
        log.warning(
            f"[parse] {path.name} degraded from {primary} to {parsed.parser}: "
            f"{rec.fallback_reason}"
        )
    rec.stage_log["parse"] = StageRecord(duration_s=round(time.time() - t0, 3), ok=True)
    rec.status = "parsed"
    return parsed


def _parse_with_fallback(
    path: Path,
    *,
    doc_id: str,
    cache_dir: Path | None,
    metadata: DocMetadata,
    quality: str,
    device: str,
    ocr_device: str | None,
    pdf_profile: Any | None,
    parser_names: list[str],
    dependency_policy: DependencyPolicy = "fallback",
    parser_failure_policy: str = "fallback",
    dependency_cache: dict[str, DependencyResult] | None = None,
    return_attempts: bool = False,
) -> ParsedDoc | tuple[ParsedDoc, list[dict[str, Any]]]:
    errors: list[str] = []
    attempts: list[dict[str, Any]] = []
    for name in parser_names:
        cls = parser_registry.get(name)
        if cls is None:
            detail = "not registered"
            errors.append(f"{name}: {detail}")
            attempts.append({"parser": name, "status": "unavailable", "reason": detail})
            if parser_failure_policy == "error":
                break
            continue
        dependency = (
            dependency_cache[name]
            if dependency_cache is not None and name in dependency_cache
            else ensure_parser_dependency(name, dependency_policy)
        )
        if dependency_cache is not None:
            dependency_cache[name] = dependency
        if not dependency.available:
            detail = dependency.reason or "dependency unavailable"
            errors.append(f"{name}: {detail}")
            attempts.append(
                {
                    "parser": name,
                    "status": "dependency_missing",
                    "reason": detail,
                    "install_attempted": dependency.attempted_install,
                }
            )
            log.warning(f"[parse] {name} unavailable for {path.name}: {detail}")
            if dependency_policy == "error" or parser_failure_policy == "error":
                break
            continue
        parser = cls()
        if not parser.can_parse(path):
            detail = "unavailable or unsupported"
            errors.append(f"{name}: {detail}")
            attempts.append({"parser": name, "status": "unavailable", "reason": detail})
            if parser_failure_policy == "error":
                break
            continue
        ctx = ParseContext(
            doc_id=doc_id,
            cache_dir=cache_dir,
            metadata=metadata,
            options={
                "quality": quality,
                "device": device,
                "ocr_device": ocr_device,
                "pdf_profile": pdf_profile.__dict__ if pdf_profile else None,
                "selected_parser": parser.name,
            },
        )
        try:
            dependency_spec = parser_dependency(name)
            if dependency_spec and dependency_spec.model_notice:
                log.info(f"[parse] {dependency_spec.model_notice}")
            parsed = parser.parse(path, ctx)
            if not any(page.blocks for page in parsed.pages):
                raise RuntimeError("parse quality gate failed: parser returned no L0 blocks")
            if path.suffix.lower() == ".pdf" and pdf_profile is not None:
                verdict = assess_pdf_parse(parsed, pdf_profile)
                if not verdict.ok:
                    raise RuntimeError(f"parse quality gate failed: {verdict.reason}")
            attempts.append({"parser": name, "status": "succeeded"})
            return (parsed, attempts) if return_attempts else parsed
        except Exception as e:
            errors.append(f"{name}: {e}")
            attempts.append({"parser": name, "status": "failed", "reason": str(e)})
            log.warning(f"[parse] parser {name} failed for {path.name}: {e}")
            if parser_failure_policy == "error":
                break
    raise ParserExhaustedError(
        f"No parser succeeded for {path} (tried: {parser_names}; errors: {errors})",
        attempts,
    )


def _fallback_reason(attempts: list[dict[str, Any]]) -> str | None:
    reasons = [
        f"{attempt['parser']}: {attempt.get('reason', attempt['status'])}"
        for attempt in attempts
        if attempt.get("status") != "succeeded"
    ]
    return "; ".join(reasons) or None


def _normalize_quality(quality: str | None) -> str:
    value = (quality or "balanced").strip().lower()
    if value not in {"fast", "balanced", "accurate"}:
        raise ValueError(f"Unsupported build quality '{quality}'. Use fast, balanced, or accurate.")
    return value


def _parser_chain_for_quality(
    ext: str,
    primary: str,
    fallback: list[str],
    quality: str,
    *,
    path: Path | None = None,
    profile: Any | None = None,
) -> tuple[str, list[str]]:
    """Keep one user command while allowing practical parser profiles.

    `auto` uses a cheap PyMuPDF profile for PDFs and routes to Docling,
    MinerU, or PyMuPDF. Explicit parser choices still work unchanged.
    """
    if ext != ".pdf":
        return primary, fallback
    profile = profile or (inspect_pdf(path) if path is not None else None)
    return pdf_parser_chain(
        configured_primary=primary,
        configured_fallback=fallback,
        quality=quality,
        profile=profile,
    )


def _stage_extract(
    parsed: ParsedDoc,
    cfg: DocGraphConfig,
    rec: FileRecord,
    llm_client: LLMClient | None,
    vlm_client: Any | None,
    root: Path,
    doc_id: str,
) -> ExtractResult:
    t0 = time.time()
    classes = extractor_registry.resolve_order(cfg.extractors.enabled)
    if not classes:
        rec.stage_log["extract"] = StageRecord(
            duration_s=round(time.time() - t0, 3),
            ok=True,
        )
        return ExtractResult()

    merged = ExtractResult()
    for cls in classes:
        # 构造 extractor 实例 + 上下文
        try:
            inst = cls()
        except Exception as e:
            log.warning(f"[extract] could not instantiate {cls.__name__}: {e}")
            continue

        ctx = ExtractContext(
            family=cfg.project.family,
            cache_dir=str(root / ".docgraph" / "cache"),
            llm_client=llm_client,
            options={
                "vlm_client": vlm_client,
                "root": str(root),
                "vlm_figure_limit": cfg.llm.vlm.figure_limit,
            },
        )
        try:
            log.info(f"[extract] start {inst.name}")
            res = inst.extract(parsed, ctx)
            log.info(
                f"[extract] done {inst.name}: "
                f"{len(res.nodes)} nodes / {len(res.edges)} edges / "
                f"{res.stats.llm_calls} llm_calls"
            )
        except Exception as e:
            log.warning(f"[extract] extractor {inst.name} failed: {e}")
            continue
        merged.nodes.extend(res.nodes)
        merged.edges.extend(res.edges)
        merged.chunks.extend(res.chunks)
        merged.stats.nodes_emitted += res.stats.nodes_emitted
        merged.stats.edges_emitted += res.stats.edges_emitted
        merged.stats.duration_s += res.stats.duration_s
        merged.stats.llm_calls += res.stats.llm_calls
        merged.stats.failed += res.stats.failed

    rec.stage_log["extract"] = StageRecord(
        duration_s=round(time.time() - t0, 3),
        ok=True,
        nodes=len(merged.nodes),
        edges=len(merged.edges),
    )
    return merged


def _stage_store_blocks(
    parsed: ParsedDoc, store: SQLiteGraphStore, rec: FileRecord
) -> None:
    """L0：先清旧 doc，再把所有 Block 落库。"""
    t0 = time.time()
    if rec.doc_id and rec.doc_id != parsed.doc_id:
        store.delete_doc(rec.doc_id)
    store.delete_doc(parsed.doc_id)  # 清干净（含 nodes/blocks/chunks）
    all_blocks = [b for p in parsed.pages for b in p.blocks]
    store.upsert_blocks(all_blocks)
    rec.stage_log["store_blocks"] = StageRecord(
        duration_s=round(time.time() - t0, 3), ok=True, nodes=len(all_blocks),
    )


def _stage_store_chunks(
    parsed: ParsedDoc, store: SQLiteGraphStore, rec: FileRecord
) -> int:
    """L1：把 Block 切成 chunk 并落库（含 FTS 全文索引）。返回 chunk 数。"""
    from docgraph.chunker import chunk_doc

    t0 = time.time()
    chunks = chunk_doc(parsed)
    store.upsert_chunks(chunks)
    rec.stage_log["store_chunks"] = StageRecord(
        duration_s=round(time.time() - t0, 3), ok=True, nodes=len(chunks),
    )
    return len(chunks)


def _stage_store(
    result: ExtractResult, store: SQLiteGraphStore, rec: FileRecord
) -> None:
    t0 = time.time()
    # 注意：doc 已在 _stage_store_blocks 阶段清理，这里不再 delete_doc
    for node in result.nodes:
        store.upsert_node(node)
    for edge in result.edges:
        try:
            store.upsert_edge(edge)
        except Exception:
            log.exception(f"edge upsert failed {edge.src}→{edge.dst}")
            raise
    rec.stage_log["store"] = StageRecord(
        duration_s=round(time.time() - t0, 3),
        ok=True,
        nodes=len(result.nodes),
        edges=len(result.edges),
    )
