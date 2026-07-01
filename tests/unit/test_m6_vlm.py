"""M6 测试：PageQuality 评估 + 整页渲染 + VLM 兜底逻辑。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# PageQuality 评估
# ---------------------------------------------------------------------------


def test_quality_scan_like_page():
    """无文本层的页 → 触发 scan_like_no_text。"""
    import pymupdf
    from docgraph.parsers.pymupdf_parser import PyMuPDFParser

    p = PyMuPDFParser()
    # 创建一份空白 PDF（无文本）
    with tempfile.TemporaryDirectory() as d:
        pdf_path = Path(d) / "blank.pdf"
        doc = pymupdf.open()
        doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        from docgraph.parsers.base import ParseContext
        parsed = p.parse(pdf_path, ParseContext(doc_id="t", cache_dir=Path(d)))

    assert len(parsed.pages) == 1
    q = parsed.pages[0].quality
    assert q is not None
    assert q.has_text_layer is False or q.text_chars < 80
    assert q.needs_vlm is True
    assert "scan_like_no_text" in q.vlm_reasons


def test_quality_normal_text_page():
    """有正常文本但没有 trigger 关键词 → needs_vlm = False。"""
    import pymupdf
    from docgraph.parsers.pymupdf_parser import PyMuPDFParser

    p = PyMuPDFParser()
    with tempfile.TemporaryDirectory() as d:
        pdf_path = Path(d) / "txt.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        # 写一段普通文字（不含 register/pin/timing 触发词）
        page.insert_text((72, 100), "This is a generic chapter about the system overview.\n"
                                    "It explains the architecture and design philosophy.\n"
                                    "Nothing here should trigger any VLM fallback at all.\n"
                                    "We just want some pages with text but no markers.\n")
        doc.save(str(pdf_path))
        doc.close()

        from docgraph.parsers.base import ParseContext
        parsed = p.parse(pdf_path, ParseContext(doc_id="t", cache_dir=Path(d)))

    q = parsed.pages[0].quality
    assert q is not None
    assert q.has_text_layer is True
    assert q.text_chars > 80
    assert q.needs_vlm is False
    assert q.vlm_reasons == []


def test_quality_register_table_page():
    """register + table caption 共现 → register_with_table 触发。"""
    import pymupdf
    from docgraph.parsers.pymupdf_parser import PyMuPDFParser

    p = PyMuPDFParser()
    with tempfile.TemporaryDirectory() as d:
        pdf_path = Path(d) / "reg.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 100),
            "Section 4.2 The CSW Register\n"
            "Table 4-1 CSW register bit assignments\n"
            "This register controls AHB transfers.\n"
            "It contains several important bit fields.\n")
        doc.save(str(pdf_path))
        doc.close()

        from docgraph.parsers.base import ParseContext
        parsed = p.parse(pdf_path, ParseContext(doc_id="t", cache_dir=Path(d)))

        # assert 必须在 with 块内（出块后临时目录会被清掉）
        q = parsed.pages[0].quality
        assert q is not None
        assert q.register_keyword_hits >= 1
        assert q.table_keyword_hits >= 1
        assert q.needs_vlm is True
        assert "register_with_table" in q.vlm_reasons
        assert parsed.pages[0].rendered_image_path is not None
        assert Path(parsed.pages[0].rendered_image_path).is_file()


def test_quality_timing_table_page():
    import pymupdf
    from docgraph.parsers.pymupdf_parser import PyMuPDFParser

    p = PyMuPDFParser()
    with tempfile.TemporaryDirectory() as d:
        pdf_path = Path(d) / "t.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 100),
            "Table 7-3 Timing Characteristics\n"
            "Symbol  Min  Typ  Max  Unit  Condition\n"
            "tSU     2    -    -    ns    VDD=3.3V\n")
        doc.save(str(pdf_path))
        doc.close()

        from docgraph.parsers.base import ParseContext
        parsed = p.parse(pdf_path, ParseContext(doc_id="t", cache_dir=Path(d)))

    q = parsed.pages[0].quality
    assert q is not None
    assert q.timing_keyword_hits >= 1
    assert q.table_keyword_hits >= 1
    assert "timing_with_table" in q.vlm_reasons


def test_page_render_idempotent():
    """同页渲染两次应该复用缓存。"""
    import pymupdf
    from docgraph.parsers.pymupdf_parser import PyMuPDFParser

    p = PyMuPDFParser()
    with tempfile.TemporaryDirectory() as d:
        pdf_path = Path(d) / "x.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 100), "Register table here\nTable 1\n")
        doc.save(str(pdf_path))
        doc.close()

        from docgraph.parsers.base import ParseContext
        cache_dir = Path(d) / "cache"
        ctx = ParseContext(doc_id="t", cache_dir=cache_dir)

        parsed1 = p.parse(pdf_path, ctx)
        img1 = parsed1.pages[0].rendered_image_path
        mtime1 = Path(img1).stat().st_mtime

        # 重 parse —— 应该复用同一张 PNG
        parsed2 = p.parse(pdf_path, ctx)
        img2 = parsed2.pages[0].rendered_image_path
        assert img1 == img2
        assert Path(img2).stat().st_mtime == mtime1


# ---------------------------------------------------------------------------
# VLM 兜底逻辑（mock vlm_client）
# ---------------------------------------------------------------------------


class FakeVLMResp:
    def __init__(self, text: str):
        self.text = text
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost_usd = 0.0
        self.cache_hit = False
        self.model = "mock"


class FakeVLM:
    def __init__(self, response: str, fail: bool = False):
        self.response = response
        self.fail = fail
        self.calls = 0

    def describe(self, image_path, prompt, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated VLM failure")
        return FakeVLMResp(self.response)


def test_vlm_backstop_page_needs_helper():
    """page_needs_vlm_for 工具函数（_vlm_backstop.py）"""
    from docgraph.extractors._vlm_backstop import page_needs_vlm_for
    from docgraph.graph.schema import PageQuality, ParsedPage

    p1 = ParsedPage(page_no=1); assert not page_needs_vlm_for(p1, {"register_with_table"})
    p2 = ParsedPage(page_no=1, quality=PageQuality(needs_vlm=False)); assert not page_needs_vlm_for(p2, {"register_with_table"})
    p3 = ParsedPage(page_no=1, quality=PageQuality(needs_vlm=True, vlm_reasons=["register_with_table"]))
    assert not page_needs_vlm_for(p3, {"register_with_table"})  # 无图
    p4 = ParsedPage(page_no=1, quality=PageQuality(needs_vlm=True, vlm_reasons=["register_with_table"]),
                    rendered_image_path="/no/such/file.png")
    assert not page_needs_vlm_for(p4, {"register_with_table"})


def test_page_needs_vlm_helper():
    """page_needs_vlm_for 工具函数。"""
    from docgraph.extractors._vlm_backstop import page_needs_vlm_for
    from docgraph.graph.schema import PageQuality, ParsedPage

    # 没 quality → False
    p1 = ParsedPage(page_no=1)
    assert not page_needs_vlm_for(p1, {"register_with_table"})

    # quality.needs_vlm=False → False
    p2 = ParsedPage(page_no=1, quality=PageQuality(needs_vlm=False))
    assert not page_needs_vlm_for(p2, {"register_with_table"})

    # 没渲染图 → False
    p3 = ParsedPage(page_no=1, quality=PageQuality(
        needs_vlm=True, vlm_reasons=["register_with_table"]))
    assert not page_needs_vlm_for(p3, {"register_with_table"})

    # 渲染图不存在 → False
    p4 = ParsedPage(page_no=1, quality=PageQuality(
        needs_vlm=True, vlm_reasons=["register_with_table"]),
        rendered_image_path="/no/such/file.png")
    assert not page_needs_vlm_for(p4, {"register_with_table"})


def test_vlm_backstop_vlm_extract_returns_none_on_no_vlm():
    """vlm_extract 在无 VLM 时返回 None，不抛。"""
    from docgraph.extractors._vlm_backstop import vlm_extract
    from docgraph.graph.schema import RegisterDef

    result = vlm_extract(
        vlm_client=None,
        image_path="/no/such/file.png",
        prompt="test",
        schema=RegisterDef,
        extractor="test",
    )
    assert result is None
