"""VLM 整页兜底 —— 让 table_entity 等 extractor
在文本启发式 + LLM 都召回不足时，直接把页图喂给 VLM 抽数据。

设计：
- 只在 page.quality.needs_vlm == True 且 page.rendered_image_path 可用时才走
- 每页有 hits_per_page 上限避免成本失控
- 失败优雅 fallback（DeepSeek 无 vision → 警告 + 继续）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from docgraph.core.logger import get_logger
from docgraph.graph.schema import ParsedPage

log = get_logger(__name__)


def page_needs_vlm_for(page: ParsedPage, reasons: set[str]) -> bool:
    """判断该页是否应该走某类 VLM 兜底。

    reasons：当前 extractor 关心的 reason 集合，例如：
      register → {"register_with_table", "scan_like_no_text"}
      pin      → {"pin_with_table",      "scan_like_no_text"}
      timing   → {"timing_with_table",   "scan_like_no_text"}
    """
    if page.quality is None or not page.quality.needs_vlm:
        return False
    if not page.rendered_image_path:
        return False
    if not Path(page.rendered_image_path).is_file():
        return False
    return any(r in reasons for r in page.quality.vlm_reasons)


def vlm_extract(
    *,
    vlm_client: Any,
    image_path: Path | str,
    prompt: str,
    schema: type[BaseModel],
    extractor: str,
    max_tokens: int = 3072,
) -> BaseModel | None:
    """让 VLM 看页图按 Pydantic schema 输出。

    DeepSeek 等无 vision 的 provider 会在 describe() 内抛错 → 这里捕获返回 None。
    """
    import json
    sys_prompt = (
        "你正在分析芯片 spec 文档的一页。请只返回**唯一一个 JSON 对象**，"
        "不要 markdown 代码块、不要解释文字。JSON 必须严格匹配以下 schema：\n"
        + json.dumps(schema.model_json_schema(), ensure_ascii=False)
    )
    if getattr(vlm_client, "disabled", False):
        return None
    try:
        resp = vlm_client.describe(
            Path(image_path), prompt,
            system=sys_prompt, max_tokens=max_tokens,
            extractor=extractor,
        )
    except Exception as e:
        # Fast-fail 已经会在 VLMClient 内部 warning；这里避免每页刷屏
        if not getattr(vlm_client, "disabled", False):
            log.warning(f"[{extractor}] VLM describe failed: {str(e)[:120]}")
        return None
    if not resp or not resp.text:
        return None
    from docgraph.llm.client import _extract_json
    try:
        data = _extract_json(resp.text)
        return schema.model_validate(data)
    except Exception as e:
        log.warning(f"[{extractor}] VLM JSON parse failed: {str(e)[:120]}")
        return None
