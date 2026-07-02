# 需求变更记录

> 本文件记录已确认的需求变化、决策和影响范围。正式设计文档只保留当前稳定事实；需求演进过程统一沉淀在这里。

## 记录格式

- **日期**：YYYY-MM-DD
- **需求**：用户或项目明确提出的新要求
- **决策**：采用的产品/架构方案
- **影响范围**：涉及的代码、配置、文档或使用方式
- **状态**：Accepted / Implemented / Superseded

## 2026-07-02：配置与生成物分离

- **需求**：DocGraph 应像 codegraph 和 Claude Code 一样按用户安装使用。大模型、embedding、VLM 和密钥配置放到用户目录；项目执行 `docgraph init` 后只产生项目本地运行目录。
- **决策**：
  - 用户级配置放在 `~/.docgraph/config.yaml`。
  - 用户级环境变量放在 `~/.docgraph/.env.local` 与 `~/.docgraph/.env`。
  - 项目级 `docgraph.yaml` 变为可选覆盖文件，只在需要设置 family、文档范围、parser/extractor 策略时使用。
  - 项目 `.docgraph/` 是纯生成目录，保存 `graph.db`、缓存、manifest、日志和导出结果。
  - `docgraph init` 默认不生成项目配置文件；传入 `--name` 或 `--family` 时生成最小 `docgraph.yaml`。
- **影响范围**：CLI init、配置加载、dotenv 自动加载、README、configuration、architecture、layered architecture。
- **状态**：Implemented

## 2026-07-02：模型密钥集成到用户配置

- **需求**：用户不希望额外维护 `~/.docgraph/.env`；模型、embedding、VLM 的 key 和 endpoint 应像 Claude Code 一样集中在用户级配置中。
- **决策**：
  - `~/.docgraph/config.yaml` 支持直接配置 `llm.providers.<name>.api_key` / `base_url`。
  - `embeddings` 支持直接配置 `api_key` / `base_url`。
  - `llm.vlm` 支持独立配置 `provider` / `model` / `api_key` / `base_url`，允许文本 LLM 与 VLM 使用不同服务。
  - `.env` 和环境变量保留为兼容、CI 和临时覆盖路径，但不再是推荐主路径。
- **影响范围**：配置模型、LLM provider、VLM provider、embedding provider、configuration、cookbook、operations。
- **状态**：Implemented

## 2026-07-02：默认开箱体验收敛

- **需求**：普通用户不应维护繁琐配置文件；默认即可在项目内跑起来。
- **决策**：
  - 默认扫描 `docs/**/*.pdf` 与 `spec/**/*.pdf`。
  - PDF 默认 parser 为轻量 PyMuPDF，保证安装后可直接构建 L0/L1。
  - MinerU 作为复杂芯片 PDF 的高保真推荐后端，通过可选 `docgraph.yaml` 显式开启，并建议配置 PyMuPDF fallback。
  - 日常命令保持少量核心入口：`docgraph init`、`docgraph build`、`docgraph doctor`、`docgraph serve` 和检索/查询类命令。
- **影响范围**：配置默认值、Parser 文档、README、roadmap。
- **状态**：Implemented

## 2026-07-02：L2 生产质量要求

- **需求**：L2 知识图谱是项目高收益层，不能依赖偶然的 LLM/VLM 输出，必须能在芯片文档中保证高召回、高准确和可审计。
- **决策**：
  - L2 抽取源数据统一来自 L1 chunk 与 L0 block，包括表格、正文、表格图、页图和 figure。
  - 表格实体优先走确定性 normalizer；LLM/VLM 只作为结构化增强和兜底。
  - L2 节点必须带 `source_block_ids`、`source_chunk_ids` 和 evidence。
  - register/bitfield、signal/interface、interrupt、memory_map 等实体进入强结构校验。
  - `docgraph l2 audit` 用于候选覆盖诊断，`docgraph l2 eval` 用于 golden set precision/recall/F1 评估。
- **影响范围**：Extractor、schema registry、doctor、l2-audit、l2-eval、operations、layered architecture、roadmap。
- **状态**：Implemented；生产导入前仍需针对目标文档集建立 golden set。

## 2026-07-02：文档收口

- **需求**：删除中间态和阶段性说明，文档只反映当前最新设计；后续需求变化集中记录。
- **决策**：
  - 顶层设计入口保留 `DESIGN.md`、`docs/layered-architecture.md` 和主题文档。
  - `docs/roadmap.md` 改为当前产品基线、近期工程重点和稳定 ADR。
  - 需求变化统一写入本文件，避免把临时讨论边界写进正式设计文档。
- **影响范围**：README、DESIGN、roadmap、layered architecture、configuration、parsers、operations。
- **状态**：Implemented

## 2026-07-02：L2 质量诊断与接口实体语义收紧

- **需求**：继续优化 L2 效果，避免图谱实体看起来存在但语义不准确，且要能定位候选命中后没有物化的问题。
- **决策**：
  - interface 表中 `Name / Protocol / Width / Details` 这类常见芯片表型，`Name` 作为接口实例实体名，`Protocol` 作为协议属性，不再把 `AMBA 5 AXI` 这类协议名误当接口实例。
  - `docgraph l2 audit` 增加 schema 级 `materialization_rate`。
  - `docgraph l2 audit` 对“schema 已命中候选但没有物化 L2 节点”的情况输出 `l2.matched_but_no_nodes` 警告，并带样例 candidate id。
  - CLI 的 `--json` 输出使用标准 stdout，保证机器可解析，便于 CI 和批处理门禁。
- **影响范围**：TableEntityExtractor、schema registry、l2-audit、CLI、operations、L2 validation 文档。
- **状态**：Implemented

## 2026-07-02：Protocol/Spec 文档不得关闭寄存器抽取

- **需求**：协议规范、subsystem spec 和 interface spec 也常包含寄存器、位域、pin/package 等实现约束，不能因为文档类型被判成 `protocol` 就跳过 register/pin。
- **决策**：
  - `DocType.PROTOCOL` 默认 schema 路由加入 `register` 与 `pin`。
  - 保留 signal/interface/timing/memory_map/interrupt/constraint 的协议类 schema。
  - 增加 protocol spec 下 register table 能被 L2 audit 命中的回归测试。
- **影响范围**：schema registry、l2-audit、extractors 文档、layered architecture。
- **状态**：Implemented
