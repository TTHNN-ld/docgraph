# 检索、查询引擎与 MCP

> 对应 DESIGN.md §10 + §11 + 附录 A。

## 1. 嵌入与向量

### 1.1 chunk 策略

- 以 **section 为最小语义单元**切块
- 寄存器、管脚、时序参数**不切**——整体一个 chunk，与 graph 节点 1:1 关联
- 长 section 按 ~512 token 滑窗切，overlap 64

### 1.2 嵌入

- 默认 `bge-m3`（中英双语、1024 维）
- 通过 `EmbeddingProvider` 接口，可替换 OpenAI / Voyage / 自部署
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
   ├── 结构化命中（exact name / id）  → Graph 直查
   ├── 图谱遍历（neighbors / impact / trace） → 递归 CTE
   └── 语义查询（自然语言）           → 向量 → top-k → 反查节点
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
| `docgraph_search` | 按名字搜节点 |
| `docgraph_node` | 拿单节点详情（含可选邻居） |
| `docgraph_neighbors` | 图遍历（深度可控） |
| `docgraph_context` | **主入口**：组合搜索 + 邻居 + 摘要，按 task 返回相关包 |
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
- 数量大时支持 `limit` + `offset`，返回 `total`

## 4. API 详细签名

### 4.1 CLI

```
docgraph init [--name=foo] [--family=foo]
docgraph build [--doc=PATH] [--stage=parse|extract|link|embed]
                [--force] [--limit-cost=USD]
docgraph rebuild --doc=PATH
docgraph watch [--paths=docs/]
docgraph serve [--mcp] [--http] [--port=7331] [--bind=127.0.0.1]
docgraph query "<natural language>"
docgraph register NAME
docgraph pin NAME
docgraph timing NAME
docgraph trace FROM TO
docgraph impact NAME [--depth=2]
docgraph status [--cost] [--per-doc]
docgraph review [--min-confidence=0.5]
docgraph eval [--golden=PATH]
docgraph plugins {ls|enable|disable}
docgraph federate {add|ls|rm}
docgraph prune [--older-than=30d]
docgraph export {ip-xact|systemrdl} --register=NAME
```

### 4.2 MCP

```python
docgraph_status() -> StatusReport
docgraph_files(path: str | None = None, pattern: str | None = None) -> list[FileInfo]
docgraph_search(query: str, kind: NodeKind | None = None, limit: int = 10) -> list[NodeBrief]
docgraph_node(id: str, include_neighbors: bool = False) -> NodeDetail
docgraph_neighbors(id: str, edge_kinds: list[EdgeKind] | None = None,
                   depth: int = 1, limit: int = 50) -> Subgraph
docgraph_context(task: str, max_nodes: int = 20) -> ContextBundle
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
