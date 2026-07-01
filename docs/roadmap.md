# 路线图与设计决策

> 对应 DESIGN.md §20 + §21。

## 路线图

历史 M1-M6 已并入当前实现，不再作为设计入口维护。当前路线图以 M7 分层架构为准：L0/L1 是事实底座，L2 是可选增强。

### M7 — 分层架构改造（进行中，最高优先级）

> 依据 [layered-architecture.md](./layered-architecture.md)。把重心从"实体抽取"转向"L0 无损版面 + L1 可检索索引"，根治"每项目调抽取器"与"预处理丢信息"两大问题。

- [x] **M7-P1（P0）L0 无损版面**：真实接入 Marker/MinerU；新增 `Block` + `TableData` 模型并落库（`blocks` 表）；表格保留单元格结构、图/公式入库；每个 block 带页码/坐标/章节，可回溯
- [x] **M7-P2a（P0）L1 基础落地**：`chunks` 表写入 + `chunk→block_ids` 回溯链 + FTS5 全文索引 + section-aware chunk + `section_node_id` / page range
- [x] **M7-P2b（P0）L1 检索增强**：L1 chunk 真实语义索引 + hybrid ranking + table profile + 保守 continued/logical-table 归并基础版。向量后端可插拔：默认本地 `sqlite_json`，可配置 LanceDB。
- [x] **M7-P2c（P0）L0/L1 生产质量门禁**：新增 `docgraph doctor`，校验 L0 block / L1 chunk / block 回溯 / FTS 一致性 / 表格 cell 与原始证据覆盖 / figure image 与 caption-only 证据覆盖；章节树支持 L1 fallback。
- [x] **M7-P3a（P1）L2 Candidate 输入层**：从 L1 chunk + L0 block 生成 `EntityCandidate`，统一 table/text/table_image/page_image/figure 抽取入口；L2 节点写入 `source_block_ids` / `source_chunk_ids`。
- [ ] **M7-P3b（P1）L2 生产召回评估**：建立 golden set，统计 candidate 覆盖率、schema 命中率、LLM/VLM 成功率、precision/recall/F1。
- [ ] **M7-P3c（P1）L2 schema 校准**：继续收紧 register/pin/timing/signal/interface/requirement/memory_map/interrupt 等 schema 的生产准确率。
- [ ] **M7-P4（P1）Agent 接口**：新增 `docgraph_blocks` / `docgraph_fetch`，增强 `docgraph_context` 返回可回溯原文片段；"读全文"不再是默认路径
- [ ] **M7-P5（P2）领域 schema 包**：register/pin/signal/interface/requirement/memory_map/interrupt 的 schema 预设集

**交付**：任意芯片文档（register manual / 接口 spec / 设计 spec）进来，**L0/L1 零调整**即可用；agent 在 RTL/验证/测试各阶段"先定位、再按需拉无损原文片段"，既省上下文又不丢信息。

## 已确认的设计决策

> 与用户讨论中明确的决定（按时间顺序）。这些是 ADR（Architecture Decision Record）的精简形式。

### ADR-001 使用模式对齐 codegraph

- **决定**：`docgraph init` + `.docgraph/` + MCP 工具集与 codegraph 风格一致
- **理由**：心智模型迁移成本最低；Agent 调用方式可复用

### ADR-002 PDF 为 P0，其它格式 P1

- **决定**：PDF 是首发目标，Word / MD / Excel 在 M3 接入
- **理由**：芯片 spec 90% 是 PDF；但 Parser 接口从 v0 起就支持多格式

### ADR-003 开源 + Apache 2.0

- **决定**：开源，license Apache 2.0
- **理由**：专利保护、商用友好、社区生态

### ADR-004 必须支持联邦

- **决定**：多 spec（datasheet/reference/errata）共存与覆盖
- **理由**：芯片 spec 真实世界场景；联邦不能后补

### ADR-005 SQLite + sqlite-vec 为默认存储

- **决定**：单 SQLite 库装节点 + 边 + 向量
- **理由**：零依赖部署、避免双库一致性问题；GraphStore 接口预留切换空间

### ADR-006 结构化检索与语义检索结合

- **决定**：L1 使用 FTS/LIKE + 语义候选 + 规则重排的 hybrid ranking；L2 图谱命中是加速路径，不是唯一入口。
- **理由**：芯片文档既需要精确定位寄存器/信号/章节，也需要覆盖长尾自然语言问题；L0/L1 保证信息不丢，L2 提升结构化查询效率。

### ADR-007 LLM/VLM 可选增强

- **决定**：L0/L1 不依赖 LLM/VLM；L2 的 TableEntity/Figure 语义增强按配置启用，失败时优雅降级。
- **理由**：芯片文档处理必须可离线、可审计；外部模型只提升召回和结构化程度，不得影响事实底座。

### ADR-008 每个节点/边必须带 evidence

- **决定**：evidence 字段不可省，含 page / bbox / chunk_id
- **理由**：Agent 可反查、人工可审计、错误可追溯

### ADR-009 插件用 entry points

- **决定**：不引入自定义插件加载器，复用 Python entry points
- **理由**：标准、生态成熟、`pip install` 即装即用

### ADR-010 Schema 自带 version

- **决定**：Node / Edge 都有 `schema_version`，migration 表跟踪
- **理由**：项目长期演进必备，越早越好

### ADR-011 三层架构：L0 无损 / L1 检索 / L2 增强（修订 ADR-006）

- **决定**：数据架构以 [layered-architecture.md](./layered-architecture.md) 为权威。L0 高保真版面无损保存、L1 切块+多索引、L2 实体抽取降为"可选增强"。
- **理由**：实跑 PCIe spec 暴露"每项目调抽取器""预处理丢信息"两大根因；通用性靠架构而非规则；预处理价值是"无损+可检索"而非"有损压缩成几个实体"。
- **影响**：修订 ADR-006（不再以实体图谱为唯一核心）；触发 M7 重构。

### ADR-012 L2 抽取从专用正则转向通用 schema-guided

- **决定**：用一个通用 `TableEntityExtractor` + schema registry 替代每种实体一个专用正则 extractor；register/pin/timing 降为 schema 预设。
- **理由**：新文档类型 = 注册一个 schema，而不是写一个新 extractor，根治"每项目调"。

### ADR-013 设计文档是唯一权威，代码紧跟文档

- **决定**：DESIGN.md + `docs/`（尤以 layered-architecture.md）是唯一权威设计；代码与文档冲突时改代码；重大变更先走 RFC 改文档再写代码；每个 PR 注明遵循/修订的设计条款。
- **理由**：避免实现漂移；保证架构意图不被零散提交侵蚀。

## 未决问题

- [ ] 是否提供托管版云服务？（短期内 No）
- [ ] 多用户协作（review、标注）的数据模型？
- [ ] 跨 family 联邦的具体 UX？
- [ ] L2 schema registry 是否需要 YAML 声明格式，还是保持 Pydantic 类注册？
- [ ] 跨页表归并从保守相邻策略升级到更强的 caption/table-id 归并？
