"""从已保存的整页 HTML 中取出「渲染」面板内的正文（与 build 时传入 indexer 的 body 一致）。"""

import re

_PANEL_END = re.compile(
    r'<div\s+id="nm-panel-source"',
    re.IGNORECASE,
)


def extract_render_panel_inner_html(full_page_html: str) -> str:
    """从 wrap_html 输出的整页中提取 ``#nm-panel-render`` 内部 HTML。"""
    key = 'id="nm-panel-render"'
    i = full_page_html.find(key)
    if i < 0:
        return full_page_html
    gt = full_page_html.find(">", i)
    if gt < 0:
        return full_page_html
    inner_start = gt + 1
    m = _PANEL_END.search(full_page_html, inner_start)
    if not m:
        return full_page_html[inner_start:]
    return full_page_html[inner_start : m.start()]
