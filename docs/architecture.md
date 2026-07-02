# 架构总览

> 本文档对应 DESIGN.md §1–4。后续详细话题见 [data-model](./data-model.md)、[parsers](./parsers.md)、[extractors](./extractors.md) 等。

> ⭐ **数据架构以 [layered-architecture.md](./layered-architecture.md)（L0/L1/L2）为最高权威。** 本文档描述的"Parser→Extractor→Linker→Store"流水线是实现细节，其层次定位与契约须服从分层架构文档。两者冲突时以分层架构为准。

## 1. 项目定位

**DocGraph 是一个面向芯片 spec 文档的知识图谱引擎**，目标是让 spec-driven 芯片开发 Agent 拥有稳定、结构化、可追溯的文档查询接口，而不是把整篇 PDF 灌进 LLM 长上下文。

参考 `codegraph` 的使用模式：

| codegraph | docgraph |
|---|---|
| 代码符号（function / class） | 文档实体（register / pin / signal / figure / section …） |
| AST 边（calls / imports） | 文档边（contains / defines / references / connects_to …） |
| `codegraph init` → `.codegraph/` | `docgraph init` → `.docgraph/` |
| `codegraph_*` MCP 工具 | `docgraph_*` MCP 工具 |

### 目标

**P0 必须**：PDF 解析、L0 无损 blocks、L1 chunks + FTS/语义检索、`build/doctor/serve/search` 核心入口、MCP server、质量门禁。

**P1 重要**：L2 schema registry、寄存器/管脚/时序/信号/接口/需求等实体增强、agent fetch/blocks 接口、跨 spec 引用消歧。

**P2 远期**：更多 parser 后端、联邦 UX、EDA / IDE 集成、导出能力完善。

### 非目标

- 不是 OCR / PDF parser 本身，而是它们的**编排者**
- 不直接生成芯片代码（那是上层 Agent 的职责）
- 不是通用文档管理系统（不做权限、Web 编辑）
- 不绑定特定 LLM 供应商

## 2. 设计原则

冲突时按顺序优先：

1. **本地优先（Local-first）** —— 项目生成数据默认存在 `.docgraph/`，离线可用
2. **无损 > 抽取** —— L0/L1 先保证信息不丢；L2 抽取只是可选增强
3. **可插拔的边界** —— Parser / Extractor / Embedding / VLM / Storage 全部走接口注入
4. **幂等与可恢复** —— 每个 pipeline stage 必须幂等；任意阶段可单独重跑
5. **强 schema、强校验** —— Pydantic v2 全程把关；LLM 输出必须 schema 校验
6. **演进友好** —— schema 带 `version`；存储层自带 migration；不保留与当前分层契约冲突的旧接口
7. **可观测** —— 每条节点/边带 `evidence` + `confidence`
8. **成本意识** —— LLM/VLM 调用全缓存；增量按页粒度生效

## 3. 核心使用模式

```bash
cd my-chip-project/             # docs/ 下有 spec 文件
docgraph init                   # 初始化 .docgraph/；默认不生成项目配置文件
docgraph build                  # 全量构建
docgraph admin watch &                # 后台增量
docgraph serve --mcp            # MCP server，对接 Agent
docgraph search "PWM_CTRL bit 3" # CLI 查询
```

用户项目结构：

```
my-chip-project/
├── docs/
│   ├── datasheet.pdf
│   ├── reference-manual.pdf
│   ├── errata.pdf
│   └── app-note-pwm.docx
└── .docgraph/                  # 自动生成
    ├── manifest.json
    ├── graph.db
    ├── cache/
    ├── entities/
    └── logs/
```

用户级配置放在 `~/.docgraph/`，用于保存跨项目复用的模型、embedding、VLM 和密钥：

```
~/.docgraph/
├── config.yaml                 # 用户级模型/API key/base_url 配置
├── .env                        # 可选，环境变量兼容路径
└── .env.local                  # 可选，本机覆盖
```

`.docgraph/` 是纯生成目录，不存放手写配置。

项目级 `docgraph.yaml` 是可选文件。默认文档布局为 `docs/**/*.pdf` 和
`spec/**/*.pdf`，只有需要覆盖 docs 范围、parser、extractor 或 family 时才创建：

```
my-chip-project/
├── docgraph.yaml               # 可选，项目级覆盖配置
├── docs/
└── .docgraph/                  # 自动生成
```

## 4. 分层架构

```
┌──────────────────────────────────────────────────────────────────┐
│  Interface Layer                                                  │
│  ┌──────────┐  ┌────────────┐  ┌────────────┐  ┌──────────────┐  │
│  │  CLI     │  │ MCP Server │  │ Python SDK │  │ HTTP API*    │  │
│  └──────────┘  └────────────┘  └────────────┘  └──────────────┘  │
├──────────────────────────────────────────────────────────────────┤
│  Query Engine                                                     │
│   search / node / neighbors / context / trace / impact / ...      │
├──────────────────────────────────────────────────────────────────┤
│  Pipeline Orchestrator                                            │
│  Ingest → Parse → Extract → Link → Embed → Store                  │
├────────────────┬────────────────┬─────────────────┬───────────────┤
│ Parser Plugins │ Extractor      │ Linker          │ Embedding     │
├────────────────┴────────────────┴─────────────────┴───────────────┤
│  Storage Layer (Pluggable)                                        │
│  GraphStore (SQLite) + VectorStore (sqlite_json/LanceDB) + FS Cache│
└──────────────────────────────────────────────────────────────────┘
        ▲
        │ 监听
┌──────────────────────────────────────────────────────────────────┐
│  Watcher（增量触发）  +  Migration（schema 演进）                  │
└──────────────────────────────────────────────────────────────────┘
```

### 构建期数据流

```
docs/*.pdf
   │
   ├─► Ingestor      hash + manifest，决定跳过/重建
   ├─► Parser        MinerU/PyMuPDF/Docling/... → ParsedDoc + L0 Blocks
   ├─► Chunker       L1 chunks + FTS + vector index
   ├─► Extractors    section / table_entity / figure / glossary（L2 可选增强）
   ├─► Linker        xref + 实体消歧 + 联邦合并
   ├─► Embedder      L1 chunks / L2 nodes → 向量
   └─► Graph Store   SQLite 入图，更新 manifest
```

### 查询期数据流

```
Agent ── MCP ──► Query Engine
                     │
                     ├── Graph Query (SQLite + 递归 CTE)
                     ├── Vector Query (sqlite_json / LanceDB)
                     └── Hybrid Rerank
                     │
                     ▼
                结构化结果（节点 + 边 + 证据 + 摘要）
```

## 相关文档

- 数据模型与存储 → [data-model.md](./data-model.md)
- 每个层的实现细节 → [parsers.md](./parsers.md) / [extractors.md](./extractors.md) / [linker.md](./linker.md) / [retrieval.md](./retrieval.md)
- 联邦与多 spec → [federation.md](./federation.md)
- 路线图 → [roadmap.md](./roadmap.md)
