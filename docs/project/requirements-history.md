# 需求变更记录

> 本文件记录已确认的需求变化、决策和影响范围。正式设计文档只保留当前稳定事实；需求演进过程统一沉淀在这里。

## 记录格式

- **日期**：YYYY-MM-DD
- **需求**：用户或项目明确提出的新要求
- **决策**：采用的产品/架构方案
- **影响范围**：涉及的代码、配置、文档或使用方式
- **状态**：Accepted / Implemented / Superseded

## 2026-08-26：统一使用 uv 管理 Python 环境

- **需求**：项目依赖、开发环境和 CI 统一由 uv 管理，不再保留其他 Python 包安装路径。
- **决策**：
  - 提交 `uv.lock` 和 `.python-version`，锁定完整依赖图并以 Python 3.11 作为默认开发版本。
  - 核心环境、可选能力和开发工具分别通过 `uv sync`、`--extra` 和 `--group dev` 同步；`default-groups = []` 保持默认环境轻量。
  - CI 使用固定版本的 uv，先执行 locked sync，再通过普通 `uv run` 运行命令。
  - 运行时的白名单可选依赖安装在源码检出中调用 `uv sync --locked --inexact --extra`，保留已有环境；已安装工具提示用户用 `uv tool install --force` 重建环境。
  - MinerU 与 Marker 的 Pillow 约束互斥，作为 uv extra conflict 显式建模，不再提供不可解析的聚合 extra。
- **影响范围**：项目元数据、锁文件、CI、运行时依赖处理、CLI 提示、README、贡献指南和专题文档。
- **状态**：Implemented。

## 2026-08-26：文档信息架构与事实收口

- **需求**：README 和专题文档需要反映真实实现，删除阶段编号、不可复现数字、失效命令和重复介绍，并让不同文档各自承担清晰职责。
- **决策**：
  - README 只保留定位、安装、快速开始、核心边界和文档入口。
  - `docs/` 按 architecture、guides、development、reference、project、decisions、research 分类，不再把专题平铺在根目录。
  - 数据模型并入分层契约，增量并入导入链路，Linker 并入知识图谱；重复 Cookbook 只保留独有的 MCP、Web 和导出操作。
  - DESIGN 管理设计权威关系；`docs/README.md` 按读者任务导航。
  - Roadmap 只记录当前基线、已知缺口和下一步；稳定设计、历史决策和研究快照分开保存。
  - 精确 CLI、配置字段和数据库结构分别以 `--help`、Pydantic config model 和 migrations/store 源码为准，专题文档不复制易漂移实现。
  - 调研与评测明确标为时间点材料，不再把缺少数据集/版本信息的数字放进 README。
- **影响范围**：README、DESIGN、AGENTS、文档目录、架构、指南、插件、Roadmap、RFC 和研究评测材料。
- **状态**：Implemented。

## 2026-08-26：默认支持常用文档格式

- **需求**：默认安装不应只发现 PDF；DOCX、XLSX/XLSM 和 Markdown 也应开箱可用，无需额外配置文档范围或安装轻量 parser extra。
- **决策**：
  - 核心安装包含 PyMuPDF、python-docx、openpyxl 和 markdown-it-py。
  - 默认扫描 `docs/` 与 `spec/` 下的 PDF、DOCX、XLSX/XLSM、MD/Markdown。
  - document ID 纳入项目相对源路径，避免同名 `.xlsx`/`.xlsm`
    或不同目录的文档相互覆盖。
  - 首次使用新 ID 规则构建时，即使文件 hash 未变也会自动重建旧记录。
  - `docgraph admin watch` 与一次性构建复用同一份文档发现规则，并在删除文件时执行完整增量对账。
  - Docling、MinerU、Marker 等重型 PDF 后端继续按需安装；它们增强 PDF 解析质量，不决定格式是否受支持。
  - 各格式统一归一化为 `ParsedDoc + L0 Blocks`，后续继续使用相同的 L1/L2 流程。
- **影响范围**：核心依赖、配置默认值、document ID、Parser 选择、watch、setup 检查、测试和使用文档。
- **状态**：Implemented。

## 2026-08-21：MinerU 远程 VLM 推理服务

- **需求**：MinerU 的视觉模型应能像 vLLM/SGLang 服务一样独立部署，避免 DocGraph 构建机器承担 VLM 权重和 GPU 推理。
- **决策**：
  - MinerU adapter 升级到 3.x 客户端和结构化输出。
  - DocGraph 保留 PDF 编排、解析产物缓存和 `middle.json → ParsedDoc/L0` 归一化。
  - `vlm-http-client` / `hybrid-http-client` 通过 `model_server_url` 连接 OpenAI-compatible 模型服务。
  - `model_server_url` 明确表示模型推理地址，不与文档级 MinerU `api_url` 混用。
  - 模型名、API key 和服务地址支持用户级配置及独立环境变量；凭证不进入子进程命令行和缓存键。
  - 远程推理失败继续使用现有 parser fallback 和 manifest 审计链路，不写入不完整 L0/L1。
- **影响范围**：MinerU Parser、依赖管理、Parser 配置、缓存、测试、README、Parser/配置文档。
- **状态**：Implemented。

## 2026-07-14：小语料直接读取完整 L1

- **需求**：文档较少、L2 实体和关系还不充分时，Agent 应能直接取得完整 L1，避免只靠实体图谱漏掉信息。
- **决策**：
  - 新增统一入口 `docgraph_context`。
  - `auto` 模式按 L1 字符数和 chunk 数判断；预算内完整返回，超预算自动检索。
  - `full` 模式支持游标分页，不能绕过单次响应硬上限。
  - 完整读取返回 L1 chunk 和 `block_ids`，L0 继续按需 fetch；L2 只作为候选附带。
  - MCP 只提供受预算约束的文档视图，不替 Agent 摘要或判断结论；chunk 文本保持原样。
  - 完整模式和检索模式使用不同的完整性声明；检索结果必须公开检索方法、候选数、遗漏量和排序理由。
  - Agent 可以覆盖自动模式、改写查询、指定文档、继续分页或直接调用底层检索与 fetch 工具。
  - 默认预算为 40000 字符、80 chunks，后续通过真实 Agent 回归测试校准。
- **影响范围**：Query Engine、MCP、分层架构、检索设计、Agent 使用规范。
- **状态**：Implemented。

## 2026-07-02：配置与生成物分离

- **需求**：DocGraph 应像 codegraph 和 Claude Code 一样按用户安装使用。大模型、embedding、VLM 和密钥配置放到用户目录；项目执行 `docgraph init` 后只产生项目本地运行目录。
- **决策**：
  - 用户级配置放在 `~/.docgraph/config.yaml`。
  - 用户级环境变量放在 `~/.docgraph/.env.local` 与 `~/.docgraph/.env`。
  - 项目级 `docgraph.yaml` 变为可选覆盖文件，只在需要设置 family、文档范围、parser/extractor 策略时使用。
  - 项目 `.docgraph/` 是纯生成目录，保存 `graph.db`、缓存、manifest、向量和审计产物。
  - `docgraph init` 默认不生成项目配置文件；传入 `--name` 或 `--family` 时生成最小 `docgraph.yaml`。
- **影响范围**：CLI init、配置加载、dotenv 自动加载、README、configuration、architecture、layered architecture。
- **状态**：Implemented。

## 2026-07-02：模型密钥集成到用户配置

- **需求**：用户不希望额外维护 `~/.docgraph/.env`；模型、embedding、VLM 的 key 和 endpoint 应像 Claude Code 一样集中在用户级配置中。
- **决策**：
  - `~/.docgraph/config.yaml` 支持直接配置 `llm.providers.<name>.api_key` / `base_url`。
  - `embeddings` 支持直接配置 `api_key` / `base_url`。
  - `llm.vlm` 支持独立配置 `provider` / `model` / `api_key` / `base_url`，允许文本 LLM 与 VLM 使用不同服务。
  - `.env` 和环境变量保留为兼容、CI 和临时覆盖路径，但不再是推荐主路径。
- **影响范围**：配置模型、LLM provider、VLM provider、embedding provider、configuration、cookbook、operations。
- **状态**：Implemented。

## 2026-07-02：默认开箱体验收敛

- **需求**：普通用户不应维护繁琐配置文件；默认即可在项目内跑起来。
- **决策**：
  - 默认扫描 `docs/**/*.pdf` 与 `spec/**/*.pdf`。
  - PDF 默认 parser 为轻量 PyMuPDF，保证安装后可直接构建 L0/L1。
  - MinerU 作为复杂芯片 PDF 的高保真推荐后端，通过可选 `docgraph.yaml` 显式开启，并建议配置 PyMuPDF fallback。
  - 日常命令保持少量核心入口：`docgraph init`、`docgraph build`、`docgraph doctor`、`docgraph serve` 和检索/查询类命令。
- **影响范围**：配置默认值、Parser 文档、README、roadmap。
- **状态**：Superseded by 2026-08-26 默认多格式支持；“零配置运行”原则继续有效。

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
  - 当时的顶层设计入口保留 `DESIGN.md`、分层架构和主题文档。
  - Roadmap 只记录当前产品基线、近期工程重点和稳定 ADR。
  - 需求变化统一写入本文件，避免把临时讨论边界写进正式设计文档。
- **影响范围**：README、DESIGN、roadmap、layered architecture、configuration、parsers、operations。
- **状态**：Superseded by 2026-08-26 文档信息架构；需求变更记录原则继续有效。

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
