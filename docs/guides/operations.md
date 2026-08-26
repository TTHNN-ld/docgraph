# 运维与质量

## 构建完成条件

- 每个文件是独立错误边界；成功文档原子替换 L0/L1/L2。
- 失败写入 manifest 并继续其他文件，但整体命令最终返回非零。
- L2 extractor 失败只降级 L2，不阻断 L0/L1。
- 完整构建清理已删除文档；局部 `--doc` 不做全局删除对账。

当前 linker 和 embedding 失败只记录 warning，尚未统一计入 `BuildReport.errors`。自动化除检查退出码外，还应运行 strict doctor 并检查这些 warning；缺口见 [Roadmap](../project/roadmap.md)。

## 日常检查

```bash
docgraph setup
docgraph build
docgraph doctor --strict
docgraph l2 audit --strict
docgraph status
```

- `setup` 检查 parser、模型和 embedding。
- `doctor` 检查 L0/L1 完整性、FTS、来源链和 L2 结构。
- `l2 audit` 不调用模型，用于定位候选、schema 和物化问题。
- `status` 汇总文档、节点、边和向量。

CI 使用 `--json` 输出做门禁，避免解析终端样式。

## L2 Golden 评估

```bash
docgraph l2 eval \
  --golden tests-or-data/l2_expected.json \
  --kind register \
  --min-precision 0.90 \
  --min-recall 0.85
```

没有与生产文档类型匹配的版本化 golden set 时，不能把通过 doctor 等同于 L2 召回已达标。

## 日志、缓存和成本

- manifest 保存 parser 尝试、fallback、状态、错误和阶段统计。
- `.docgraph/cache/llm/`、`vlm/` 保存模型响应；向量和 parser 缓存均可重建。
- LLM/VLM 调用应有 timeout、缓存和成本预算。
- 切换模型、prompt 或 embedding provider 后，必须验证缓存键和向量刷新行为。

## 人工审核

```bash
docgraph admin review --min-confidence 0.5
```

当前 review 只处理低置信 edge。accept/reject 记录在 `.docgraph/entities/reviewed.jsonl`，但尚未形成重建时自动重放的完整闭环；重要决定应固化为规则、配置或测试。

## Migration 与恢复

```bash
docgraph admin migrate --dry-run
docgraph admin migrate
```

升级前会备份 `graph.db`。migration 失败必须恢复备份并保留旧版本号。重大升级、模型切换或批量重建前建议额外备份整个 `.docgraph/`。

## 安全边界

- 核心默认不启用远程模型，不主动外发文档。
- 启用远程 LLM、VLM、embedding 或 MinerU 后，文本或图片会发送到配置服务。
- 密钥不进入仓库、项目配置或 `.docgraph/`。
- MCP 使用本地 stdio；Web UI 无内置认证。
- Web 绑定非 loopback 地址前必须在外层提供认证、授权和 TLS。
- 只处理有权使用的规格书。

## 提交前验证

```bash
uv run pytest tests/ -q
uv run ruff check docgraph/
git diff --check
```

修改 Parser、Chunk、Extractor、Store 或构建链路时，再用代表性文档运行 `docgraph build` 和 `docgraph doctor --strict`。
