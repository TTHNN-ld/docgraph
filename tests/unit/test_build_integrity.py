"""Regression tests for build consistency and incremental indexing."""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from docgraph.core.config import DocGraphConfig, DocsConfig, ExtractorsConfig
from docgraph.core.manifest import FileRecord, Manifest
from docgraph.core.pipeline import BuildReport, _stage_extract, build
from docgraph.embeddings.hash_encoder import HashEncoder
from docgraph.embeddings.indexer import embed_graph
from docgraph.embeddings.vector_store import VectorStore
from docgraph.graph.schema import (
    Block,
    BlockKind,
    Node,
    NodeKind,
    ParsedDoc,
    ParsedPage,
)
from docgraph.graph.sqlite_store import SQLiteGraphStore


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
