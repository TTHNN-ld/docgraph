# 运维、可观测性与安全

> 对应 DESIGN.md §16 + §17。

## 1. 错误分级

| 级别 | 行为 |
|---|---|
| `fatal` | 中止 build，落 log |
| `error` | 跳过当前文件/节点，记录到 manifest，build 继续 |
| `warn` | 仅记录，不影响结果 |
| `info` | 进度信息 |

## 2. 日志

- 结构化 JSON 行（`.docgraph/logs/docgraph.log`）
- 每条带 `run_id` / `doc_id` / `stage` / `extractor`
- CLI 默认彩色 pretty print
- 用 `structlog` 实现

```jsonl
{"ts":"2026-06-25T09:12:30Z","level":"info","run_id":"r1","stage":"parse","doc_id":"stm32::datasheet","msg":"page 12 done","duration_ms":420}
{"ts":"2026-06-25T09:12:35Z","level":"warn","run_id":"r1","stage":"extract","extractor":"register","msg":"low confidence register","node":"reg:UNKNOWN_X","confidence":0.3}
```

## 3. 成本追踪

- 每次 LLM/VLM 调用记 `tokens_in / tokens_out / model / cost_usd`
- `docgraph status --cost` 看本项目总开销
- `cost.budget_per_build_usd` 超限暂停
- `cost.vlm_max_calls_per_doc` 防止单文档刷爆

## 4. 质量评估

`docgraph doctor` 是分层硬门禁，检查 L0/L1 完整性、L2 provenance 和强结构实体约束；`docgraph l2-audit` 是 L2 抽取前诊断，不调用 LLM/VLM，用于确认候选覆盖与 schema 路由是否合理。

```bash
docgraph doctor --strict
docgraph l2-audit
docgraph l2-audit --schema register --schema signal
docgraph l2-audit --json
docgraph l2-eval --golden examples/golden --kind register --min-recall 0.9
```

`l2-audit` 输出：

- L1 chunk 中有多少 table/text/figure candidate。
- 每个 schema 看到了多少候选、命中了多少候选。
- 当前库中各 schema 已物化的 L2 节点数量。
- 哪些文档有 table candidate 但没有 schema 命中。

它回答的是“L2 有没有机会抽到、为什么可能漏抽”；不替代最终 precision/recall 评估。

`doctor` 会对强结构 L2 实体做确定性校验。例如 register/bitfield 必须满足：bit range 合法、bitfield 不越过 register width、同一 register 下 bitfield 不重叠、bitfield 必须指回存在的 register。模型输出不能绕过这些硬约束。

`l2-eval` 对比人工标注的 expected JSON 与当前库中已物化的 L2 节点，输出 precision / recall / F1。默认是报告模式；传入 `--min-precision` 或 `--min-recall` 后可作为 CI 门禁。

```
examples/golden/                    # 人工标注的"小份 spec + 期望抽取"
├── stm32f407-tim1/
│   ├── input.pdf
│   ├── expected_registers.json
│   └── expected_pins.json
└── arm-cortex-m4-systick/
    └── ...
```

- `expected_registers.json` 可以是 `["CTRL", "STATUS"]`，也可以是 `[{"name": "CTRL", "doc_id": "..."}]`
- `l2_expected.json` 可以按 kind 分组：`{"registers": ["CTRL"], "signals": ["clk"]}`
- `docgraph l2-eval` 跑 golden 集，输出 precision/recall 报告
- CI 跑 golden 评估，回归检测
- 阈值不达标 → CI 失败

## 5. 抽取置信度审核

- 每条节点/边带 `confidence`
- `docgraph review` 命令交互式列出低置信项
- 审核结果回写 `entities/*.reviewed.jsonl`，下次构建保留

```bash
docgraph review --min-confidence=0.5
# 进入 TUI：accept / reject / edit / skip
```

## 6. 安全与隐私

### 6.1 数据不主动外发

- 默认所有 LLM/VLM 调用走用户配置的 API key
- 需要离线或低成本构建时，在配置中设置 `llm.enabled=false` 与本地/哈希 embedding；L0/L1 不依赖外部服务

### 6.2 API key 来源

- 环境变量优先
- 其次 `~/.docgraph/credentials`
- **绝不写入项目 config**

### 6.3 日志脱敏

- 默认不打印文档全文
- 只打 hash + 位置（page、bbox）
- 显式 `--verbose-snippets` 才打 raw 内容

### 6.4 传输安全

- MCP server 默认仅 `127.0.0.1`
- 开放外部端口需显式 `--bind 0.0.0.0` + 二次确认

### 6.5 Spec 版权

- README / FAQ 提示用户**只对自有授权的 spec 使用**
- 项目本身不分发任何 spec 文档

## 7. 性能监控

`docgraph status` 输出示例：

```
Project:   stm32f407-spec
Family:    stm32f407
Documents: 4
Nodes:     12,840   (registers: 1,827 / pins: 312 / figures: 421 / ...)
Edges:     45,201
Chunks:    18,920   (with vectors: 18,920)
Storage:   .docgraph/graph.db (218 MB) + vectors.db (84 MB)

Last build: 2026-06-25 09:30  (12m 04s)
Total cost: $2.34 USD (LLM: $1.81, VLM: $0.53)

Per-doc:
  datasheet.pdf            ✓ linked      482 pages   3m12s
  reference-manual.pdf     ✓ linked     1240 pages   7m52s
  errata-rev3.pdf          ✓ linked       18 pages   1m00s
```
