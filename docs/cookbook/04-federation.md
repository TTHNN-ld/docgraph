# 04 — Datasheet + Errata 联邦

场景：一颗芯片有 datasheet（基准）+ reference manual（详细）+ errata（勘误，最高优先级）。

## 配置

```yaml
# docgraph.yaml
project:
  name: stm32f407-spec
  family: stm32f407       # 同 family 才会自动合并

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
      priority: 100         # ← 最高
      supersedes:
        - "docs/datasheet.pdf"
        - "docs/reference-manual.pdf"
```

## 构建

```bash
docgraph build
```

Pipeline 会自动跑：
1. parse + extract（每份 spec 独立）
2. linker.federation：跨 doc 同名节点 → `SUPERSEDES` 边或 `ALIAS_OF` 边
3. linker.entity_resolver：同 family 归一

## 查询语义

```bash
# 默认返回最高优先级版本（errata）
docgraph inspect register TIM1_CR1

# 也可以看所有版本（M4 加 --include-superseded flag）
```

## 跨项目挂接

如果有多个 family（如 STM32F407 + STM32H7）想统一查询：

```bash
cd my-mcu-projects/stm32h7/
docgraph admin federate add ../stm32f407
docgraph admin federate ls
```

挂接后 MCP / CLI 查询会跨 family 检索（只读视图）。

## 注意

- 联邦合并只在**同 family** 内自动发生
- 跨 family 用 `docgraph admin federate add` 显式挂接
- 每条 `SUPERSEDES / ALIAS_OF` 边都带 `evidence`，agent 可以反查
