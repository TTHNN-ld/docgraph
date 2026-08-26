#!/bin/bash
# Claude Code Agent 对比 Benchmark
# 用法: bash tests/e2e/benchmark.sh

set -e

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MCP_CONFIG="$ROOT/.docgraph/docgraph_mcp.json"
CHUNKS_FILE="$ROOT/.docgraph/export/chunks.md"
OUTDIR="$ROOT/.docgraph/benchmark_results"
mkdir -p "$OUTDIR"

DG_SYSTEM="你是一个芯片设计验证工程师的助手。你有 docgraph MCP 工具可以查询芯片设计文档。
一般问题先用 docgraph_query 获取可阅读的原文；需要核对表格、图片或来源时，用 docgraph_read 批量取证。精确查询寄存器、信号或模块时，可以用 docgraph_entities 定位实体，再用 docgraph_neighbors 查看关系，并沿 source IDs 回查原文。用 docgraph_outline 浏览章节，用 docgraph_documents 查看文档范围和索引状态。
只基于工具返回的结果回答，不要编造。给出具体的名称、地址、数值等可操作信息。用中文回答，技术术语保留英文原名。"

BASE_SYSTEM="你是一个芯片设计验证工程师的助手。文档全文导出在 .docgraph/export/chunks.md (2355个chunk，约1.5MB)。
可用工具: Bash(grep) 搜索关键词, Read 查看文档片段。
规则: 先用 grep 搜索关键词再 Read 相关区域。只基于文档内容回答，不要编造。给出具体名称、地址、数值等可操作信息。用中文回答，技术术语保留英文原名。"

# 测试用例
declare -a QUESTIONS=(
  "FTB 模块的功能是什么？它有哪些关键设计参数？请从文档中找出具体的技术细节。"
  "sbpctl 寄存器的地址是多少？它有哪些位域？分别控制什么？"
  "BPU 的 s1/s2/s3 三级流水各自负责什么工作？什么情况会导致流水线冲刷？"
  "BPU 到 FTQ 的接口有哪些握手信号？信号的时序关系是怎样的？"
  "BPU 分支预测错误时的恢复流程是什么？有哪些重定向类型？"
  "ICache 有哪些可配置参数？各自的默认值和约束是什么？"
  "TAGE 预测器在什么情况下准确率会下降？有哪些已知的性能瓶颈？"
  "信号 'pc' 在 BPU 中是怎么产生和传递的？经过哪些模块？"
  "RAS 预测器的持久化队列有什么优点？它能解决什么问题？"
  "一条指令在香山处理器中从取指到写回的完整流水线路径是怎样的？各阶段的关键模块和可能出现的异常有哪些？"
)

declare -a TITLES=(
  "模块定位" "寄存器配置" "时序理解" "接口协议" "异常处理"
  "配置参数" "性能限制" "信号追踪" "架构差异" "综合理解"
)

echo "============================================================"
echo "Claude Code Agent 对比 Benchmark"
echo "  共 ${#QUESTIONS[@]} 个测试用例"
echo "============================================================"

SUMMARY="$OUTDIR/summary.csv"
echo "case,title,dg_turns,dg_tools,dg_tokens_in,dg_tokens_out,dg_cost,dg_wall_ms,base_turns,base_tools,base_tokens_in,base_tokens_out,base_cost,base_wall_ms" > "$SUMMARY"

for i in "${!QUESTIONS[@]}"; do
  Q="${QUESTIONS[$i]}"
  TITLE="${TITLES[$i]}"
  NUM=$((i+1))
  echo ""
  echo "──── [$NUM/10] $TITLE ────"

  # ── DocGraph 模式 ──
  DG_FILE="$OUTDIR/case${NUM}_docgraph.json"
  echo -n "  [DocGraph] "
  /opt/homebrew/bin/claude -p --bare \
    --output-format json \
    --mcp-config "$MCP_CONFIG" \
    --dangerously-skip-permissions \
    --max-budget-usd 1.00 \
    --no-session-persistence \
    --system-prompt "$DG_SYSTEM" \
    "$Q" > "$DG_FILE" 2>/dev/null || true

  # 提取指标
  DG_TURNS=$(python3 -c "import json; d=json.load(open('$DG_FILE')); print(d.get('num_turns',0))" 2>/dev/null || echo "0")
  DG_COST=$(python3 -c "import json; d=json.load(open('$DG_FILE')); print(f\"{d.get('total_cost_usd',0):.4f}\")" 2>/dev/null || echo "0.0000")
  DG_TOK_IN=$(python3 -c "import json; d=json.load(open('$DG_FILE')); print(d['usage'].get('input_tokens',0))" 2>/dev/null || echo "0")
  DG_TOK_OUT=$(python3 -c "import json; d=json.load(open('$DG_FILE')); print(d['usage'].get('output_tokens',0))" 2>/dev/null || echo "0")
  DG_WALL=$(python3 -c "import json; d=json.load(open('$DG_FILE')); print(d.get('duration_ms',0))" 2>/dev/null || echo "0")
  DG_TOOLS=$(python3 -c "
import json
d=json.load(open('$DG_FILE'))
tools=0
for it in d.get('usage',{}).get('iterations',[]):
    tools += len(it.get('tool_calls',[]))
print(tools)
" 2>/dev/null || echo "0")
  echo "turns=$DG_TURNS tools=$DG_TOOLS cost=\$$DG_COST tokens=$DG_TOK_IN/$DG_TOK_OUT wall=${DG_WALL}ms"

  # ── Base 模式 ──
  BS_FILE="$OUTDIR/case${NUM}_base.json"
  echo -n "  [Base]    "
  /opt/homebrew/bin/claude -p --bare \
    --output-format json \
    --add-dir "$ROOT/.docgraph/export" \
    --dangerously-skip-permissions \
    --max-budget-usd 1.00 \
    --no-session-persistence \
    --system-prompt "$BASE_SYSTEM" \
    "$Q" > "$BS_FILE" 2>/dev/null || true

  BS_TURNS=$(python3 -c "import json; d=json.load(open('$BS_FILE')); print(d.get('num_turns',0))" 2>/dev/null || echo "0")
  BS_COST=$(python3 -c "import json; d=json.load(open('$BS_FILE')); print(f\"{d.get('total_cost_usd',0):.4f}\")" 2>/dev/null || echo "0.0000")
  BS_TOK_IN=$(python3 -c "import json; d=json.load(open('$BS_FILE')); print(d['usage'].get('input_tokens',0))" 2>/dev/null || echo "0")
  BS_TOK_OUT=$(python3 -c "import json; d=json.load(open('$BS_FILE')); print(d['usage'].get('output_tokens',0))" 2>/dev/null || echo "0")
  BS_WALL=$(python3 -c "import json; d=json.load(open('$BS_FILE')); print(d.get('duration_ms',0))" 2>/dev/null || echo "0")
  BS_TOOLS=$(python3 -c "
import json
d=json.load(open('$BS_FILE'))
tools=0
for it in d.get('usage',{}).get('iterations',[]):
    tools += len(it.get('tool_calls',[]))
print(tools)
" 2>/dev/null || echo "0")
  echo "  turns=$BS_TURNS tools=$BS_TOOLS cost=\$$BS_COST tokens=$BS_TOK_IN/$BS_TOK_OUT wall=${BS_WALL}ms"

  echo "$NUM,$TITLE,$DG_TURNS,$DG_TOOLS,$DG_TOK_IN,$DG_TOK_OUT,$DG_COST,$DG_WALL,$BS_TURNS,$BS_TOOLS,$BS_TOK_IN,$BS_TOK_OUT,$BS_COST,$BS_WALL" >> "$SUMMARY"
done

echo ""
echo "============================================================"
echo "完成！结果保存在: $OUTDIR"
echo "============================================================"

# 汇总
python3 -c "
import csv, os

rows = list(csv.DictReader(open('$SUMMARY')))
dg_cost = sum(float(r['dg_cost']) for r in rows)
bs_cost = sum(float(r['base_cost']) for r in rows)
dg_tokens = sum(int(r['dg_tokens_in'])+int(r['dg_tokens_out']) for r in rows)
bs_tokens = sum(int(r['base_tokens_in'])+int(r['base_tokens_out']) for r in rows)
dg_tools = sum(int(r['dg_tools']) for r in rows)
bs_tools = sum(int(r['base_tools']) for r in rows)
dg_turns = sum(int(r['dg_turns']) for r in rows)
bs_turns = sum(int(r['base_turns']) for r in rows)
dg_wall = sum(int(r['dg_wall_ms']) for r in rows) / 1000
bs_wall = sum(int(r['base_wall_ms']) for r in rows) / 1000

print(f\"\"\"
┌──────────────┬────────────┬────────────┬──────────┐
│ 指标         │ DocGraph   │ Base       │ 差异     │
├──────────────┼────────────┼────────────┼──────────┤
│ 总耗时(wall) │ {dg_wall:7.1f}s  │ {bs_wall:7.1f}s  │          │
│ 总轮次       │ {dg_turns:10d} │ {bs_turns:10d} │          │
│ 总工具调用   │ {dg_tools:10d} │ {bs_tools:10d} │          │
│ 总 Token     │ {dg_tokens:10d} │ {bs_tokens:10d} │ {(dg_tokens-bs_tokens)/max(bs_tokens,1)*100:+.0f}% │
│ 总成本       │ \${dg_cost:9.4f} │ \${bs_cost:9.4f} │ {(dg_cost-bs_cost)/max(bs_cost,0.0001)*100:+.0f}% │
└──────────────┴────────────┴────────────┴──────────┘
\"\"\")
"
