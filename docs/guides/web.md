# Web UI

Web UI 用于本地浏览和调试，不是带认证的生产服务。

```bash
uv sync --extra web
docgraph build
docgraph serve --web --port 8000
```

默认访问 `http://127.0.0.1:8000`；`docgraph serve` 不带参数时也启动 Web UI。

主要页面包括概览、寄存器、管脚、参数、章节、图、术语、搜索、chunk、node、graph 和插件。JSON API 的精确路由以 [`docgraph/web/routes.py`](../../docgraph/web/routes.py) 为准。

`/graph` 是 3D 力导向图：默认只勾选模块/寄存器骨架；相机用 Trackball（左键旋转、滚轮缩放、右键或 Shift+左键平移）。默认不显示节点名称，悬停可看 `kind: name`。

## 安全与离线边界

- 服务没有内置认证。绑定 `0.0.0.0` 前必须在外层增加认证、授权和 TLS。
- 基础 HTML/CSS 随包提供，部分 HTMX 和图可视化脚本来自 CDN；完全离线时部分交互不可用。
- 面向 Agent 的接口使用本地 [MCP stdio](./mcp.md)。
