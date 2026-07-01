# 07 — Web UI 可视化

DocGraph 自带一个轻量 Web UI，把抽取出的图谱直观展示给人看。

## 启动

```bash
pip install 'docgraph[web]'
cd my-chip-project/
docgraph serve --web --port 8000
# 浏览器打开 http://127.0.0.1:8000
```

无需额外构建 —— FastAPI + Jinja2 + HTMX + d3.js（前两者随包安装，HTMX / d3.js 走 CDN）。

## 页面

| 页面 | 用途 |
|---|---|
| `/` | 概览：节点 / 边 / 文档统计，kind 分布柱图 |
| `/registers` | 寄存器列表 + 过滤 |
| `/registers/<id>` | **寄存器详情 + 位图可视化** |
| `/pins` | 管脚列表 |
| `/timing` | 时序/电气参数（含 min/typ/max + unit） |
| `/sections` | 章节树（点击展开） |
| `/figures` | 图列表（含 mermaid / wavejson 内嵌） |
| `/glossary` | 术语 / 缩写 |
| `/search` | 全文 + 别名 + 语义混合检索 |
| `/graph` | **d3.js 力导向图谱可视化**（拖动 / 缩放 / 点击跳转） |
| `/plugins` | 已注册的内置与第三方插件 |

## 寄存器位图

进入 `/registers/<reg_id>` 看到的视图：

```
+-----+-----+-----+-----+-----+-----+-----+-----+
| 31  | 30  | 29  | 28  | 27  | 26  | 25  | 24  |
| Res | Res |Mast |Type | Mode|     | Hpr | …   |
+-----+-----+-----+-----+-----+-----+-----+-----+
| 23  | 22  | 21  | 20  | 19  | 18  | 17  | 16  |
| ... |     |     |     |     |     |     |     |
…
```

位图按 bit_high / bit_low 自动填色，hover 显示完整字段信息；下方再列出完整 bitfield 表 + 原始 JSON。

## 关系图

`/graph` 是 d3.js 力导向图：

- 节点颜色按 kind：register / bitfield / section / pin / parameter / figure / term
- 边按类型：has_bitfield / contains / references / supersedes …
- 可拖、可缩放、可点击节点跳转
- 顶部 form 控制：`kinds`、`edge_kinds`、`limit`

## JSON API

每个页面背后的数据接口都暴露在 `/api/*`，方便集成第三方工具：

| 路径 | 输出 |
|---|---|
| `GET /api/status` | 节点 / 边 / kind 统计 |
| `GET /api/search?q=...&kind=...&limit=...` | 名字 / 别名 / fuzzy / 语义混合检索 |
| `GET /api/node/<id>` | 单节点 JSON |
| `GET /api/neighbors/<id>?depth=N` | 邻居子图 |
| `GET /api/graph?kinds=...&edge_kinds=...&limit=...` | 全图 force-graph 数据 |
| `GET /api/sections/tree` | 章节树 |

返回都是标准 JSON，跨域可启用：

```python
from fastapi.middleware.cors import CORSMiddleware
from docgraph.web.server import create_app

app = create_app()
app.add_middleware(CORSMiddleware, allow_origins=["*"])
```

## 远程访问

默认绑 `127.0.0.1`。对外暴露：

```bash
docgraph serve --web --host 0.0.0.0 --port 8000
```

⚠️ 当前**没有内置认证**，公开端口前请用 nginx / cloudflared 加一层 auth；或者只在内网用。

## 性能

- 启动一次性打开 SQLite + 加载 encoder
- 单次请求 < 50ms（10k 节点级别）
- 全图导出受 `limit` 控制，默认 500 节点

## 截图（描述）

- **概览页**：蓝色柱图显示 section: 143 / register: 17 / bitfield: 63 / pin: 28（ARM Cortex-M4 TRM 实测）
- **CSW 寄存器详情**：32 位位图，9 个着色字段（MasterType / Hprot1 / Mode / …）+ 完整描述表
- **关系图**：FOO_CTRL 节点为中心，辐射出 9 个 bitfield 子节点

## 相关

- 集成到 Agent → [05-mcp-integration.md](./05-mcp-integration.md)
- 数据导出 → [06-export-ipxact-rdl.md](./06-export-ipxact-rdl.md)
