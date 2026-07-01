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

## 3. 14 个可用工具

| 工具 | 用途 |
|---|---|
| `docgraph_status` | 图谱总览 |
| `docgraph_files` | 列文档 |
| `docgraph_search` | 名字 / 别名 / 语义检索 |
| `docgraph_node` | 节点详情 |
| `docgraph_neighbors` | 图遍历 |
| `docgraph_context` | **主入口**：按 task 拉相关包 |
| `docgraph_trace` | from→to 路径 |
| `docgraph_impact` | 影响范围 |
| `docgraph_register` | 寄存器（含 bitfields） |
| `docgraph_pin` | 管脚 |
| `docgraph_timing` | 时序参数 |
| `docgraph_figure` | 图（含 mermaid/wavejson） |
| `docgraph_section` | 章节正文 |
| `docgraph_glossary` | 术语 |

## 4. 使用示例

在 Claude Code 里：

> Q: PWM_CTRL 寄存器的 bit 3 控制什么？

Agent 会自动调 `docgraph_register("PWM_CTRL")`，拿到完整 bitfields，给出准确回答。

> Q: 实现一个 100kHz 的 PWM 输出。

Agent 调 `docgraph_context("实现 PWM 100kHz")`，DocGraph 返回相关 register / pin / section / figure 的结构化集合，agent 据此写代码。

## 5. 调试

```bash
# 手动模拟 MCP 请求
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | docgraph serve --mcp
```

## 6. 远程使用

M3 暂不支持远程 MCP（stdio only）。M4 计划加 HTTP transport。
