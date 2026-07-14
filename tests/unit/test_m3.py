"""M3 测试：多格式 Parser、插件、Migration、Federation。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from docgraph.parsers.base import ParseContext


# ---------------------------------------------------------------------------
# Markdown parser（无外部依赖，pip install 时已带 markdown-it-py）
# ---------------------------------------------------------------------------


def test_markdown_parser_basic():
    from docgraph.parsers.markdown_parser import MarkdownParser

    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "t.md"
        f.write_text(
            "# Chapter 1\n\nIntro text.\n\n"
            "## Section 1.1\n\nSome content.\n\n"
            "```python\nprint('hello')\n```\n\n"
            "## Section 1.2\n\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n",
            encoding="utf-8",
        )
        p = MarkdownParser()
        assert p.can_parse(f)
        parsed = p.parse(f, ParseContext(doc_id="d"))

    assert len(parsed.toc) == 3
    assert parsed.toc[0].section_path == "1"
    assert parsed.toc[1].section_path == "1.1"
    assert parsed.toc[2].section_path == "1.2"
    # 至少有一个 table
    assert len(parsed.pages[0].tables) == 1
    tbl = parsed.pages[0].tables[0]
    assert tbl.headers == ["A", "B"]
    assert tbl.rows == [["1", "2"]]
    assert parsed.pages[0].blocks
    assert any(block.table is not None for block in parsed.pages[0].blocks)


def test_markdown_parser_image():
    from docgraph.parsers.markdown_parser import MarkdownParser

    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "img.md"
        f.write_text(
            "# Title\n\n![diagram](images/foo.png)\n",
            encoding="utf-8",
        )
        parsed = MarkdownParser().parse(f, ParseContext(doc_id="d"))
    figs = parsed.pages[0].figures
    assert len(figs) == 1
    assert figs[0].image_path == "images/foo.png"
    assert figs[0].caption == "diagram"
    assert any(block.image_path == "images/foo.png" for block in parsed.pages[0].blocks)


# ---------------------------------------------------------------------------
# Docx / Xlsx parser：只测能 import + can_parse；真实解析需要包/样本
# ---------------------------------------------------------------------------


def test_docx_parser_import():
    from docgraph.parsers.docx_parser import DocxParser

    p = DocxParser()
    assert p.name == "docx"
    assert p.can_parse(Path("x.docx"))
    assert not p.can_parse(Path("x.pdf"))


def test_xlsx_parser_import():
    from docgraph.parsers.xlsx_parser import XlsxParser

    p = XlsxParser()
    assert p.name == "xlsx"
    assert p.can_parse(Path("x.xlsx"))
    assert p.can_parse(Path("x.xlsm"))


# ---------------------------------------------------------------------------
# Plugin discovery
# ---------------------------------------------------------------------------


def test_plugin_discovery_runs():
    from docgraph.core.bootstrap import bootstrap
    from docgraph.core.plugins import discovered

    bootstrap()
    by_group = discovered()
    assert "docgraph.parsers" in by_group
    assert "docgraph.extractors" in by_group
    # 内置至少 4 个 parser
    parser_names = {p.name for p in by_group["docgraph.parsers"]}
    assert {"pymupdf", "docx", "markdown", "xlsx"}.issubset(parser_names)
    # 内置 extractor（去中心化：TableEntityExtractor 统管 register/pin/timing）
    ex_names = {p.name for p in by_group["docgraph.extractors"]}
    assert {"section", "table_entity", "glossary", "figure"}.issubset(ex_names)


def test_bootstrap_idempotent():
    from docgraph.core.bootstrap import bootstrap
    from docgraph.extractors.base import registry as ex_reg
    bootstrap()
    n1 = len(ex_reg.list_names())
    bootstrap()
    n2 = len(ex_reg.list_names())
    assert n1 == n2


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_migration_baseline(tmp_path):
    from docgraph.graph.migrations import (
        CURRENT_VERSION,
        current_db_version,
        needs_migration,
        run_migrations,
    )
    from docgraph.graph.sqlite_store import SQLiteGraphStore

    db = tmp_path / "g.db"
    store = SQLiteGraphStore(db)
    store.init_schema()
    store.close()

    # init_schema 应当把 db 带到最新版本
    assert current_db_version(db) == CURRENT_VERSION
    assert not needs_migration(db)
    applied = run_migrations(db)
    assert applied == []


def test_migration_fresh_db_runs_baseline(tmp_path):
    """空 db（无任何表）上跑 migrate 不应崩溃。"""
    from docgraph.graph.migrations import CURRENT_VERSION, current_db_version, run_migrations
    from docgraph.graph.sqlite_store import SQLiteGraphStore

    db = tmp_path / "fresh.db"
    store = SQLiteGraphStore(db)
    store.init_schema()
    # 清空版本号模拟旧 db
    import sqlite3
    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM schema_versions")
    conn.commit()
    conn.close()
    store.close()

    assert current_db_version(db) == 0
    applied = run_migrations(db)
    # 从 0 升级应跑全部 migration
    assert applied == list(range(1, CURRENT_VERSION + 1))
    assert current_db_version(db) == CURRENT_VERSION


# ---------------------------------------------------------------------------
# Federation
# ---------------------------------------------------------------------------


def test_federation_add_ls_rm(tmp_path):
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.linker.federate_mount import (
        add_federation,
        list_federations,
        remove_federation,
    )

    # 准备 local 项目
    local = tmp_path / "local"
    (local / ".docgraph").mkdir(parents=True)
    SQLiteGraphStore(local / ".docgraph" / "graph.db").init_schema()
    (local / "docgraph.yaml").write_text(
        "project:\n  family: local\n", encoding="utf-8"
    )

    # 准备 remote
    remote = tmp_path / "remote"
    (remote / ".docgraph").mkdir(parents=True)
    SQLiteGraphStore(remote / ".docgraph" / "graph.db").init_schema()
    (remote / "docgraph.yaml").write_text(
        "project:\n  family: remote-chip\n", encoding="utf-8"
    )

    entry = add_federation(local, remote)
    assert entry.family == "remote-chip"
    entries = list_federations(local)
    assert len(entries) == 1
    assert remove_federation(local, entry.name)
    assert list_federations(local) == []


def test_federated_store_query(tmp_path):
    from docgraph.graph.schema import Node, NodeKind
    from docgraph.graph.sqlite_store import SQLiteGraphStore
    from docgraph.graph.store import NodeQuery
    from docgraph.linker.federate_mount import FederatedGraphStore

    # 两个独立 db，各放一个 register
    a_path = tmp_path / "a.db"
    b_path = tmp_path / "b.db"
    a = SQLiteGraphStore(a_path); a.init_schema()
    b = SQLiteGraphStore(b_path); b.init_schema()
    a.upsert_node(Node(id="a::reg:R1", kind=NodeKind.REGISTER, name="R1", doc_id="da"))
    b.upsert_node(Node(id="b::reg:R2", kind=NodeKind.REGISTER, name="R2", doc_id="db"))

    fed = FederatedGraphStore(a, [b])
    # 跨库 search
    rs = fed.search_nodes(NodeQuery(kind=NodeKind.REGISTER, limit=10))
    assert {n.id for n in rs} == {"a::reg:R1", "b::reg:R2"}
    # 跨库 count
    assert fed.count_nodes(NodeKind.REGISTER) == 2
    # 远端 get_node
    assert fed.get_node("b::reg:R2") is not None
    fed.close()


# ---------------------------------------------------------------------------
# Config / Default config 健壮
# ---------------------------------------------------------------------------


def test_default_config_has_all_extractors():
    from docgraph.core.config import DEFAULT_CONFIG_YAML
    import yaml

    data = yaml.safe_load(DEFAULT_CONFIG_YAML)
    enabled = data["extractors"]["enabled"]
    for name in ("section", "table_entity", "glossary", "figure"):
        assert name in enabled


def test_default_config_validates():
    """Default YAML must round-trip through Pydantic."""
    from docgraph.core.config import DEFAULT_CONFIG_YAML, DocGraphConfig
    import yaml

    cfg = DocGraphConfig.model_validate(yaml.safe_load(DEFAULT_CONFIG_YAML))
    assert cfg.project.name
    assert "section" in cfg.extractors.enabled
