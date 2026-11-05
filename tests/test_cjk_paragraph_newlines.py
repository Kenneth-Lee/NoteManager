"""CJK-aware soft-newline merging in rendered paragraphs.

A newline inside a paragraph is displayed by browsers as a space. Between CJK
characters that space is wrong ("中文 中文"), between Western words it is
required ("text wraps"). Regression tests for clean_paragraph_newlines and the
RST/MD renderers that call it.
"""

import re

from notemanager.renderer.md_renderer import render_md
from notemanager.renderer.postproc import clean_paragraph_newlines
from notemanager.renderer import rst_renderer


def paragraphs(html: str) -> list[str]:
    return re.findall(r"<p>.*?</p>", html, re.S)


def test_cjk_newline_no_space():
    assert clean_paragraph_newlines("<p>中文\n换行</p>") == "<p>中文换行</p>"


def test_newline_cjk_western_boundary_no_space():
    # 中文书写中英文混排不需要词间空格：任一侧是 CJK 就不留空格。
    assert clean_paragraph_newlines("<p>中文\nEnglish</p>") == "<p>中文English</p>"
    assert clean_paragraph_newlines("<p>English\n中文</p>") == "<p>English中文</p>"
    assert clean_paragraph_newlines("<p>数值为\n123</p>") == "<p>数值为123</p>"
    assert clean_paragraph_newlines("<p>123\n个文件</p>") == "<p>123个文件</p>"


def test_western_newline_keeps_space():
    assert clean_paragraph_newlines("<p>text\nwraps</p>") == "<p>text wraps</p>"


def test_newline_across_inline_tags():
    # Newline between tags: skip tags when finding neighbour text chars.
    html = "<p>中文<span>甲</span>\n<span>乙</span>中文</p>"
    assert clean_paragraph_newlines(html) == "<p>中文<span>甲</span><span>乙</span>中文</p>"
    html = "<p>text<span>x</span>\n<span>y</span>text</p>"
    assert clean_paragraph_newlines(html) == "<p>text<span>x</span> <span>y</span>text</p>"


def test_cjk_punctuation_counts_as_cjk():
    assert clean_paragraph_newlines("<p>结束。\n下一句</p>") == "<p>结束。下一句</p>"


def test_pre_blocks_untouched():
    html = "<pre><code>a $1\nb $2</code></pre>"
    assert clean_paragraph_newlines(html) == html


def test_entities_kept_verbatim():
    assert clean_paragraph_newlines("<p>a &lt; b\nc &amp; d</p>") == "<p>a &lt; b c &amp; d</p>"


def test_render_rst_cjk_wrap():
    out = rst_renderer.render_rst("这是一个测试段落\n这里手动换行了\n第二行内容继续。\n")
    assert paragraphs(out["body"]) == ["<p>这是一个测试段落这里手动换行了第二行内容继续。</p>"]
    out = rst_renderer.render_rst("English text wraps\nacross lines.\n")
    assert paragraphs(out["body"]) == ["<p>English text wraps across lines.</p>"]
    # 中文-英文换行边界不留空格
    out = rst_renderer.render_rst("使用Python\n实现的功能。\n")
    assert paragraphs(out["body"]) == ["<p>使用Python实现的功能。</p>"]


def test_render_md_cjk_wrap():
    out = render_md("这是一个测试段落\n这里手动换行了\n第二行内容继续。\n", "/tmp/x.md")
    assert paragraphs(out["body"]) == ["<p>这是一个测试段落这里手动换行了第二行内容继续。</p>"]
    out = render_md("English text wraps\nacross lines.\n", "/tmp/x.md")
    assert paragraphs(out["body"]) == ["<p>English text wraps across lines.</p>"]
    # 中文-英文换行边界不留空格
    out = render_md("使用Python\n实现的功能。\n", "/tmp/x.md")
    assert paragraphs(out["body"]) == ["<p>使用Python实现的功能。</p>"]
    # Fenced code blocks must keep newlines verbatim.
    out = render_md("```\na $1\nb $2\n```\n", "/tmp/x.md")
    assert "a $1\nb $2" in out["body"]
