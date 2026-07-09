# 贡献指南

> 对应 DESIGN.md §18 + §19。开源治理、测试、CI。

## License

计划 **Apache 2.0** —— 专利保护 + 商用友好。第三方插件可选自己的 license。

## 仓库治理

- `main` 受保护，所有变更经 PR + CI
- `CONTRIBUTING.md` + `CODE_OF_CONDUCT.md`
- 提交规范：Conventional Commits
- 版本：SemVer，1.0 前 minor 可破坏

## RFC 流程

大变更走 RFC：

```
docs/rfcs/
├── 0000-template.md
├── 0001-federation-namespace.md
├── 0002-streaming-llm-extractor.md
└── ...
```

RFC 包括：动机、设计、备选方案、迁移路径、未决问题。

## 测试

| 类型 | 工具 | 要求 |
|---|---|---|
| 单元测试 | pytest | core 模块覆盖 ≥80% |
| 集成测试 | pytest | 真实开源 PDF（如 RISC-V spec） |
| Golden 评估 | docgraph l2 eval | 标注集 precision/recall |
| Property test | hypothesis | schema 健壮性 |

## CI

```yaml
# .github/workflows/ci.yml
- pytest tests/unit -v --cov=docgraph
- pytest tests/integration
- docgraph l2 eval --golden=examples/golden/ --min-recall=0.85
- ruff check
- mypy docgraph/
```

## pre-commit

```yaml
- repo: ruff
- repo: mypy
- repo: end-of-file-fixer
```

## 技术栈

| 维度 | 选型 | 备注 |
|---|---|---|
| 语言 | Python 3.11+ | LLM 生态 |
| CLI | Typer | 简洁、自动文档 |
| 数据校验 | Pydantic v2 | schema 是命根子 |
| 图存储 | SQLite + 可插拔向量后端 | 默认本地轻量，支持 LanceDB |
| PDF 解析 | PyMuPDF / Docling / MinerU | 默认自动路由：PyMuPDF 预检/兜底，Docling 处理 born-digital，MinerU 处理 OCR/图片密集 |
| VLM | OpenAI-compatible / 可换 | 适配器隔离 |
| Embedding | hash / OpenAI-compatible / 可换 | 低成本默认，可配置真实语义模型 |
| MCP | FastMCP / mcp-python | 标准协议 |
| 文件监控 | watchdog | 跨平台 |
| 任务编排 | 自写轻量 DAG | 避免重依赖 |
| 配置 | YAML（ruamel.yaml） | 保注释 |
| 日志 | structlog | JSON 输出 |
| 测试 | pytest + hypothesis | property test |
| 打包 | hatch / uv | 现代化 |

## 文档站

- `mkdocs` + `mkdocs-material`
- 发布到 GitHub Pages
- 每个 plugin 接口附 cookbook 示例

## 路线图公开

- GitHub Projects 维护 roadmap
- DESIGN.md + docs/ 作为长期权威
- 公开 RFC 流程

## 如何开始贡献

1. Fork 仓库
2. 阅读 [DESIGN.md](../DESIGN.md) 和 docs/
3. 找一个 `good first issue`
4. 提 PR，跑通 CI
5. 至少一位维护者 review
