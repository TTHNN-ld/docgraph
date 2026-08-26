# RFC: 语义知识图谱 —— IP-XACT 对齐本体 + 三层混合抽取

- **ID**: 0015
- **状态**: Accepted
- **作者**: @ld
- **起草日期**: 2026-07-07
- **关联**: ADR-011（三层架构）、ADR-012（schema-guided 抽取，本 RFC 升级其语义关系层）、ADR-008（evidence 非空）

## 摘要

当前 L2 图谱 81% 的边是溯源/结构（`illustrated_by` / `contains` / `has_bitfield`），语义关系边仅 17% 且全来自 figure VLM。表格抽出的实体（register / interrupt / memory_map / signal）互相之间没有语义边，图谱实质是"实体 + 出处"而非知识图谱。本 RFC 提出三层混合抽取：A 层确定性事实抽取（保留）、B 层确定性关系推断（新增，章节归属 + 地址 join + 名字 join）、C 层 LLM 开放 IE（新增，GraphRAG 式、本体约束），本体对齐 IP-XACT（IEEE 1685-2022）。同时将溯源移出图边（保留在节点 attrs），图谱边只留语义关系。

## 动机

- **现状**（从 DDI0275 + PCIE 三文档实测）：边分布 `illustrated_by` 226 / `contains` 197 / `has_bitfield` 29 / `connects_to` 52 / `controls` 27 / `depends_on` 16 / `alias_of` 11 / `references` 1。溯源+结构占 81%，`register→module`、`interrupt→register`、`memory_map→register`、`signal→pin` 完全缺失。`TableEntityExtractor` 只抽实体不建横向关系；`xref` linker 几乎失效（1 条）。
- **溯源冗余**：节点 attrs 已有 `source_block_ids` / `source_chunk_ids`，`illustrated_by` / `contains` 作为图边是冗余，还稀释了语义。
- **规则化不可扩展**：新文档类型/厂商要写新 normalizer；text_entity 纯正则。
- **不做的后果**：图谱无法回答"DMA 模块的所有寄存器""哪个中断映射到 Status Register""AXI master 连到哪个 pin"等芯片语义问题，agent 只能退回全文检索，L2 失去意义。

## 详细设计

### 1. 本体（对齐 IP-XACT IEEE 1685-2022）

IP-XACT 是芯片 IP 交付的工业标准（ARM/Synopsys/Cadence 都用），实体/关系类型直接对齐它，保证领域正当性。

**实体类型**（沿用现有 `NodeKind`，补齐 IP-XACT 语义）：register、bitfield、pin、signal、interface、interrupt、memory_map、address_space、clock、reset_domain、module、parameter、requirement、errata、term、figure、section。

**关系类型**（有限、类型化，新增 `EdgeKind`）：

| EdgeKind | 语义 | 典型来源 |
|---|---|---|
| `belongs_to` | entity → module | B 层章节归属 / C 层 LLM |
| `contained_in` | memory_map → register | B 层地址 join |
| `mapped_to` | interrupt → register | B 层名字 join / C 层 LLM |
| `drives` | signal → pin \| interface | C 层 LLM / VLM |
| `clocks` | clock → module \| signal | C 层 LLM / VLM |
| `resets` | reset_domain → module \| register | C 层 LLM |
| `implements` | module → interface | C 层 LLM |
| `connects_to` / `controls` / `depends_on` / `references` | 沿用 | VLM / C 层 |
| `has_bitfield` | register → bitfield | A 层（结构拆解，保留） |
| `alias_of` / `supersedes` / `derived_from` | 沿用 | linker |

类型化关系优于 GraphRAG 的自由关系：芯片关系语义可枚举，约束后 LLM 精度更高、图谱更干净。

### 2. 三层抽取

| 层 | 输入 | 方法 | 产出 | 理由 |
|---|---|---|---|---|
| **A 确定性事实** | 表格 | normalizer（保留+精炼） | 实体 + 精确属性 + `has_bitfield` | bit/address/reset 不能交给 LLM |
| **B 确定性关系推断**（新） | A 的实体 + 章节结构 + 地址 | 规则推断 | `belongs_to` / `contained_in` / 名字合并 | 廉价、高精度，补一大半语义边 |
| **C LLM 开放 IE**（新） | B 未覆盖的文本 chunk + caption | LLM 抽三元组，约束在本体关系类型 | `mapped_to` / `drives` / `clocks` 等 | 补规则覆盖不到的语义 |
| **VLM 图**（保留+扩展） | figure | GLM-4.6V | module/signal/interface + `connects_to`；扩展 pinout→pin | 图是语义关系富矿 |

**关键**：B 层先跑，C 层只跑 B 未覆盖的 chunk —— 相比纯 GraphRAG（每块调 LLM）成本大幅下降，精度更高。

### 3. B 层规则（性价比最高）

零 LLM，靠结构推断：

- **章节归属**：实体出现在 `X.Y <Name> Module` 章节下 → `entity belongs_to <Name> Module`（confidence 0.85）。当前 extractor 完全没用章节上下文，这是最大漏洞。
- **地址 join**：memory_map.base + register.offset → `memory_map contained_in register`（confidence 0.95，精确）。
- **名字 join**：figure VLM 抽的 signal "mstr_aclk" + 表格 signal "mstr_aclk" → 合并节点，连接 figure 语义与 table 属性（confidence 0.9）。

### 4. C 层 LLM IE（GraphRAG 式，约束）

- 输入：B 未覆盖的文本 chunk + 表/图 caption
- prompt 给**本体关系类型清单**做约束（非自由生成）
- 输出 `(src, relation, dst)` + confidence + evidence（必填，引用 source chunk）
- confidence < 0.6 只入库不进主图，等校验
- 受 `llm.enabled` 控制；`llm.enabled=false` 时跳过，B 层仍工作

### 5. 溯源移出图边

- 砍掉 `illustrated_by`、`contains` 作为 `EdgeKind`（标 deprecated，数据保留一版以便回滚，下个大版本删）
- 溯源只保留在节点 attrs（`source_block_ids` / `source_chunk_ids`，已有）
- 图谱边只留语义关系 + `has_bitfield`（结构拆解）
- 图视图 `/graph` 不再渲染溯源边

### 6. 跨文档融合（保留）

- name + chip_model 实例键 → `alias_of`（TRM↔datasheet 同一寄存器）
- 文档版本 → `supersedes`

## 备选方案

1. **纯 GraphRAG**（每块调 LLM、自由关系类型）：rejected —— 精确事实 LLM 幻觉风险高（实测 width=32 幻觉）；每 chunk 调 LLM 成本高；芯片关系可枚举，自由类型降精度。
2. **纯规则扩展**（为新关系写更多 normalizer）：rejected —— 不可扩展（用户已指出）；新厂商/新表型要写代码。
3. **llm-wiki 式 agent 维护 markdown wiki**：rejected —— 强在持续累积，弱在结构化精确事实；芯片 spec 需精确位/地址，范式不匹配。

## 迁移路径

- 新增 `EdgeKind`（`belongs_to` / `contained_in` / `mapped_to` / `drives` / `clocks` / `resets` / `implements`），向后兼容（新边类型，老边不删）
- `illustrated_by` / `contains`：标 deprecated，图视图不渲染，数据保留一版；下个大版本删除
- 新 extractor（`relation_infer` / `llm_ie`）默认启用，可通过 `extractors.enabled` 关闭
- 无 schema migration（`EdgeKind` 是枚举，sqlite 存字符串）

## 未决问题

- LLM IE 的 confidence 门槛与人工校验流程
- IP-XACT VLNV 跨厂商匹配是否纳入 v1（暂不）
- Leiden 社区检测作为补充是否纳入 v1（暂不，章节结构为主）

## 时间线

| 阶段 | 日期 | 备注 |
|---|---|---|
| 草案提交 | 2026-07-07 | |
| Accepted | 2026-07-07 | 用户评审通过 |
| B 层实现 | 2026-07-07 | RelationInferExtractor + 砍溯源边 |
| C 层实现 | 待定 | LLMIEExtractor |
| VLM 扩展 | 待定 | pinout / timing |
