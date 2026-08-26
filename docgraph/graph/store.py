"""GraphStore 协议 —— 让上层不绑定具体存储后端。"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from docgraph.graph.schema import Edge, EdgeKind, Node, NodeKind


class NodeQuery(BaseModel):
    """节点查询条件。任一字段为 None 表示不过滤。"""

    name: str | None = None
    kind: NodeKind | None = None
    doc_id: str | None = None
    qualified_name: str | None = None
    alias: str | None = None
    fuzzy: str | None = None  # 子串 / LIKE 匹配
    limit: int = 50
    offset: int = 0


class Subgraph(BaseModel):
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)


class GraphStore(Protocol):
    """图谱存储的抽象接口。

    所有方法对幂等：upsert 重复写入同 id 是安全的。
    """

    # --- lifecycle ---
    def init_schema(self) -> None: ...
    def close(self) -> None: ...

    # --- node ---
    def upsert_node(self, node: Node) -> None: ...
    def get_node(self, id: str) -> Node | None: ...
    def delete_node(self, id: str) -> None: ...
    def search_nodes(self, query: NodeQuery) -> list[Node]: ...

    # --- edge ---
    def upsert_edge(self, edge: Edge) -> None: ...
    def neighbors(
        self,
        id: str,
        edge_kinds: list[EdgeKind] | None = None,
        depth: int = 1,
        limit: int = 50,
    ) -> Subgraph: ...

    # --- doc-level ---
    def delete_doc(self, doc_id: str) -> None: ...

    # --- stats ---
    def count_nodes(self, kind: NodeKind | None = None) -> int: ...
    def count_edges(self, kind: EdgeKind | None = None) -> int: ...
    def list_docs(self) -> list[str]: ...
