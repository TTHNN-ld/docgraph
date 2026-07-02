# 联邦机制

> 对应 DESIGN.md §12。让 datasheet + reference manual + errata + app note 在同一图谱中共存与覆盖。

> 一颗芯片往往有多份 spec。联邦是 DocGraph 不能后补的设计点。

## 1. 数据模型扩展

```python
class Document(BaseModel):
    doc_id: str                    # e.g. "stm32f407::datasheet@rev9"
    title: str
    family: str                    # e.g. "stm32f407"
    type: DocType                  # datasheet | reference_manual | errata | app_note | trm
    version: str
    date: str | None
    priority: int                  # 数字越大越权威，errata 最高
    supersedes: list[str] = []     # 显式声明覆盖了哪些 doc_id
```

## 2. 命名空间

节点 id 全局形式：

```
<family>::<kind>:<qualified_name>[#<doc_id>]

例：
stm32f407::reg:TIM1.CR1                                # 合并后的主节点
stm32f407::reg:TIM1.CR1#reference_manual@rev9          # 原始版本
stm32f407::reg:TIM1.CR1#errata@rev3                    # errata 版本
```

合并规则：
- 同 family + 同 kind + 同 qualified_name + 同 address（如有）→ 合并候选
- 合并时按 `priority` 选主，其余保留为副本 + alias
- 冲突字段（reset value、access）由主节点决定，副本通过 `SUPERSEDES` 边追溯

## 3. SUPERSEDES 边

- errata 中的某寄存器/段落覆盖原 spec → 建 `SUPERSEDES` 边
- 查询时默认返回最高优先级版本
- Agent 可显式 `include_superseded=true` 获取旧版
- 每次 query 结果带 `provenance` 列表，记录涉及的 `doc_id` + `version`

## 4. 配置示例

```yaml
docs:
  metadata:
    "docs/datasheet.pdf":
      type: datasheet
      version: rev9
      priority: 10
    "docs/reference-manual.pdf":
      type: reference_manual
      version: rev9
      priority: 20
    "docs/errata.pdf":
      type: errata
      version: rev3
      priority: 100
      supersedes:
        - "docs/datasheet.pdf"
        - "docs/reference-manual.pdf"
```

## 5. 联邦 CLI

```
docgraph build                       # 当前 family 全部 spec
docgraph build --doc=PATH            # 只重建某一份
docgraph admin federate add ../another-chip/.docgraph
                                     # 把另一项目的图谱挂接进来（只读 mount）
docgraph admin federate ls
docgraph admin federate rm <family>
```

> 联邦挂接（多 family 共存）的设计预留，P2 实现。核心 schema 已支持。

## 6. 查询语义

```python
# 默认：返回最高优先级版本
docgraph_register("TIM1_CR1")

# 拿到所有版本
docgraph_register("TIM1_CR1", include_superseded=True)
# 返回：
# {
#   "primary": {...errata 版本...},
#   "superseded": [{...原 reference manual 版本...}]
# }
```

## 7. 设计取舍

| 选项 | 选择 | 理由 |
|---|---|---|
| 多 family 同图 vs 分库 | 分库挂接（federate add） | 隔离故障域，权限独立 |
| errata 合并 vs 并存 | 并存 + SUPERSEDES 边 | 可审计、可回退 |
| 自动嗅探 doc 类型 vs 用户声明 | **用户声明优先**，自动嗅探兜底 | 准确性 > 便利性 |

## 相关文档

- 数据模型 → [data-model.md](./data-model.md)
- Linker 中的合并实现 → [linker.md](./linker.md)
