# MCP 接入

DocGraph 通过本地 stdio 把已经构建的文档库提供给 Agent。MCP 属于核心功能，不需要安装 extra。

先在文档项目中构建索引：

```bash
uv run docgraph init
uv run docgraph build
```

然后让 MCP host 从同一目录启动服务：

```json
{
  "mcpServers": {
    "docgraph": {
      "command": "uv",
      "args": ["run", "docgraph", "serve", "--mcp"],
      "cwd": "/absolute/path/to/document-project"
    }
  }
}
```

`cwd` 必须位于包含 `.docgraph/` 的项目中。DocGraph 会从这里向上定位项目根；host 配置文件本身放在哪里，由对应产品决定。

也可以在终端直接启动，观察错误日志：

```bash
uv run docgraph serve --mcp
```

## Agent 的典型路径

一般问题调用 `docgraph_query(task=...)`。返回的 chunk 已经是完整 L1 文本，内容足够时可以直接作答。

只有这些情况需要继续调用 `docgraph_read`：

- 结论依赖表格 cells、图片、公式或版面位置；
- 需要核对多个 chunk 的共同来源；
- L2 实体提示 `needs_source_check=true`。

精确查询可以用 `docgraph_entities` 找实体，再用 `docgraph_neighbors` 看关系。需要限定文档时，先从 `docgraph_documents` 取得 `doc_id`；需要浏览目录时使用 `docgraph_outline`。

全部参数和返回字段见 [MCP 工具参考](../reference/mcp-tools.md)，完整性和取证原则见[检索架构](../architecture/retrieval.md)。

## 排查连接问题

先确认同一个目录下的索引可以读取：

```bash
uv run docgraph status
```

如果命令正常，再检查 host 中的 `command`、`args`、`cwd` 和 MCP 日志。stdio 的 stdout 是协议通道，运行日志只应写入 stderr；不要手工向进程发送 JSON 来代替 host 的 MCP 会话。

当前实现使用官方 MCP Python SDK v2 和 `2026-07-28` 协议，只提供本地 stdio transport，不保留旧协议或旧工具名。
