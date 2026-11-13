"""分屏/回顶/目录定位源码三项界面优化的服务端部分测试。

覆盖：
- RST/MD 渲染器生成的 heading_lines（锚点 id -> 源码 1-based 行号）映射；
- wrap_html 输出包含 nm-heading-map、分屏按钮、回顶按钮与新布局样式；
- builder 仍可使用从渲染器移入的标题辅助函数（标题选取回归）。
"""

import re

from notemanager.builder import _DEFAULT_JS, _md_collect_headings, _rst_clean_title_text
from notemanager.renderer.md_renderer import render_md
from notemanager.renderer.rst_renderer import render_rst
from notemanager.renderer.template import wrap_html


def _section_ids_in_order(body: str) -> list[str]:
    return re.findall(r'<section[^>]*\bid="([^"]*)"', body)


def test_rst_heading_line_map_basic_and_roles() -> None:
    content = "\n".join([
        "Alpha Doc",
        "=========",
        "",
        "intro text",
        "",
        "First Part",
        "----------",
        "",
        "text one",
        "",
        "Second Part",
        "~~~~~~~~~~~",
        "",
        "Third Part with role :index:`target`",
        "------------------------------------",
        "",
        "text three",
        "",
    ])
    result = render_rst(content, "sample.rst")
    hmap = result["heading_lines"]
    ids = _section_ids_in_order(result["body"])

    assert len(ids) >= 3
    first, second, third = ids[0], ids[1], ids[2]
    assert hmap[first] == 6       # First Part 标题行
    assert hmap[second] == 11     # Second Part 标题行
    assert hmap[third] == 14      # 带 role 的标题行


def test_rst_heading_line_map_over_and_under() -> None:
    """over+under（上下划线）形式的标题也应映射到标题所在行。"""
    content = "\n".join([
        "Doc Title",
        "=========",
        "",
        "Sec One",
        "----------",
        "",
        "Over Head",
        "~~~~~~~~~~",
        "",
        "plain text",
    ])
    result = render_rst(content, "over.rst")
    hmap = result["heading_lines"]
    ids = _section_ids_in_order(result["body"])
    assert ids, "应至少识别出一个 section"
    # over+under 形式标题在源码第 7 行
    assert hmap[ids[-1]] == 7


def test_md_heading_line_map_atx_setext_and_fenced() -> None:
    content = "\n".join([
        "# Alpha One",
        "",
        "intro",
        "",
        "## Second Two",
        "",
        "```python",
        "# not a heading",
        "```",
        "",
        "Setext Head",
        "============",
        "",
        "## Duplicate",
        "",
        "## Duplicate",
        "",
        "### Third with [link](http://x)",
        "",
    ])
    result = render_md(content, "sample.md")
    hmap = result["heading_lines"]

    assert hmap.get("alpha-one") == 1
    assert hmap.get("second-two") == 5
    # fenced code 内的 # 不应被当作标题
    assert "not-a-heading" not in {k for k in hmap}
    assert hmap.get("setext-head") == 11
    # 重复标题：两个 id（toc 扩展用 _1 去重）都应映射到各自行
    assert hmap.get("duplicate") == 14
    assert hmap.get("duplicate_1") == 16
    # 链接标记应被剥掉后再匹配
    assert hmap.get("third-with-link") == 18


def test_wrap_html_embeds_map_split_and_backtop() -> None:
    html = wrap_html(
        "<p>hello</p>",
        "t",
        "a/b.html",
        toc="",
        source="line1\nline2",
        warnings=[],
        source_path="a/b.rst",
        heading_lines={"sec-one": 3, "sec-two": 9},
    )
    assert '<script type="application/json" id="nm-heading-map">{"sec-one": 3, "sec-two": 9}</script>' in html
    assert 'id="nm-split-btn"' in html
    assert 'id="nm-sync-btn"' in html
    assert 'id="nm-backtop"' in html
    # 新布局：主区固定视口高、面板内部滚动
    assert "height: 100vh" in html
    assert "body.nm-split .nm-tab-panels" in html
    assert "body:not(.nm-split) #nm-sync-btn" in html
    assert "scroll-margin-top: 16px" in html
    assert "@media print" in html


def test_wrap_html_without_map_embeds_empty_object() -> None:
    html = wrap_html("<p>x</p>", "t", "x.html", toc="")
    m = re.search(r'<script type="application/json" id="nm-heading-map">(.*?)</script>', html)
    assert m and m.group(1) == "{}"


def test_default_js_contains_layout_feature_hooks() -> None:
    assert "nm-layout-change" in _DEFAULT_JS
    assert "__nmGotoSourceLine" in _DEFAULT_JS
    assert "nm-heading-map" in _DEFAULT_JS
    assert "nm-backtop" in _DEFAULT_JS
    assert "nm-split-btn" in _DEFAULT_JS
    # 分屏双向同步滚动：基于 heading map 的滚动像素域分段线性插值，两端钉住
    assert "nm-sync-scroll" in _DEFAULT_JS
    assert "heightAtLine" in _DEFAULT_JS
    assert "buildSyncAnchors" in _DEFAULT_JS
    assert "interpSrcToDst" in _DEFAULT_JS
    assert "interpDstToSrc" in _DEFAULT_JS


def test_moved_title_helpers_still_work() -> None:
    # 从渲染器移入 builder 的两个辅助函数保持原行为
    # （_rst_clean_title_text 会把整段替换为 role 内容，这是历史行为）
    assert _rst_clean_title_text(":index:`bash` 技巧") == "bash"
    heads = _md_collect_headings(["# A", "", "para", "", "## B"])
    assert heads == [(1, 0, "A"), (2, 4, "B")]
