# 检索、查询引擎与 MCP

> 对应 DESIGN.md §10 + §11 + 附录 A。

## 1. 嵌入与向量

### 1.1 chunk 策略

- 以 **section 为主要语义单元**切块
- 表格、图、公式独立成 chunk，并保留 `block_ids` 回溯
- 长 section 按 ~512 token 滑窗切，overlap 64

### 1.2 嵌入

- 默认使用本地轻量 hash encoder，保证离线和低成本可用
- 通过 `EmbeddingProvider` 接口，可替换 OpenAI-compatible / Voyage / 自部署 / 本地模型
- 不同模型并存：`chunk_vec_map` 记录每个 chunk 在哪些模型下有向量
- 模型升级 → 旧向量打 deprecated，按需重算

### 1.3 EmbeddingProvider 接口

```python
class EmbeddingProvider(Protocol):
    name: str
    dim: int
    model: str

    def encode(self, texts: list[str]) -> list[list[float]]: ...
```

## 2. 查询引擎

### 2.1 输入分类

```
Agent / CLI 查询
   │
   ├── 自然语言问题                   → L1 chunk 检索 → L0 block 回溯
   ├── 结构化命中(exact name / id)     → L2 候选 → L1/L0 证据验证
   └── 图谱遍历(neighbors/impact/trace) → 候选关系 → 证据验证
```

### 2.2 Rerank

默认权重：
1. ID / qualified_name 精确命中（最高）
2. 别名匹配
3. 图谱邻居加权
4. 向量相似度

后期可接 cross-encoder rerank。

## 3. MCP 工具集

| 工具 | 用途 |
|---|---|
| `docgraph_status` | 索引健康、统计信息 |
| `docgraph_files` | 列出/过滤已索引文档 |
| `docgraph_search_chunks` | 检索 L1 chunks，返回 snippet、page、block_ids |
| `docgraph_fetch` | 取一个 L1 chunk 及其原始 L0 blocks |
| `docgraph_blocks` | 按 block id 取 L0 原始文本、表格、图片路径 |
| `docgraph_sources` | 按 L2 node id 取 source chunks 和 source blocks |
| `docgraph_search` | 搜 L2 节点候选，返回 `source_quality` |
| `docgraph_node` | 拿单节点详情，返回 `source_quality` |
| `docgraph_neighbors` | 图遍历，返回候选关系，关键关系需回溯证据 |
| `docgraph_context` | **主入口**：按 task 返回 L2 候选 + L1 chunks + L0 blocks |
| `docgraph_trace` | from→to 的关系路径 |
| `docgraph_impact` | 改某节点影响哪些下游 |
| `docgraph_register` | 寄存器专项（含所有 bitfields） |
| `docgraph_pin` | 管脚专项 |
| `docgraph_timing` | 时序参数 |
| `docgraph_figure` | 图（含 mermaid + 描述） |
| `docgraph_section` | 章节正文 |
| `docgraph_glossary` | 术语 / 缩写 |

### 3.1 输出格式约束

- 全部 JSON，UTF-8
- 大文本（图描述、章节正文）放 `body`，其余字段平铺
- 永远附带 `evidence`（page、doc_id）
- L2 节点输出必须带 `source_quality`，至少包含 `source`、`extraction_confidence`、`needs_source_check`、`source_block_ids`、`source_chunk_ids`
- `needs_source_check=true` 的节点或关系只能作为候选，不得作为最终事实直接使用
- 数量大时支持 `limit` + `offset`，返回 `total`

### 3.2 Agent 调用规范

DocGraph MCP 的生产使用路径是 evidence-first：

1. 自然语言任务先调用 `docgraph_context` 或 `docgraph_search_chunks`。
2. 用 `docgraph_fetch` / `docgraph_blocks` 读取 L1/L0 原文证据。
3. 只有在证据可回溯时，才把 `docgraph_search`、`docgraph_neighbors`、`docgraph_trace` 得到的 L2 节点/关系写入结论。
4. 对 `figure@*`、`vlm`、`llm` 或缺少 `extraction_confidence` 的节点，必须用 `docgraph_sources` 回到原始图、表或正文验证。

## 4. API 详细签名

### 4.1 CLI

```
docgraph init [--name=foo] [--family=foo]
docgraph build [--doc=PATH] [--quality=fast|balanced|accurate]
                [--force] [--limit-cost=USD]
docgraph serve [--mcp] [--web] [--http] [--port=7331] [--bind=127.0.0.1]
docgraph search "<natural language>"
docgraph status [--cost] [--per-doc]
docgraph doctor [--strict]
docgraph l2 audit [--schema=register]
docgraph l2 eval --golden=PATH
docgraph inspect register NAME
docgraph inspect pin NAME
docgraph graph trace FROM TO
docgraph graph impact NAME [--depth=2]
docgraph admin watch [--paths=docs/]
docgraph admin review [--min-confidence=0.5]
docgraph admin plugins {ls|info}
docgraph admin federate {add|ls|rm}
docgraph export {ipxact|systemrdl} --register=NAME
```

### 4.2 MCP

```python
docgraph_status() -> StatusReport
docgraph_files(path: str | None = None, pattern: str | None = None) -> list[FileInfo]
docgraph_search_chunks(query: str, limit: int = 20) -> list[ChunkHit]
docgraph_fetch(chunk_id: str) -> ChunkWithBlocks
docgraph_blocks(block_ids: list[str]) -> list[Block]
docgraph_sources(id: str) -> NodeWithSources
docgraph_search(query: str, kind: NodeKind | None = None, limit: int = 10) -> list[NodeBrief]
docgraph_node(id: str) -> NodeDetail
docgraph_neighbors(id: str, edge_kinds: list[EdgeKind] | None = None,
                   depth: int = 1, limit: int = 50) -> Subgraph
docgraph_context(task: str, max_nodes: int = 20) -> EvidenceBundle
docgraph_trace(from_id: str, to_id: str, max_depth: int = 5) -> list[Path]
docgraph_impact(id: str, depth: int = 2) -> ImpactReport
docgraph_register(name: str) -> RegisterDetail
docgraph_pin(name: str) -> PinDetail
docgraph_timing(name: str) -> TimingDetail
docgraph_figure(id: str) -> FigureDetail
docgraph_section(path_or_id: str) -> SectionDetail
docgraph_glossary(term: str) -> list[TermDetail]
```

### 4.3 Python SDK

```python
from docgraph import DocGraph

dg = DocGraph.open(".docgraph/")
reg = dg.register("PWM_CTRL")
for bf in reg.bitfields:
    print(bf.name, bf.bit_range, bf.description)

ctx = dg.context("实现 PWM 100kHz 输出")
```

## 5. 工具设计原则

- **Agent 优先**：所有签名 LLM 友好（紧凑 JSON、有 docstring）
- **组合优先**：`docgraph_context` 一次问清，避免 Agent 拼调用
- **稳定优先**：工具签名走 deprecation 周期；新增字段可加，删字段要走流程

## 相关文档

- 配置（嵌入模型、检索权重）→ [configuration.md](./configuration.md)
- 联邦查询语义 → [federation.md](./federation.md)
