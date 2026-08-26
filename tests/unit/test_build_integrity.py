"""Regression tests for build consistency and incremental indexing."""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from docgraph.core.bootstrap import bootstrap
from docgraph.core.config import DocGraphConfig, DocsConfig, ExtractorsConfig
from docgraph.core.ids import file_hash
from docgraph.core.manifest import FileRecord, Manifest
from docgraph.core.pipeline import BuildReport, _stage_extract, build, discover_files
from docgraph.embeddings.hash_encoder import HashEncoder
from docgraph.embeddings.indexer import embed_graph
from docgraph.embeddings.vector_store import VectorStore
from docgraph.graph.schema import (
    Block,
    BlockKind,
    Chunk,
    Node,
    NodeKind,
    ParsedDoc,
    ParsedPage,
)
from docgraph.graph.sqlite_store import SQLiteGraphStore


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
    (docs / "registers.markdown").write_text(
        "# Long-extension source\n", encoding="utf-8"
    )
    (tmp_path / ".docgraph").mkdir()
    store = SQLiteGraphStore(tmp_path / ".docgraph" / "graph.db")
    store.init_schema()
    cfg = DocGraphConfig(
        docs=DocsConfig(include=["docs/*.md", "docs/*.markdown"]),
        extractors=ExtractorsConfig(enabled=[]),
    )
    manifest = Manifest()
    bootstrap()

    report = build(tmp_path, cfg, store, manifest)

    expected_doc_ids = {
        "default::doc::docs/registers.md",
        "default::doc::docs/registers.markdown",
    }
    assert report.errors == 0
    assert set(store.list_docs()) == expected_doc_ids
    assert {record.doc_id for record in manifest.files.values()} == expected_doc_ids
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

    report = build(tmp_path, cfg, store, manifest)

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


def test_schema_initialization_propagates_migration_failures(
    tmp_path: Path, monkeypatch
) -> None:
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

    result = _stage_extract(parsed, cfg, record, None, None, tmp_path, "doc")

    assert result.nodes == []
    assert result.edges == []
    assert record.stage_log["extract"].ok


def test_full_build_removes_documents_and_manifest_entries_missing_from_source(
    tmp_path: Path,
) -> None:
    store = SQLiteGraphStore(tmp_path / ".docgraph" / "graph.db")
    store.init_schema()
    store.upsert_blocks([
        Block(
            id="stale#p1#b0",
            doc_id="stale",
            page=1,
            kind=BlockKind.PARAGRAPH,
            text="stale",
        )
    ])
    manifest = Manifest(
        files={"spec/removed.pdf": FileRecord(path="spec/removed.pdf", doc_id="stale")}
    )
    cfg = DocGraphConfig(
        docs=DocsConfig(include=[]),
        extractors=ExtractorsConfig(enabled=[]),
    )

    report = build(tmp_path, cfg, store, manifest)

    assert report.errors == 0
    assert store.list_docs() == []
    assert manifest.files == {}
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
            )
        }
    )
    cfg = DocGraphConfig(docs=DocsConfig(include=["spec/cached.pdf"]))
    store = SQLiteGraphStore(tmp_path / ".docgraph" / "graph.db")
    store.init_schema()

    def fail_model_init(*_args, **_kwargs):
        raise AssertionError("model clients should not initialize when all files are skipped")

    def fail_encoder_init(*_args, **_kwargs):
        raise AssertionError("embedding should not run when there are no index changes")

    monkeypatch.setattr(pipeline, "_build_llm_client", fail_model_init)
    monkeypatch.setattr(pipeline, "_build_vlm_client", fail_model_init)
    monkeypatch.setattr(pipeline, "build_encoder", fail_encoder_init)

    report = build(tmp_path, cfg, store, manifest)

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
            )
        }
    )
    cfg = DocGraphConfig.model_validate(
        {
            "docs": {"include": ["spec/cached.pdf"]},
            "embeddings": {"provider": "bge_m3", "model": "new-model"},
        }
    )
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
        return HashEncoder(dim=16)

    monkeypatch.setattr(pipeline, "build_encoder", fake_build_encoder)

    report = build(tmp_path, cfg, store, manifest)

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


def test_cli_build_returns_nonzero_when_any_file_failed(monkeypatch) -> None:
    from docgraph.cli import main

    class Store:
        def close(self) -> None:
            pass

    monkeypatch.setattr(main, "_open_project", lambda: (Path.cwd(), Store(), None))
    monkeypatch.setattr(main, "load_config", lambda _root: DocGraphConfig())
    monkeypatch.setattr(main, "load_manifest", lambda _root: Manifest())
    monkeypatch.setattr(main, "run_build", lambda *args, **kwargs: BuildReport(errors=1))

    result = CliRunner().invoke(main.app, ["build"])

    assert result.exit_code == 1
