# 01 — 5 分钟跑通 ARM Cortex-M4 TRM

目标：把一份 ARM Cortex-M4 技术参考手册（PDF）建成图谱并查询。

## 准备

```bash
mkdir my-arm && cd my-arm
mkdir spec
# 把 arm_cortexm4_processor_trm.pdf 放到 spec/ 下
```

## 构建

```bash
docgraph init --family arm-cortex
docgraph build
```

输出示例：

```
Build start — 1 files, LLM=no VLM=no budget=5.00
ok      spec/arm_cortexm4_trm.pdf  (171 nodes / 128 edges)
xref: 0 edges, 7 unresolved
entity-resolve: 0 alias edges
federation: 0 SUPERSEDES
171 nodes embedded with hash-256
Build done in 0.26s
```

## 查询

```bash
# 看看抽出来了什么
docgraph status

# 搜章节
docgraph search "interrupt" --kind=section --limit=5

# 找具体寄存器（LLM 关闭时召回有限；启用 LLM 见 02-enable-llm.md）
docgraph inspect register SYSTICK_CTRL

# 一句话上下文（Agent 友好）
docgraph graph context "如何配置 NVIC 优先级"

# 启动 MCP server，挂到 Claude Code
docgraph serve --mcp
```

## 接下来

- 启用 LLM 提高召回 → [02-enable-llm.md](./02-enable-llm.md)
- 接 errata 做联邦 → [04-federation.md](./04-federation.md)
