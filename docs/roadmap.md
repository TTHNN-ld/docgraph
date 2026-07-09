# 路线图与设计决策

> 本文档记录当前产品基线、近期工程重点和稳定架构决策。需求变化记录见 [requirements-changelog.md](./requirements-changelog.md)。

## 当前产品基线

- **L0 高保真版面**：PDF/DOCX/XLSX/MD 统一归一为 `ParsedDoc` 与 L0 `Block`；表格保留单元格、HTML/图片证据，图、公式、页码、坐标和阅读顺序可回溯。
- **L1 可检索索引**：章节、表格、图形成稳定 chunk；支持 `block_ids` 回溯、FTS5、章节路径、table profile、continued table 保守归并和可插拔语义索引。
- **L2 实体增强**：通过 schema registry + `TableEntityExtractor` 抽取 register、bitfield、pin、signal、interface、requirement、memory_map、interrupt 等实体；节点必须带 `source_block_ids`、`source_chunk_ids` 和 evidence。
- **质量门禁**：`docgraph doctor --strict` 检查 L0/L1 完整性、FTS 一致性、表格/图证据、L2 provenance 和强结构约束；`docgraph l2 audit` 与 `docgraph l2 eval` 用于候选覆盖和 golden 评估。
- **安装与配置**：用户级配置位于 `~/.docgraph/`；项目级 `docgraph.yaml` 可选；项目 `.docgraph/` 只保存生成数据库、缓存、manifest、日志和导出结果。
- **默认体验**：`docgraph init` 创建项目运行目录与用户配置；普通项目无需维护配置文件，默认扫描 `docs/**/*.pdf` 与 `spec/**/*.pdf`，PDF 默认使用 PyMuPDF。

## 近期工程重点

1. **L2 生产评估集**：为目标芯片文档集建立 golden set，按实体类型统计 candidate 覆盖率、schema 命中率、LLM/VLM 成功率、precision、recall 和 F1。
2. **L2 schema 校准**：持续收紧 register、bitfield、pin、signal、interface、memory_map、interrupt、requirement 的必填字段、值域、证据和冲突处理。
3. **Agent fetch 接口**：补齐 MCP/CLI 的 block、chunk、section 原文拉取能力，让默认路径稳定落在“L1 定位 → L0 取证 → L2 加速”。
4. **Parser adapter 扩展**：在不改变 L0/L1 契约的前提下接入 Docling、Marker、Excel 专项表格解析等后端。
5. **生产运维闭环**：将 `doctor`、`l2-audit`、`l2-eval`、成本统计和构建耗时纳入 CI/批处理流程。

## 已确认的设计决策

### ADR-001 使用模式对齐 codegraph

- **决定**：使用 `docgraph init`、项目 `.docgraph/`、MCP 工具集和本地索引的工作方式。
- **理由**：降低 Agent 和工程师的心智迁移成本。

### ADR-002 PDF 为首要输入格式

- **决定**：PDF 是 P0；Word、Markdown、Excel 通过同一 parser adapter 契约接入。
- **理由**：芯片 spec 主要以 PDF 分发，但系统不能绑定单一文件格式。

### ADR-003 开源 + Apache 2.0

- **决定**：项目采用 Apache 2.0 许可证。
- **理由**：商用友好，便于企业内部和社区协作。

### ADR-004 支持联邦文档集

- **决定**：同一项目可包含 datasheet、reference manual、TRM、errata、app note 等多份文档，并支持优先级和覆盖关系。
- **理由**：真实芯片资料以多文档集合存在，单 PDF 图谱不足以支撑工程使用。

### ADR-005 SQLite 为默认存储

- **决定**：默认使用本地 SQLite 保存图谱、L0/L1/L2 数据和索引元数据；向量后端可插拔。
- **理由**：部署简单、可离线、便于版本化和调试。

### ADR-006 结构化检索与语义检索结合

- **决定**：L1 使用 FTS/LIKE + 语义候选 + 规则重排的 hybrid ranking；L2 图谱命中是加速路径，不是唯一入口。
- **理由**：芯片文档既有精确名称查询，也有自然语言定位需求。

### ADR-007 LLM/VLM 可选增强

- **决定**：L0/L1 不依赖 LLM/VLM；L2 的 TableEntity/Figure 语义增强按配置启用，失败时优雅降级。
- **理由**：事实底座必须可离线、可审计、可重复构建。

### ADR-008 每个 L2 节点/边必须带 evidence

- **决定**：L2 节点和边必须记录来源页、block、chunk、extractor 和证据文本/图片。
- **理由**：Agent 可反查，人工可审计，错误可定位。

### ADR-009 插件用 Python entry points

- **决定**：Parser、Extractor、Linker、Store 后端通过 Python entry points 扩展。
- **理由**：复用成熟生态，`pip install` 即可安装插件。

### ADR-010 Schema 自带 version

- **决定**：Node、Edge 和实体 schema 都带版本，migration 表记录升级。
- **理由**：长期演进需要可控迁移。

### ADR-011 三层架构：L0 无损 / L1 检索 / L2 增强

- **决定**：数据架构以 [layered-architecture.md](./layered-architecture.md) 为权威。L0 高保真保存，L1 多索引检索，L2 实体增强。
- **理由**：避免把系统价值绑定到一次实体抽取结果，确保信息不丢失且可定位。

### ADR-012 L2 抽取采用 schema-guided 机制

- **决定**：统一候选层 + schema registry + 通用 `TableEntityExtractor` 是 L2 默认抽取路径；实体类型优先通过 schema 扩展。
- **理由**：新增文档类型时应扩展 schema，而不是为每类表格重写专用抽取器。

### ADR-013 设计文档是唯一权威

- **决定**：`docs/layered-architecture.md`、`DESIGN.md` 和 `docs/` 是稳定设计入口；重大架构变更先更新文档和 RFC，再改代码。
- **理由**：避免实现漂移和临时任务说明进入长期文档。

### ADR-014 配置与生成物分离

- **决定**：用户级配置放在 `~/.docgraph/`；项目级 `docgraph.yaml` 可选；项目 `.docgraph/` 只保存生成物。
- **理由**：普通用户开箱即用，团队项目不需要提交本地数据库和密钥，新增文档后可直接增量构建。

### ADR-015 语义知识图谱：IP-XACT 对齐本体 + 三层混合抽取

- **决定**：L2 升级为语义知识图谱。本体对齐 IP-XACT（IEEE 1685-2022），关系类型化（`belongs_to` / `contained_in` / `mapped_to` / `drives` / `clocks` / `resets` / `implements`）。抽取分三层：A 确定性事实（表格，保留）、B 确定性关系推断（章节归属 + 地址 join + 名字 join，新增）、C LLM 开放 IE（GraphRAG 式、本体约束，新增）。溯源移出图边（保留在节点 attrs），图谱边只留语义关系 + `has_bitfield`。详见 [RFC 0015](./rfcs/0015-semantic-kg-hybrid-extraction.md)。
- **理由**：当前图谱 81% 边是溯源/结构，语义关系稀缺，无法回答芯片语义问题；纯规则不可扩展，纯 GraphRAG 对精确事实有幻觉风险。分层各取其长，B 层零 LLM 成本补大半语义边。

## 未决问题

- 多用户协作、人工 review 和标注数据的产品形态。
- 跨 family 联邦查询的用户体验。
- L2 schema registry 是否需要稳定 YAML 声明格式。
- 跨页表归并从保守相邻策略升级到 caption/table-id/版面特征联合策略。
