"""Web UI for DocGraph —— FastAPI + Jinja2 + HTMX + d3.js。

启动：
    docgraph serve --web --port 8000
    # 然后浏览器打开 http://127.0.0.1:8000

页面：
- /              Dashboard（统计 + 文档列表）
- /registers     寄存器列表 + 位图可视化
- /registers/<id>  寄存器详情
- /pins          管脚
- /timing        时序参数
- /sections      章节树
- /figures       图列表
- /glossary      术语
- /search        全文 + 语义搜索
- /graph         d3 force graph 图谱可视化
- /node/<id>     单节点详情（含邻居）
- /api/...       JSON API（HTMX 与 d3.js 取数）
"""

from docgraph.web.server import create_app, run

__all__ = ["create_app", "run"]
