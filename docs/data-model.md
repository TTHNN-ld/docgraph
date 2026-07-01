# 数据模型与存储

> 对应 DESIGN.md §5 + §6 + 附录 B。

## 1. 节点（Node）

### 1.1 通用字段

```python
class Node(BaseModel):
    schema_version: int            # 迁移用
    id: str                        # 全局唯一，e.g. "stm32f4::reg:PWM_CTRL"
    kind: NodeKind                 # 见下
    name: str
    qualified_name: str            # 含 module/scope
    aliases: list[str] = []
    doc_id: str
    location: Location             # 页码 + bbox + section_path
    attrs: dict                    # kind 专属字段（受子 schema 约束）
    summary: str | None
    embedding_id: int | None
    hash: str
    created_at: str
    updated_at: str
```

### 1.2 节点类型

| 类型 | 说明 |
|---|---|
| `DOCUMENT` | spec 整体 |
| `SECTION` | 章节（树形） |
| `REGISTER` | 寄存器 |
| `BITFIELD` | 位域 |
| `PIN` | 物理管脚 |
| `SIGNAL` | 内部信号 |
| `MODULE` | 功能模块 / IP |
| `INTERFACE` | 总线 / 协议接口 |
| `PARAMETER` | 时序 / 电气参数 |
| `FIGURE` | 图（block / timing / fsm / waveform） |
| `TABLE` | 非寄存器的通用表 |
| `FORMULA` | 公式 |
| `CODEBLOCK` | Verilog / 伪代码 |
| `TERM` | 术语 / 缩写 |
| `CHUNK` | 检索用文本片段 |

每个 kind 对应一份 `attrs_schema`，由对应的 Extractor 定义。

## 2. 边（Edge）

```python
class Edge(BaseModel):
    schema_version: int
    src: str
    dst: str
    kind: EdgeKind
    confidence: float              # 0.0 - 1.0
    evidence: Evidence
    attrs: dict = {}
    created_at: str
```

**核心边类型**：

| 类型 | 含义 |
|---|---|
| `CONTAINS` | 父子包含（section → register） |
| `DEFINES` | 定义关系（section → register） |
| `HAS_BITFIELD` | register → bitfield |
| `BELONGS_TO` | signal → interface |
| `CONNECTS_TO` | pin ↔ signal |
| `CONTROLS` | bitfield → module/signal 行为 |
| `DEPENDS_ON` | 配置依赖 |
| `CONSTRAINS` | parameter → signal |
| `REFERENCES` | 自由文本交叉引用 |
| `ILLUSTRATED_BY` | concept → figure |
| `ALIAS_OF` | 同物异名 |
| `SUPERSEDES` | errata 覆盖原段（联邦关键） |
| `DERIVED_FROM` | 抽取得来（追溯） |

扩展边类型通过插件注册，见 [plugins.md](./plugins.md)。

## 3. 证据（Evidence）

```python
class Evidence(BaseModel):
    chunk_ids: list[str] = []
    pages: list[int] = []
    bboxes: list[BBox] = []
    extractor: str                 # 抽取器标识 + 版本
    raw_snippet: str | None
```

**核心约束**：写入图谱的每个节点和每条边都必须带 evidence。这让 Agent 可以反查原文，让人工 review 成为可能。

## 4. 存储设计

### 4.1 选型理由

| 数据 | 存储 | 理由 |
|---|---|---|
| 节点 + 边 | **SQLite** | 单文件、零依赖、CTE 足够；几十万节点无压力 |
| 向量 | **sqlite-vec** | 与图同库，避免一致性问题 |
| Parser 原始输出 | **文件系统 + JSONL** | 大、便于 diff 调试 |
| Manifest | **JSON** | 小、人读、git 友好 |
| 配置 | **YAML** | 用户编辑 |

**为什么不用 Neo4j**：部署成本远高于收益。SQLite + 递归 CTE 已足够。GraphStore 接口预留切换空间。

### 4.2 核心表

```sql
-- 节点
CREATE TABLE nodes (
  id              TEXT PRIMARY KEY,
  kind            TEXT NOT NULL,
  name            TEXT NOT NULL,
  qualified_name  TEXT,
  doc_id          TEXT NOT NULL,
  page            INTEGER,
  bbox            TEXT,            -- JSON
  section_path    TEXT,
  attrs           TEXT,            -- JSON
  summary         TEXT,
  embedding_id    INTEGER,
  hash            TEXT,
  schema_version  INTEGER,
  created_at      TEXT,
  updated_at      TEXT
);
CREATE INDEX idx_nodes_kind_name      ON nodes(kind, name);
CREATE INDEX idx_nodes_qualified_name ON nodes(qualified_name);
CREATE INDEX idx_nodes_doc            ON nodes(doc_id);

-- 别名
CREATE TABLE aliases (
  alias    TEXT NOT NULL,
  node_id  TEXT NOT NULL,
  PRIMARY KEY (alias, node_id),
  FOREIGN KEY(node_id) REFERENCES nodes(id) ON DELETE CASCADE
);
CREATE INDEX idx_aliases_alias ON aliases(alias);

-- 边
CREATE TABLE edges (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  src         TEXT NOT NULL,
  dst         TEXT NOT NULL,
  kind        TEXT NOT NULL,
  confidence  REAL,
  evidence    TEXT,
  attrs       TEXT,
  created_at  TEXT,
  FOREIGN KEY(src) REFERENCES nodes(id) ON DELETE CASCADE,
  FOREIGN KEY(dst) REFERENCES nodes(id) ON DELETE CASCADE
);
CREATE INDEX idx_edges_src       ON edges(src, kind);
CREATE INDEX idx_edges_dst       ON edges(dst, kind);
CREATE INDEX idx_edges_kind_conf ON edges(kind, confidence);

-- 文本片段
CREATE TABLE chunks (
  id          TEXT PRIMARY KEY,
  doc_id      TEXT NOT NULL,
  page        INTEGER,
  section_id  TEXT,
  text        TEXT NOT NULL,
  hash        TEXT,
  attrs       TEXT
);

-- 向量
CREATE VIRTUAL TABLE vec_chunks USING vec0(
  id INTEGER PRIMARY KEY,
  embedding FLOAT[1024]
);
CREATE TABLE chunk_vec_map (
  chunk_id   TEXT PRIMARY KEY,
  vec_id     INTEGER NOT NULL,
  model      TEXT NOT NULL,
  dim        INTEGER NOT NULL
);

-- 文件清单（增量基石）
CREATE TABLE manifest (
  path       TEXT PRIMARY KEY,
  doc_id     TEXT,
  hash       TEXT,
  mtime      REAL,
  size       INTEGER,
  parser     TEXT,
  status     TEXT,                 -- pending|parsed|extracted|linked|embedded|error
  stage_log  TEXT,
  last_run   TEXT
);

-- Schema 版本
CREATE TABLE schema_versions (
  component  TEXT PRIMARY KEY,
  version    INTEGER NOT NULL,
  applied_at TEXT
);
```

### 4.3 Migration

- `docgraph/graph/migrations/NNN_xxx.sql|py` 顺序应用
- `docgraph build` 启动时自动检查 `schema_versions`
- 迁移前自动备份 `.docgraph/graph.db` → `.docgraph/graph.db.bak.<ts>`

### 4.4 GraphStore 抽象

```python
class GraphStore(Protocol):
    def upsert_node(self, node: Node) -> None: ...
    def upsert_edge(self, edge: Edge) -> None: ...
    def get_node(self, id: str) -> Node | None: ...
    def search_nodes(self, query: NodeQuery) -> list[Node]: ...
    def neighbors(self, id: str, edge_kinds: list[EdgeKind] | None,
                  depth: int = 1) -> Subgraph: ...
    def find_path(self, src: str, dst: str, max_depth: int) -> list[Path]: ...
    def delete_doc(self, doc_id: str) -> None: ...
```

默认实现 `SQLiteGraphStore`。未来切 Neo4j / DuckDB 只需替换实现。

## 5. Schema 示例

### 5.1 Register

```json
{
  "schema_version": 1,
  "id": "stm32f407::reg:TIM1.CR1",
  "kind": "register",
  "name": "CR1",
  "qualified_name": "TIM1.CR1",
  "aliases": ["TIM1_CR1"],
  "doc_id": "stm32f407::reference_manual@rev9",
  "location": {
    "page": 562,
    "bbox": [72, 105, 540, 410],
    "section_path": "17.4.1"
  },
  "attrs": {
    "address": "0x40010000",
    "offset": "0x00",
    "width": 16,
    "access": "RW",
    "reset_value": "0x0000",
    "module_id": "stm32f407::module:TIM1",
    "bitfields": ["stm32f407::bf:TIM1.CR1.CEN", "stm32f407::bf:TIM1.CR1.UDIS"]
  },
  "summary": "TIM1 control register 1 — counter enable, update event control.",
  "hash": "sha256:..."
}
```

### 5.2 Figure (timing)

```json
{
  "id": "stm32f407::fig:14-3",
  "kind": "figure",
  "name": "Figure 14-3",
  "doc_id": "stm32f407::reference_manual@rev9",
  "location": { "page": 482, "section_path": "14.3.4" },
  "attrs": {
    "figure_type": "timing",
    "image_path": ".docgraph/cache/<hash>/figures/fig_14-3.png",
    "wavejson": { "signal": [{ "name": "CLK", "wave": "p......" }] },
    "vlm_desc": "时序图显示 SPI 主模式下 SCK、MOSI、MISO 的相位关系..."
  },
  "summary": "SPI master timing — CPOL=0, CPHA=0"
}
```

### 5.3 Edge (SUPERSEDES)

```json
{
  "src": "stm32f407::reg:TIM1.CR1#errata@rev3",
  "dst": "stm32f407::reg:TIM1.CR1#reference_manual@rev9",
  "kind": "supersedes",
  "confidence": 1.0,
  "evidence": {
    "pages": [12],
    "extractor": "errata_extractor@0.1",
    "raw_snippet": "Erratum 2.3.1: TIM1_CR1.CEN behavior is corrected..."
  }
}
```
