# Linker 层

> 对应 DESIGN.md §9。构建跨实体、跨文档的关系边。

> 这一层最难，也是图谱"活起来"的关键。

## 1. 子任务

1. **章节/实体的 `CONTAINS` 边** —— 已在 extractor 阶段建立
2. **交叉引用（xref）解析** —— "see Section 5.3.2"、"refer to Figure 4-1"、"Table 7-2"
3. **实体消歧（entity resolution）** —— 同名信号在不同模块；缩写与全称
4. **跨 spec 联邦** —— datasheet 的 register 和 reference manual 的同一 register 合并
5. **errata 覆盖** —— `SUPERSEDES` 边

## 2. 三段式策略

```
Stage 1: 精确匹配（规则）
   - 完全相同的 name / qualified_name
   - 已知别名表（glossary）

Stage 2: 启发式匹配（规则）
   - 大小写归一、下划线/连字符归一
   - 模块前缀剥离（PWM_CTRL ↔ PWM.CTRL）
   - 数字后缀匹配（GPIO0..31）

Stage 3: LLM 兜底（仅限低置信集合）
   - 输入：候选实体对 + 各自上下文
   - 输出：merge / alias / unrelated + 理由
   - 用 fast tier 模型控制成本
```

每条边都附 confidence，agent 查询时可 `confidence_threshold` 过滤。

## 3. xref 解析

- 正则候选：
  - `Section X.Y(.Z)?`
  - `Fig(ure)? X-Y`
  - `Table X-Y`
  - `Chapter X`
- 解析后绑定到对应 Section / Figure / Table 节点
- 找不到目标时记 `references.unresolved.jsonl`

## 4. 实体消歧

```python
class EntityResolver:
    def resolve(self, candidates: list[Node]) -> ResolveResult:
        # 1. 精确匹配
        # 2. 启发式
        # 3. LLM 兜底（可选）
        return ResolveResult(
            merge_groups=...,
            alias_pairs=...,
            unresolved=...,
        )
```

合并规则：
- 同 `family` + 同 `qualified_name` + 同 `address`（寄存器）→ 高置信合并
- 同名但不同模块 → 保留为不同节点 + 双向 `ALIAS_OF`
- LLM 决定合并的，置信度上限 0.85，强制 evidence 记录推理

## 5. 联邦合并

详见 [federation.md](./federation.md)。

核心：
- 节点 id 全局形式 `<family>::<kind>:<qualified_name>[#<doc_id>]`
- 同 family 默认尝试合并
- 冲突按 `doc.priority` 决定主节点

## 6. errata 覆盖

- errata 中的寄存器/段落覆盖原 spec
- 建 `SUPERSEDES` 边（src=errata 节点, dst=原节点）
- 查询时默认返回最高优先级版本
- Agent 可显式 `include_superseded=true` 获取旧版

## 7. 输出

- 所有新建边写入 SQLite `edges` 表
- `linker.unresolved.jsonl` 记录无法处理的引用，供 review
- `linker.merged.jsonl` 记录合并决策的审计日志

## 相关文档

- 联邦 → [federation.md](./federation.md)
- 上一阶段 → [extractors.md](./extractors.md)
