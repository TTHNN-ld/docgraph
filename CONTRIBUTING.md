# Contributing to DocGraph

DocGraph 计划走开源 Apache 2.0。欢迎任何形式的贡献。

## 设计文档是唯一权威（必读）

- **唯一权威设计**：[DESIGN.md](./DESIGN.md) + [docs/](./docs/)，其中数据架构以 [docs/layered-architecture.md](./docs/layered-architecture.md)（L0/L1/L2）为最高权威。
- **代码必须紧跟设计文档**。实现与文档冲突时：**改代码，不改文档**——除非先走 [RFC 流程](./docs/rfcs/) 修订文档。
- 重大架构变更：先改文档（RFC）→ 评审通过 → 再写代码。
- 每个 PR 描述需注明：遵循/修订了哪条设计条款。
- Review 时必须检查分层契约（见 layered-architecture.md §2）：
  - Parser 是否把表格**无损**入库（不允许丢成 `[]`）
  - L2 抽取失败是否影响了 L0/L1（不允许）
  - L2 节点是否带 `source_block_ids`（必须）

## 行为准则

本项目遵循 [Contributor Covenant](https://www.contributor-covenant.org/) 行为准则。简而言之：保持友善、专业、尊重他人。

## 怎么开始

```bash
git clone https://github.com/<org>/docgraph.git
cd docgraph
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -v
```

## 工作流程

1. **Issue 优先**：除非是 typo 或 1-2 行的小修，请先开 issue 讨论
2. **Fork + branch**：`feature/foo` 或 `fix/bar` 命名
3. **小步提交**：Conventional Commits（`feat: ...`、`fix: ...`、`docs: ...`）
4. **写测试**：新增/修改代码必须配套测试；CI 会跑 pytest + ruff + mypy
5. **更新文档**：API 变更同步更新 `docs/`
6. **提 PR**：模板会引导你填关键信息

## 代码风格

- Python 3.11+
- Pydantic v2 for all cross-module data
- `ruff` 配置见 `pyproject.toml`
- `mypy` 暂为 advisory（M4 转为 strict）
- 错误用 `docgraph.errors.*`（M4 引入）
- 日志走 `docgraph.core.logger.get_logger`

## 提交新 Parser / Extractor / Embedding

1. 实现接口（详见对应 `base.py`）
2. 在 `pyproject.toml` 加 entry_point：
   ```toml
   [project.entry-points."docgraph.extractors"]
   my_ext = "my_pkg.module:MyExtractor"
   ```
3. 放一份 minimal test + 一份 golden 样本（`tests/golden/`）
4. 更新 `docs/extractors.md` 或 `docs/plugins.md`

## 重大变更：走 RFC

涉及破坏性 API / schema / 跨模块设计的变更需先写 RFC：

```
docs/rfcs/
├── 0000-template.md
├── 0001-federation-namespace.md   # 已落地
└── XXXX-your-proposal.md
```

RFC 至少应包含：动机、设计、备选方案、迁移路径、未决问题。

## 测试要求

| 类型 | 工具 | 阈值 |
|---|---|---|
| 单元测试 | pytest | core 模块 ≥80% 覆盖（M4 开始强制） |
| 集成测试 | pytest + 真实 PDF | 接 RISC-V / 其它开源 spec |
| Golden 评估 | `docgraph eval` | precision/recall ≥85%（M4） |
| Property test | hypothesis | schema 健壮性 |
| Type check | mypy --strict | M4 起强制 |

CI 跑 ubuntu + macos × Python 3.11/3.12/3.13。

## License

提交即同意你的贡献以 Apache 2.0 许可。
