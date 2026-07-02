# 02 — 启用 LLM 抽取

DocGraph 的 `table_entity` / `figure` 等 extractor 支持 LLM/VLM 增强。启用后，表格实体（register / pin / timing / signal / interface / requirement 等 schema）和图像描述的召回率、准确率会显著提高。

## 选 Provider

| Provider | 用途 | 配置 |
|---|---|---|
| `anthropic` | Claude 系列（推荐 VLM） | `api_key` |
| `openai_compat` | OpenAI / 火山方舟 / DeepSeek / Together / Groq / vLLM / Ollama | `api_key` + `base_url` |

## 方式一：Anthropic Claude

```yaml
# ~/.docgraph/config.yaml
llm:
  enabled: true
  provider: anthropic
  providers:
    anthropic:
      api_key: sk-ant-...
  tiers:
    fast: claude-haiku-4-5-20251001
    balanced: claude-sonnet-4-6
    accurate: claude-opus-4-8
```

## 方式二：OpenAI 兼容（火山方舟 / DeepSeek / etc.）

```yaml
# ~/.docgraph/config.yaml
llm:
  enabled: true
  provider: openai_compat
  providers:
    openai_compat:
      api_key: sk-...
      base_url: https://api.deepseek.com/v1
  tiers:
    fast: deepseek-chat
    balanced: deepseek-chat
    accurate: deepseek-reasoner
```

火山方舟示例（注意用标准 chat completions 端点 `/api/v3`）：

```yaml
# ~/.docgraph/config.yaml
llm:
  enabled: true
  provider: openai_compat
  providers:
    openai_compat:
      api_key: ark-xxx-yyy
      base_url: https://ark.cn-beijing.volces.com/api/v3
  tiers:
    balanced: doubao-1-5-pro-32k
```

## 控制成本

```yaml
# ~/.docgraph/config.yaml
cost:
  budget_per_build_usd: 5.0    # 超预算自动暂停
  vlm_max_calls_per_doc: 500
```

每次 `docgraph build` 完会输出本次 LLM 总开销。所有调用都按 `(prompt_version, model, temperature, system, prompt)` 哈希缓存在 `.docgraph/cache/llm/` 下，重复跑零成本。

## 验证

```bash
docgraph build --force
docgraph inspect register SYSTICK_CTRL
# 现在你应该能看到完整的 bitfields
```

## 调试

```bash
# 在 .docgraph/entities/registers.failed.jsonl 看 LLM 抽取失败的样本
cat .docgraph/entities/registers.failed.jsonl
```
