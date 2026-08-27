"""FastAPI app 构造 + uvicorn 启动入口。

复用 QueryEngine 实例（与 CLI / MCP 一致），让所有数据来自同一个 graph.db。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from docgraph.core.bootstrap import bootstrap
from docgraph.core.config import docgraph_dir, load_config, project_root_from_cwd
from docgraph.core.dotenv import autoload_env
from docgraph.core.logger import get_logger
from docgraph.embeddings.factory import open_query_embeddings
from docgraph.graph.sqlite_store import SQLiteGraphStore
from docgraph.query.engine import QueryEngine
from docgraph.version import __version__

log = get_logger(__name__)


def _build_engine(root: Path):
    autoload_env(root)
    cfg = load_config(root)
    bootstrap()
    store = SQLiteGraphStore(docgraph_dir(root) / "graph.db")
    store.init_schema()
    vstore, encoder = open_query_embeddings(cfg.embeddings, cfg.storage, docgraph_dir(root))
    return store, vstore, QueryEngine(store, vstore=vstore, encoder=encoder), cfg


def create_app(root: Path | None = None):
    """构造 FastAPI app。

    所有依赖（store / QueryEngine / config）在启动时一次性创建并放入 app.state，
    避免每请求都打开 SQLite。
    """
    try:
        from fastapi import FastAPI
        from fastapi.staticfiles import StaticFiles
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("fastapi/uvicorn not installed. Run: uv sync --extra web") from e

    root = root or project_root_from_cwd()
    if not docgraph_dir(root).is_dir():
        raise RuntimeError(f"No .docgraph/ found at {root}. Run 'docgraph init' first.")

    store, vstore, qe, cfg = _build_engine(root)

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            store.close()
            if vstore is not None:
                vstore.close()

    app = FastAPI(
        title="DocGraph",
        version=__version__,
        description="Document Knowledge Graph for chip specs",
        lifespan=lifespan,
    )
    app.state.root = root
    app.state.cfg = cfg
    app.state.store = store
    app.state.vstore = vstore
    app.state.qe = qe

    # 静态资源（少量）
    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # 路由
    from docgraph.web.routes import register_routes

    register_routes(app)

    return app


def run(
    root: Path | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = False,
) -> None:
    """启动 Web 服务（同步阻塞）。"""
    try:
        import uvicorn
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("uvicorn not installed. Run: uv sync --extra web") from e

    app = create_app(root)
    log.info(f"[web] DocGraph UI starting on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
