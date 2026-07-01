# CLAUDE.md — DocGraph 工程约定（编码 agent 必读）

> 本文件在每次会话加载，用于约束在本仓库写代码的行为。**设计文档优先级高于一切临时指令。**

## 0. 最高约定：设计文档是唯一权威

写任何代码前，先读并对齐这些文档（按权威从高到低）：

1. **[docs/layered-architecture.md](./docs/layered-architecture.md)** —— 数据架构最高权威（L0 无损版面 / L1 检索 / L2 实体增强）
2. [DESIGN.md](./DESIGN.md) —— 顶层索引
3. [docs/](./docs/) —— 各话题文档
4. [docs/roadmap.md](./docs/roadmap.md) —— 里程碑与 ADR

规则：

- **代码必须紧跟设计文档。** 实现与文档冲突时，改代码、不改文档。
- 要改变设计，先走 [RFC](./docs/rfcs/) 更新文档，评审通过后再写代码。
- 每次提交/PR 注明：遵循或修订了哪条设计条款（如 "遵循 layered-architecture §3.1 Block 模型"）。

## 1. 分层契约（硬约束，不可违反）

来自 layered-architecture.md §2：

- **L0 必须无损**：Parser 不允许把表格丢成 `[]`；表格保留单元格结构，图/公式入库，block 带页码/坐标/章节。
- **L1 必须可寻址可回溯**：chunk 有稳定 ID，能反查 L0（`chunk.block_ids`）。
- **L2 是可选增强，不得成为唯一入口**：L2 抽取失败绝不能导致信息丢失；agent 永远能绕过 L2 直达 L1/L0。
- **L2 节点必须带 `source_block_ids` / `source_chunk_ids`**，且 `evidence` 非空。
- **agent 默认路径 = L1 检索 → 按需取 L0 片段 → L2 命中则直取**；禁止把"读全文"作为常规路径。

## 2. 当前阶段：M7 分层重构（最高优先级）

见 roadmap.md M7。重心是 **L0/L1**（无损版面 + 可检索索引），不是再加领域 extractor。
新增实体类型应通过 **schema registry**（ADR-012），不是新写专用正则 extractor。

## 3. 工程基线

- Python 3.11+，Pydantic v2 承载所有跨模块数据。
- 虚拟环境：`source .venv/bin/activate`（本机已建）。
- 测试：`python -m pytest tests/ -q`，提交前必须全绿。
- Lint：`ruff check docgraph/`。
- 不引入重依赖到核心；重型 parser（Marker/MinerU）/ LLM / web 走 `optional-dependencies` extras，按需 import。
- LLM/VLM 配置走 `.env`（`autoload_env`）；VLM 走独立 `VLM_*` 变量。
- 所有 LLM/VLM 调用必须缓存 + 成本追踪 + 失败优雅降级。

## 4. 提交前自检清单

- [ ] 与 layered-architecture.md 一致？层次定位对吗？
- [ ] 没有违反 L0/L1/L2 契约？
- [ ] 新实体走 schema registry 而非新写正则 extractor？
- [ ] 测试全绿？
- [ ] PR 描述注明了遵循/修订的设计条款？
