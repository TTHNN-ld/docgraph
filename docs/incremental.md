# 增量构建与缓存

> 对应 DESIGN.md §13。

## 1. 增量触发

- 手动：`docgraph build`（检测变更）/ `docgraph rebuild --doc=PATH`
- 自动：`docgraph watch` 用 watchdog 监听 `docs/`

## 2. 粒度

| 粒度 | 行为 |
|---|---|
| **文件粒度** | hash 未变 → 跳过 |
| **页粒度** | PDF 变化时，按页 hash 比对，只重 parse 变化页 |
| **节点粒度** | 抽取后按内容 hash 比对，只更新变化节点 |
| **边粒度** | 节点变化触发受影响边的 re-link |

## 3. 删除处理

- 文件删除 → 标 `pending_delete`，延迟 30 天 GC（避免误删）
- `docgraph prune` 立即清理
- 节点删除级联清理 `aliases` / `edges` / `chunks` / `vec_chunks`

## 4. 缓存层

| 缓存 | 位置 | 失效条件 |
|---|---|---|
| Parser 输出 | `.docgraph/cache/<doc_hash>/` | doc_hash 变 or parser_version 变 |
| LLM 抽取结果 | `.docgraph/cache/llm/<call_hash>.json` | 输入 hash 变 or prompt_version 变 |
| VLM 图描述 | `.docgraph/cache/vlm/<img_hash>.json` | 图哈希变 |
| Embedding | DB（`chunk_vec_map`） | 模型变 or chunk 变 |

## 5. Manifest

`.docgraph/manifest.json` 是增量的"账本"：

```json
{
  "docs/datasheet.pdf": {
    "hash": "sha256:...",
    "mtime": 1734567890,
    "doc_id": "stm32f407::datasheet@rev9",
    "parser": "mineru@0.7",
    "status": "linked",
    "stage_log": {
      "parse":   {"duration_s": 192, "ok": true},
      "extract": {"duration_s": 84,  "ok": true, "nodes": 1827},
      "link":    {"duration_s": 12,  "ok": true, "edges": 3091}
    },
    "last_run": "2026-06-25T09:12:30Z"
  }
}
```

每次 stage 完成都更新对应字段，崩溃后可从最后成功的 stage 继续。

## 6. watch 模式

```bash
docgraph watch --paths=docs/
```

- watchdog 监听 `docs/`
- debounce 1s（避免编辑器多次写入触发风暴）
- 增量队列 → 按 doc 串行处理（避免 LLM 并发风暴）
- 大文件用低优先级队列

## 7. 性能预算

| 场景 | 目标 |
|---|---|
| 首次 build 1000 页 PDF | < 30 min（含 LLM） |
| 修改单文件重 build | < 60s（命中缓存的话） |
| 单页增量 | < 5s |
| MCP 查询 | < 100ms（图谱命中） / < 500ms（向量检索） |

## 相关文档

- 存储设计 → [data-model.md](./data-model.md)
- 配置项 → [configuration.md](./configuration.md)
