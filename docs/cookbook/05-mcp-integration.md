# 05 — 用 MCP 把 DocGraph 接入 Claude Code

DocGraph 实现了 MCP（Model Context Protocol）stdio server，可以挂到任何 MCP host（Claude Code、Claude Desktop、Cursor 等）。

## 1. 在芯片项目根启动

```bash
cd my-chip-project/
docgraph build      # 先建图
docgraph serve --mcp   # 启动 stdio MCP server
```

## 2. Claude Code 配置

在 Claude Code 设置里加 MCP server：

```json
{
  "mcpServers": {
    "docgraph": {
      "command": "docgraph",
      "args": ["serve", "--mcp"],
      "cwd": "/absolute/path/to/my-chip-project"
    }
  }
}
```

或在终端里：

```bash
claude mcp add docgraph -- docgraph serve --mcp
```

## 3. 当前可用工具

| 工具 | 用途 |
|---|---|
| `docgraph_context` | 获取自适应文档视图；小语料返回完整 L1，大语料返回透明检索结果 |
| `docgraph_status` | 图谱总览 |
| `docgraph_files` | 列文档 |
| `docgraph_search_chunks` | 检索 L1 文本 |
| `docgraph_fetch` | 读取完整 L1 chunk 和对应 L0 原文 |
| `docgraph_fetch_many` | 批量读取多个 chunk 的 L0/L1 证据，并对重复 blocks/entities 去重 |
| `docgraph_search` | 搜索 L2 实体候选 |
| `docgraph_neighbors` | 图遍历 |
| `docgraph_section` | 浏览章节结构 |

一般先调用 `docgraph_context`。返回的 `coverage` 会说明当前视图是完整 L1、分页 L1 还是检索候选；有 `next_cursor` 时可以继续读取。需要核对版面、表格或图片证据时，再用 `docgraph_fetch` 或 `docgraph_fetch_many` 回到 L0。

## 4. 使用示例

在 Claude Code 里：

> Q: PWM_CTRL 寄存器的 bit 3 控制什么？

Agent 先调用 `docgraph_search("PWM_CTRL")` 查找 L2 候选，再根据
`source_chunk_ids` 调用 `docgraph_fetch` 核对寄存器原表。

> Q: 实现一个 100kHz 的 PWM 输出。

Agent 调用 `docgraph_context(task="实现 PWM 100kHz")`。小语料会直接给出完整 L1；大语料会返回匹配的完整 chunk、检索方法、候选数量和排序理由。需要核对多个原始表格或图片时，优先调用 `docgraph_fetch_many`，只核对单个命中时调用 `docgraph_fetch`。

## 5. 调试

```bash
# 手动模拟 MCP 请求
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | docgraph serve --mcp
```

## 6. 连接方式

当前 MCP 使用 stdio，由本机 MCP host 启动和管理进程。HTTP transport 尚未提供。
