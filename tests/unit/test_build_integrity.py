"""Regression tests for build consistency and incremental indexing."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from docgraph.core.bootstrap import bootstrap
from docgraph.core.build_lock import BuildLockedError, project_build_lock
from docgraph.core.config import DocGraphConfig, DocsConfig, ExtractorsConfig
from docgraph.core.ids import file_hash
from docgraph.core.manifest import (
    FileRecord,
    Manifest,
    StageRecord,
    load_manifest,
    save_manifest,
)
from docgraph.core.pipeline import (
    BuildReport,
    _embedding_fingerprint,
    _embedding_missing_for_config,
    _file_build_fingerprint,
    _stage_extract,
    build,
    discover_files,
)
from docgraph.embeddings.hash_encoder import HashEncoder
from docgraph.embeddings.indexer import desired_node_hashes, embed_graph
from docgraph.embeddings.vector_store import VectorStore
from docgraph.graph.schema import (
    Block,
    BlockKind,
    Chunk,
    Edge,
    EdgeKind,
    Evidence,
    Node,
    NodeKind,
    ParsedDoc,
    ParsedPage,
)
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.linker.llm_ie import LLMIEReport
from docgraph.linker.runner import run_linker


def test_default_discovery_includes_all_core_document_formats(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    spec = tmp_path / "spec"
    docs.mkdir()
    spec.mkdir()
    expected = {
        docs / "manual.pdf",
        docs / "registers.docx",
        docs / "pins.xlsx",
        docs / "macros.xlsm",
        spec / "protocol.md",
        spec / "notes.markdown",
    }
    for path in expected:
        path.write_bytes(b"fixture")
    (docs / "ignored.txt").write_text("not a supported document", encoding="utf-8")

    discovered = set(discover_files(tmp_path, DocGraphConfig()))

    assert discovered == {path.resolve() for path in expected}


def test_build_keeps_same_stem_different_format_documents_distinct(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "registers.md").write_text("# Markdown source\n", encoding="utf-8")
    (docs / "registers.markdown").write_text("# Long-extension source\n", encoding="utf-8")
    (tmp_path / ".docgraph").mkdir()
    store = SQLiteGraphStore(tmp_path / ".docgraph" / "graph.db")
    store.init_schema()
    cfg = DocGraphConfig(
        docs=DocsConfig(include=["docs/*.md", "docs/*.markdown"]),
        extractors=ExtractorsConfig(enabled=[]),
    )
    bootstrap()

    report = build(tmp_path, cfg, store)

    expected_doc_ids = {
        "default::doc::docs/registers.md",
        "default::doc::docs/registers.markdown",
    }
    assert report.errors == 0
    assert report.embedded_nodes == 0
    assert report.embedded_chunks == 0
    assert not (tmp_path / ".docgraph" / "vectors.db").exists()
    assert set(store.list_docs()) == expected_doc_ids
    assert {record.doc_id for record in load_manifest(tmp_path).files.values()} == expected_doc_ids
    store.close()


def test_build_replaces_legacy_document_id_even_when_content_hash_matches(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "guide.md"
    source.write_text("# Guide\n", encoding="utf-8")
    (tmp_path / ".docgraph").mkdir()
    store = SQLiteGraphStore(tmp_path / ".docgraph" / "graph.db")
    store.init_schema()
    store.upsert_blocks(
        [
            Block(
                id="default::doc::guide#p1#b0",
                doc_id="default::doc::guide",
                page=1,
                kind=BlockKind.PARAGRAPH,
                text="legacy",
            )
        ]
    )
    manifest = Manifest(
        files={
            "docs/guide.md": FileRecord(
                path="docs/guide.md",
                doc_id="default::doc::guide",
                hash=file_hash(source),
                status="extracted",
            )
        }
    )
    cfg = DocGraphConfig(
        docs=DocsConfig(include=["docs/*.md"]),
        extractors=ExtractorsConfig(enabled=[]),
    )
    bootstrap()
    save_manifest(tmp_path, manifest)

    report = build(tmp_path, cfg, store)

    assert report.parsed == 1
    assert report.skipped == 0
    assert store.list_docs() == ["default::doc::docs/guide.md"]
    assert store.get_block("default::doc::guide#p1#b0") is None
    store.close()


def test_nested_store_transaction_rolls_back_all_document_layers(tmp_path: Path) -> None:
    store = SQLiteGraphStore(tmp_path / "graph.db")
    store.init_schema()
    block = Block(
        id="doc#p1#b0",
        doc_id="doc",
        page=1,
        kind=BlockKind.PARAGRAPH,
        text="content",
    )

    with pytest.raises(RuntimeError, match="abort"):
        with store.transaction():
            store.upsert_blocks([block])
            store.upsert_node(
                Node(id="doc::sec:one", kind=NodeKind.SECTION, name="One", doc_id="doc")
            )
            raise RuntimeError("abort")

    assert store.get_block(block.id) is None
    assert store.get_node("doc::sec:one") is None
    store.close()


def test_schema_initialization_propagates_migration_failures(tmp_path: Path, monkeypatch) -> None:
    from docgraph.graph import migrations

    store = SQLiteGraphStore(tmp_path / "graph.db")
    monkeypatch.setattr(migrations, "current_db_version", lambda _path: 0)

    def fail_migration(_path: Path) -> None:
        raise RuntimeError("migration failed")

    monkeypatch.setattr(migrations, "run_migrations", fail_migration)

    with pytest.raises(RuntimeError, match="migration failed"):
        store.init_schema()


def test_empty_l2_configuration_is_a_successful_noop(tmp_path: Path) -> None:
    cfg = DocGraphConfig(extractors=ExtractorsConfig(enabled=[]))
    record = FileRecord(path="spec.pdf")
    parsed = ParsedDoc(
        doc_id="doc",
        source_path="spec.pdf",
        pages=[ParsedPage(page_no=1)],
    )

    result = _stage_extract(parsed, cfg, record, None, None, tmp_path)

    assert result.nodes == []
    assert result.edges == []
    assert record.stage_log["extract"].ok


def test_full_build_removes_documents_and_manifest_entries_missing_from_source(
    tmp_path: Path,
) -> None:
    store = SQLiteGraphStore(tmp_path / ".docgraph" / "graph.db")
    store.init_schema()
    store.upsert_blocks(
        [
            Block(
                id="stale#p1#b0",
                doc_id="stale",
                page=1,
                kind=BlockKind.PARAGRAPH,
                text="stale",
            )
        ]
    )
    manifest = Manifest(
        files={"spec/removed.pdf": FileRecord(path="spec/removed.pdf", doc_id="stale")}
    )
    cfg = DocGraphConfig(
        docs=DocsConfig(include=[]),
        extractors=ExtractorsConfig(enabled=[]),
    )
    save_manifest(tmp_path, manifest)

    report = build(tmp_path, cfg, store)

    assert report.errors == 0
    assert store.list_docs() == []
    assert load_manifest(tmp_path).files == {}
    store.close()


def test_build_skips_model_initialization_and_embedding_when_everything_is_cached(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from docgraph.core import pipeline

    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    doc_path = spec_dir / "cached.pdf"
    doc_path.write_bytes(b"%PDF cached placeholder")
    manifest = Manifest(
        files={
            "spec/cached.pdf": FileRecord(
                path="spec/cached.pdf",
                doc_id="default::doc::spec/cached.pdf",
                hash=file_hash(doc_path),
                status="extracted",
                stage_log={"extract": StageRecord(ok=True)},
            )
        }
    )
    cfg = DocGraphConfig(docs=DocsConfig(include=["spec/cached.pdf"]))
    source_hash = file_hash(doc_path)
    cached = manifest.files["spec/cached.pdf"]
    cached.indexed_hash = source_hash
    cached.build_fingerprint = _file_build_fingerprint(
        doc_path,
        tmp_path,
        cfg,
        source_hash=source_hash,
        quality="balanced",
        parser_failure_policy=cfg.runtime.parser_failure,
    )
    save_manifest(tmp_path, manifest)
    store = SQLiteGraphStore(tmp_path / ".docgraph" / "graph.db")
    store.init_schema()

    def fail_model_init(*_args, **_kwargs):
        raise AssertionError("model clients should not initialize when all files are skipped")

    def fail_encoder_init(*_args, **_kwargs):
        raise AssertionError("embedding should not run when there are no index changes")

    monkeypatch.setattr(pipeline, "_build_llm_client", fail_model_init)
    monkeypatch.setattr(pipeline, "_build_vlm_client", fail_model_init)
    monkeypatch.setattr(pipeline, "build_encoder", fail_encoder_init)

    report = build(tmp_path, cfg, store)

    assert report.skipped == 1
    assert report.parsed == 0
    assert report.embedded_nodes == 0
    assert report.embedded_chunks == 0
    store.close()


def test_build_embeds_when_embedding_config_changed_but_files_are_cached(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from docgraph.core import pipeline

    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    doc_path = spec_dir / "cached.pdf"
    doc_path.write_bytes(b"%PDF cached placeholder")
    manifest = Manifest(
        files={
            "spec/cached.pdf": FileRecord(
                path="spec/cached.pdf",
                doc_id="default::doc::spec/cached.pdf",
                hash=file_hash(doc_path),
                status="extracted",
                stage_log={"extract": StageRecord(ok=True)},
            )
        }
    )
    cfg = DocGraphConfig.model_validate(
        {
            "docs": {"include": ["spec/cached.pdf"]},
            "embeddings": {"provider": "bge_m3", "model": "new-model"},
        }
    )
    source_hash = file_hash(doc_path)
    cached = manifest.files["spec/cached.pdf"]
    cached.indexed_hash = source_hash
    cached.build_fingerprint = _file_build_fingerprint(
        doc_path,
        tmp_path,
        cfg,
        source_hash=source_hash,
        quality="balanced",
        parser_failure_policy=cfg.runtime.parser_failure,
    )
    save_manifest(tmp_path, manifest)
    store = SQLiteGraphStore(tmp_path / ".docgraph" / "graph.db")
    store.init_schema()
    store.upsert_chunks(
        [
            Chunk(
                id="default::doc::spec/cached.pdf::chunk:1",
                doc_id="default::doc::spec/cached.pdf",
                page=1,
                page_start=1,
                page_end=1,
                text="cached chunk",
                block_ids=[],
                source_hash="source:cached",
            )
        ]
    )
    built = {"encoder": False}

    monkeypatch.setattr(pipeline, "_build_llm_client", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "_build_vlm_client", lambda *_args, **_kwargs: None)

    def fake_build_encoder(_cfg):
        built["encoder"] = True
        encoder = HashEncoder(dim=16)
        encoder.model = "new-model"
        return encoder

    monkeypatch.setattr(pipeline, "build_encoder", fake_build_encoder)

    report = build(tmp_path, cfg, store)

    assert report.skipped == 1
    assert built["encoder"] is True
    assert report.embedded_chunks == 1
    store.close()


def test_embedding_refreshes_changed_content_and_prunes_removed_nodes(
    tmp_path: Path,
) -> None:
    store = SQLiteGraphStore(tmp_path / "graph.db")
    store.init_schema()
    vectors = VectorStore(tmp_path / "vectors.db")
    encoder = HashEncoder(dim=32)
    node = Node(
        id="doc::sec:one",
        kind=NodeKind.SECTION,
        name="One",
        summary="old summary",
        doc_id="doc",
        hash="sha256:old",
    )
    store.upsert_node(node)

    first = embed_graph(store, vectors, encoder)
    node.attrs["description"] = "new description"
    store.upsert_node(node)
    second = embed_graph(store, vectors, encoder)

    assert first.nodes_embedded == 1
    assert second.nodes_embedded == 1
    assert vectors.stored_node_hashes(encoder.model)[node.id] is not None

    store.delete_doc("doc")
    embed_graph(store, vectors, encoder)
    assert vectors.count() == 0
    vectors.close()
    store.close()


def test_embedding_completeness_detects_partial_vector_index(tmp_path: Path) -> None:
    store = SQLiteGraphStore(tmp_path / "graph.db")
    store.init_schema()
    store.upsert_chunks(
        [
            Chunk(id="one", doc_id="doc", text="first", block_ids=[]),
            Chunk(id="two", doc_id="doc", text="second", block_ids=[]),
        ]
    )
    vectors = VectorStore(tmp_path / "vectors.db")
    vectors.init_schema()
    encoder = HashEncoder(dim=32)
    first = store.get_chunk("one")
    assert first is not None
    from docgraph.core.ids import content_hash
    from docgraph.embeddings.indexer import text_for_chunk_embedding

    vectors.upsert_item(
        "chunk",
        first.id,
        encoder.model,
        encoder.encode([text_for_chunk_embedding(first)])[0],
        content_hash=content_hash(text_for_chunk_embedding(first)),
    )
    cfg = DocGraphConfig.model_validate({"embeddings": {"provider": "hash", "dim": 32}})

    assert _embedding_missing_for_config(store, vectors, cfg) is True
    embed_graph(store, vectors, encoder)
    assert _embedding_missing_for_config(store, vectors, cfg) is False
    vectors.close()
    store.close()


def test_embedding_contract_traverses_all_node_pages(tmp_path: Path) -> None:
    store = SQLiteGraphStore(tmp_path / "graph.db")
    store.init_schema()
    with store.transaction():
        for index in range(2001):
            store.upsert_node(
                Node(
                    id=f"section-{index:04d}",
                    kind=NodeKind.SECTION,
                    name="Repeated section name",
                    doc_id="doc",
                )
            )

    assert len(desired_node_hashes(store)) == 2001
    store.close()


def test_build_configuration_change_invalidates_unchanged_source(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "guide.md"
    source.write_text("# Guide\n\nContent.\n", encoding="utf-8")
    store = SQLiteGraphStore(tmp_path / ".docgraph" / "graph.db")
    bootstrap()

    plain = DocGraphConfig(
        docs=DocsConfig(include=["docs/*.md"]),
        extractors=ExtractorsConfig(enabled=[]),
    )
    assert build(tmp_path, plain, store).parsed == 1
    assert build(tmp_path, plain, store).skipped == 1

    with_sections = DocGraphConfig(
        docs=DocsConfig(include=["docs/*.md"]),
        extractors=ExtractorsConfig(enabled=["section"]),
    )
    rebuilt = build(tmp_path, with_sections, store)

    assert rebuilt.parsed == 1
    assert rebuilt.skipped == 0
    assert store.count_nodes(NodeKind.SECTION) == 1
    store.close()


def test_disabled_model_settings_do_not_invalidate_file_build(tmp_path: Path) -> None:
    source = tmp_path / "guide.md"
    source.write_text("# Guide\n", encoding="utf-8")
    source_hash = file_hash(source)
    base = DocGraphConfig.model_validate(
        {
            "extractors": {"enabled": []},
            "llm": {
                "enabled": False,
                "tiers": {"fast": "unused-a"},
                "vlm": {"enabled": False, "model": "unused-vision-a"},
            },
        }
    )
    changed = DocGraphConfig.model_validate(
        {
            "extractors": {"enabled": []},
            "llm": {
                "enabled": False,
                "tiers": {"fast": "unused-b"},
                "vlm": {"enabled": False, "model": "unused-vision-b"},
            },
        }
    )

    assert _file_build_fingerprint(
        source,
        tmp_path,
        base,
        source_hash=source_hash,
        quality=None,
        parser_failure_policy="fallback",
    ) == _file_build_fingerprint(
        source,
        tmp_path,
        changed,
        source_hash=source_hash,
        quality=None,
        parser_failure_policy="fallback",
    )


def test_build_reports_invalid_explicit_target(tmp_path: Path) -> None:
    store = SQLiteGraphStore(tmp_path / ".docgraph" / "graph.db")
    report = build(
        tmp_path,
        DocGraphConfig(),
        store,
        file_filter=Path("docs/missing.pdf"),
    )

    assert report.status == "failed"
    assert report.errors == 1
    assert report.per_file[0]["error"] == "file does not exist"
    store.close()


def test_build_fails_fast_when_project_is_already_building(tmp_path: Path) -> None:
    store = SQLiteGraphStore(tmp_path / ".docgraph" / "graph.db")
    with project_build_lock(tmp_path), pytest.raises(BuildLockedError, match="already running"):
        build(tmp_path, DocGraphConfig(), store)
    store.close()


def test_embedding_fingerprint_includes_provider_endpoint() -> None:
    first = DocGraphConfig.model_validate(
        {
            "embeddings": {
                "provider": "openai",
                "model": "same-model",
                "base_url": "https://first.example/v1",
            }
        }
    )
    second = DocGraphConfig.model_validate(
        {
            "embeddings": {
                "provider": "openai_compat",
                "model": "same-model",
                "base_url": "https://second.example/v1",
            }
        }
    )

    assert _embedding_fingerprint(first) != _embedding_fingerprint(second)


def test_embedding_rejects_short_provider_response(tmp_path: Path) -> None:
    class ShortEncoder:
        name = "short"
        model = "short-model"
        dim = 4

        def encode(self, _texts):
            return []

    store = SQLiteGraphStore(tmp_path / "graph.db")
    store.init_schema()
    store.upsert_node(Node(id="section", kind=NodeKind.SECTION, name="Section", doc_id="doc"))
    vectors = VectorStore(tmp_path / "vectors.db")

    with pytest.raises(RuntimeError, match="0 vectors for 1 inputs"):
        embed_graph(store, vectors, ShortEncoder())

    vectors.close()
    store.close()


def test_embedding_retry_preserves_file_level_incrementality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from docgraph.core import pipeline

    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "guide.md"
    source.write_text("# Guide\n\nFirst version.\n", encoding="utf-8")
    cfg = DocGraphConfig.model_validate(
        {
            "docs": {"include": ["docs/*.md"]},
            "extractors": {"enabled": ["section"]},
            "embeddings": {"provider": "hash", "dim": 16},
        }
    )
    store = SQLiteGraphStore(tmp_path / ".docgraph" / "graph.db")
    bootstrap()
    assert build(tmp_path, cfg, store).status == "success"

    original_embed_graph = pipeline.embed_graph
    source.write_text("# Guide\n\nSecond version.\n", encoding="utf-8")

    def fail_embedding(*_args, **_kwargs):
        raise RuntimeError("temporary embedding outage")

    monkeypatch.setattr(pipeline, "embed_graph", fail_embedding)
    failed = build(tmp_path, cfg, store)
    assert failed.status == "degraded"
    failed_state = load_manifest(tmp_path).derived["embedding"]
    assert failed_state.fingerprint == _embedding_fingerprint(cfg)

    calls: list[bool] = []

    def capture_retry(*args, **kwargs):
        calls.append(kwargs["only_missing"])
        return original_embed_graph(*args, **kwargs)

    monkeypatch.setattr(pipeline, "embed_graph", capture_retry)
    recovered = build(tmp_path, cfg, store)

    assert recovered.skipped == 1
    assert recovered.status == "success"
    assert calls == [True]
    store.close()


def test_unavailable_enabled_model_is_retried_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from docgraph.core import pipeline

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Guide\n\nContent.\n", encoding="utf-8")
    cfg = DocGraphConfig.model_validate(
        {
            "docs": {"include": ["docs/*.md"]},
            "extractors": {"enabled": ["section"]},
            "llm": {
                "enabled": True,
                "provider": "anthropic",
                "providers": {"anthropic": {"api_key": "test-key"}},
            },
        }
    )
    store = SQLiteGraphStore(tmp_path / ".docgraph" / "graph.db")
    bootstrap()
    monkeypatch.setattr(pipeline, "_build_llm_client", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "_build_vlm_client", lambda *_args, **_kwargs: None)

    first = build(tmp_path, cfg, store)
    second = build(tmp_path, cfg, store)

    assert first.status == "degraded"
    assert second.parsed == 1
    assert second.skipped == 0
    assert load_manifest(tmp_path).derived["linker"].status == "degraded"
    store.close()


def test_extractor_failure_is_recorded_as_degraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from docgraph.core import pipeline

    class FailingExtractor:
        name = "failing"

        def extract(self, _doc, _ctx):
            raise RuntimeError("broken extractor")

    record = FileRecord(path="guide.md")
    parsed = ParsedDoc(
        doc_id="doc",
        source_path="guide.md",
        pages=[ParsedPage(page_no=1)],
    )
    monkeypatch.setattr(
        pipeline.extractor_registry,
        "resolve_order",
        lambda _enabled: [FailingExtractor],
    )
    monkeypatch.setattr(
        pipeline.extractor_registry,
        "get",
        lambda name: FailingExtractor if name == "failing" else None,
    )

    result = _stage_extract(
        parsed,
        DocGraphConfig(extractors=ExtractorsConfig(enabled=["failing"])),
        record,
        None,
        None,
        tmp_path,
    )

    assert result.stats.failed == 1
    assert record.stage_log["extract"].ok is False
    assert "broken extractor" in (record.stage_log["extract"].error or "")


def test_unknown_extractor_degrades_but_keeps_l0_l1(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Guide\n\nContent.\n", encoding="utf-8")
    store = SQLiteGraphStore(tmp_path / ".docgraph" / "graph.db")
    bootstrap()
    cfg = DocGraphConfig(
        docs=DocsConfig(include=["docs/*.md"]),
        extractors=ExtractorsConfig(enabled=["missing"]),
    )

    report = build(tmp_path, cfg, store)

    assert report.status == "degraded"
    assert report.errors == 0
    assert store.count_chunks() > 0
    assert load_manifest(tmp_path).files["docs/guide.md"].stage_log["extract"].ok is False
    retry = build(tmp_path, cfg, store)
    assert retry.parsed == 1
    assert retry.skipped == 0
    store.close()


def test_linker_rebuild_removes_stale_derived_edges(tmp_path: Path) -> None:
    store = SQLiteGraphStore(tmp_path / "graph.db")
    store.init_schema()
    store.upsert_node(Node(id="a", kind=NodeKind.SECTION, name="A", doc_id="doc"))
    store.upsert_node(Node(id="b", kind=NodeKind.SECTION, name="B", doc_id="doc"))
    store.upsert_edge(
        Edge(
            src="a",
            dst="b",
            kind=EdgeKind.REFERENCES,
            evidence=Evidence(extractor="xref@0.1", raw_snippet="stale"),
        )
    )

    run_linker(tmp_path, DocGraphConfig(), store, Manifest())

    assert store.get_edge("a", "b", EdgeKind.REFERENCES) is None
    store.close()


def test_linker_failure_rolls_back_previous_complete_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from docgraph.linker import runner

    store = SQLiteGraphStore(tmp_path / "graph.db")
    store.init_schema()
    store.upsert_node(Node(id="a", kind=NodeKind.SECTION, name="A", doc_id="doc"))
    store.upsert_node(Node(id="b", kind=NodeKind.SECTION, name="B", doc_id="doc"))
    store.upsert_edge(
        Edge(
            src="a",
            dst="b",
            kind=EdgeKind.REFERENCES,
            evidence=Evidence(extractor="xref@0.1", raw_snippet="last good result"),
        )
    )

    def fail(*_args, **_kwargs):
        raise RuntimeError("federation failed")

    monkeypatch.setattr(runner.FederationLinker, "run", fail)

    with pytest.raises(RuntimeError, match="federation failed"):
        run_linker(tmp_path, DocGraphConfig(), store, Manifest())

    assert store.get_edge("a", "b", EdgeKind.REFERENCES) is not None
    store.close()


def test_linker_prepares_remote_work_before_write_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from docgraph.linker import runner

    store = SQLiteGraphStore(tmp_path / "graph.db")
    store.init_schema()
    transaction_depths: list[int] = []

    def prepare(_self, observed_store, *, llm_client=None):
        transaction_depths.append(observed_store._transaction_depth)
        return LLMIEReport(), []

    monkeypatch.setattr(runner.LLMIELinker, "prepare", prepare)

    run_linker(tmp_path, DocGraphConfig(), store, Manifest())

    assert transaction_depths == [0]
    store.close()


def test_linker_reports_audit_failure_after_committing_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from docgraph.linker import runner

    store = SQLiteGraphStore(tmp_path / "graph.db")
    store.init_schema()

    def fail_audit(_root, _records):
        raise OSError("read-only audit directory")

    monkeypatch.setattr(runner.XRefLinker, "_write_unresolved", staticmethod(fail_audit))

    report = run_linker(tmp_path, DocGraphConfig(), store, Manifest())

    assert report.warnings == ["xref audit write failed: read-only audit directory"]
    store.close()


def test_cli_build_returns_nonzero_when_any_file_failed(monkeypatch) -> None:
    from docgraph.cli import main

    class Store:
        def close(self) -> None:
            pass

    monkeypatch.setattr(
        main,
        "_open_graph_store",
        lambda **_kwargs: (Path.cwd(), Store(), DocGraphConfig()),
    )
    monkeypatch.setattr(main, "load_manifest", lambda _root: Manifest())
    monkeypatch.setattr(
        main,
        "run_build",
        lambda *args, **kwargs: BuildReport(
            status="failed",
            errors=1,
            per_file=[
                {"path": "docs/missing.pdf", "status": "error", "error": "file does not exist"}
            ],
        ),
    )

    result = CliRunner().invoke(main.app, ["build"])

    assert result.exit_code == 1
    assert "docs/missing.pdf: file does not exist" in result.output


def test_cli_build_strict_returns_nonzero_when_degraded(monkeypatch) -> None:
    from docgraph.cli import main

    class Store:
        def close(self) -> None:
            pass

    monkeypatch.setattr(
        main,
        "_open_graph_store",
        lambda **_kwargs: (Path.cwd(), Store(), DocGraphConfig()),
    )
    monkeypatch.setattr(main, "load_manifest", lambda _root: Manifest())
    monkeypatch.setattr(
        main,
        "run_build",
        lambda *args, **kwargs: BuildReport(
            status="degraded",
            warnings=[{"stage": "linker", "error": "failed"}],
        ),
    )

    assert CliRunner().invoke(main.app, ["build"]).exit_code == 0
    assert CliRunner().invoke(main.app, ["build", "--strict"]).exit_code == 1
