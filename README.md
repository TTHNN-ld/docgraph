# DocGraph

> 面向芯片规格书的本地文档知识底座。

DocGraph 将 PDF、DOCX、XLSX/XLSM 和 Markdown 归一为可追溯的版面块（L0）和检索块（L1），并可选抽取寄存器、位域、管脚、接口、中断等实体与关系（L2）。CLI、Web UI 和 MCP 都读取同一个本地图谱。

## 能做什么

| 能力 | 默认行为 | 说明 |
|---|---|---|
| 文档导入 | 扫描 `docs/`、`spec/` | 支持 PDF、DOCX、XLSX/XLSM、MD/Markdown |
| PDF 解析 | `auto` 路由，PyMuPDF 兜底 | Docling、MinerU、Marker 是按需安装的质量增强后端 |
| 本地检索 | FTS5 + 本地 hash embedding | 可配置真实 embedding provider |
| 实体图谱 | `section`、`table_entity` 默认启用 | L2 是增强层，失败不阻断 L0/L1 |
| Agent 接入 | MCP stdio | 6 个只读工具，提供 L1 查询、L0 取证和 L2 图谱浏览 |
| 人工浏览 | 可选 Web UI | 需要安装 `web` extra |

格式支持不等于版面能力完全相同。PDF 后端可以保留页码、坐标、图片和表格证据；轻量 DOCX parser 主要读取段落、标题和表格；XLSX/XLSM 主要读取 sheet 与单元格；Markdown 保留其语义块。详细边界见[文档导入](./docs/architecture/ingestion.md)。

## 安装

需要 uv 0.11.3 或更高版本；安装方法见 [uv 官方文档](https://docs.astral.sh/uv/getting-started/installation/)。

```bash
git clone https://github.com/TTHNN-ld/docgraph.git
cd docgraph
```

根据需要选择一条同步命令；每条都会包含核心依赖，不必先单独执行 `uv sync`：

```bash
uv sync                                      # 仅核心功能
uv sync --extra web                          # 核心 + Web UI
uv sync --extra docling                      # 核心 + Docling
uv sync --extra mineru                       # 核心 + MinerU
uv sync --group dev                          # 核心 + 测试/检查工具 + Web 测试依赖
uv sync --extra web --extra docling          # 核心 + Web UI + Docling
```

`uv sync` 会让环境与当前命令精确一致。后续同步时，需要继续保留的可选能力应再次写在同一条命令中。MinerU 和 Marker 的 Pillow 版本约束不兼容，不能同时启用；重型后端首次使用时可能下载模型。

PDF 默认 `auto` 路由会在可用的 Docling、MinerU 和核心 PyMuPDF 之间选择。只想使用 MinerU 时不需要安装另外两个可选后端，但建议在 `docgraph.yaml` 中设置 `primary: mineru`；示例见[配置指南](./docs/guides/configuration.md#mineru-远程推理)。Marker 不参与自动路由，使用时需要显式设置 `primary: marker`。

## 快速开始

在项目根创建 `docs/` 或 `spec/`，放入文档后执行：

```bash
uv run docgraph init
uv run docgraph setup                 # 可选：检查 parser、模型和 embedding 状态
uv run docgraph build
uv run docgraph doctor --strict
uv run docgraph status
```

默认不需要 `docgraph.yaml`。只有要修改文档范围、family、parser 或 extractor 策略时才创建项目配置。

常用查询：

```bash
uv run docgraph search "per_vector_misc"
uv run docgraph search "clock" --kind section
uv run docgraph inspect register freeze_reg
uv run docgraph graph context "如何配置中断"
```

只重建一个文件或监听变化：

```bash
uv run docgraph build --doc docs/reference-manual.pdf
uv run docgraph admin watch
```

启动可选接口：

```bash
uv run docgraph serve --mcp
uv run docgraph serve --web --port 8000
```

完整命令以 `uv run docgraph --help` 和各子命令的 `--help` 为准。

## 数据分层

```text
输入文档
  └─ L0 Block：段落、标题、表格、图片、公式及版面证据
       └─ L1 Chunk：稳定 ID、block_ids、全文/向量检索
            └─ L2 Node/Edge：带来源和可信状态的可选实体增强
```

- L0 是原文证据层。
- L1 是 Agent 的主要阅读和检索层，可通过 `block_ids` 回到 L0。
- L2 用于加速精确实体查询，不能替代原文取证。

分层硬约束见[分层数据契约](./docs/architecture/data-layers.md)，整体数据流见[架构总览](./docs/architecture/overview.md)。

## 当前边界

- DocGraph 面向芯片 spec，不是通用文档管理、协作编辑或权限系统。
- L0/L1 可以在不配置模型的情况下构建；LLM/VLM 只增强部分 L2 抽取与视觉语义。
- 结构化表格的确定性抽取最适合自动化使用；LLM/VLM 结果应通过来源块复核。
- Web UI 默认没有认证，不应直接暴露到公网。
- IP-XACT/SystemRDL 导出目前聚焦 register/field 子集，交给下游工具前应校验地址、宽度和 reset。

当前工作重点和已知缺口见 [Roadmap](./docs/project/roadmap.md)。

## 文档

- [文档导航](./docs/README.md)：按读者任务和文档职责分类
- [MCP 接入](./docs/guides/mcp.md)：连接 Agent host
- [MCP 工具参考](./docs/reference/mcp-tools.md)：接口、参数、返回值和错误
- [DESIGN.md](./DESIGN.md)：设计文档入口与权威关系
- [配置指南](./docs/guides/configuration.md)
- [运维指南](./docs/guides/operations.md)
- [贡献指南](./CONTRIBUTING.md)

License: Apache 2.0
