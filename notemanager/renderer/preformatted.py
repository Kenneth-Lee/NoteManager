"""Detection and HTML marking for ASCII diagram / preformatted code blocks."""

from __future__ import annotations

import html
import re

_DIAGRAM_CHARS = frozenset(
    "┌┐└┘│─┬┴┼├┤┬┴┼╔╗╚╝║═╠╣╦╩╬"
    "→←↑↓↔─━│┃▲▼◀▶►"
)


def is_ascii_diagram(text: str) -> bool:
    """Return True when *text* looks like a box-drawing ASCII diagram."""
    if not text or "\n" not in text:
        return False
    lines = text.splitlines()
    if len(lines) < 2:
        return False
    box_count = sum(1 for ch in text if ch in _DIAGRAM_CHARS)
    if box_count < 2:
        return False
    boxed_lines = sum(1 for ln in lines if any(ch in _DIAGRAM_CHARS for ch in ln))
    if boxed_lines < 1:
        return False
    return box_count >= max(2, len(lines) // 3)


def _extract_code_inner(fragment: str) -> str | None:
    match = re.search(r"<code[^>]*>(.*?)</code>", fragment, re.DOTALL | re.IGNORECASE)
    if match:
        return html.unescape(match.group(1))
    match = re.search(r"<pre[^>]*>(.*?)</pre>", fragment, re.DOTALL | re.IGNORECASE)
    if not match:
        return None
    inner = re.sub(r"<[^>]+>", "", match.group(1))
    return html.unescape(inner)


def mark_diagram_code_blocks(html_text: str) -> str:
    """Add ``nm-diagram`` class to diagram-like fenced/literal code blocks."""
    def _mark_highlight(match: re.Match[str]) -> str:
        block = match.group(0)
        if "nm-diagram" in block:
            return block
        code = _extract_code_inner(block)
        if code is None or not is_ascii_diagram(code):
            return block
        return block.replace('class="highlight"', 'class="highlight nm-diagram"', 1)

    html_text = re.sub(
        r'<div class="highlight"><pre[^>]*>.*?</pre></div>',
        _mark_highlight,
        html_text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    def _mark_plain_pre(match: re.Match[str]) -> str:
        block = match.group(0)
        if "nm-diagram" in block:
            return block
        code = _extract_code_inner(block)
        if code is None or not is_ascii_diagram(code):
            return block
        if re.search(r'<pre\s+class="', block, re.IGNORECASE):
            return re.sub(
                r'(<pre\s+class=")([^"]*)(")',
                r"\1\2 nm-diagram\3",
                block,
                count=1,
                flags=re.IGNORECASE,
            )
        return block.replace("<pre>", '<pre class="nm-diagram">', 1)

    html_text = re.sub(
        r"<pre(?:\s[^>]*)?><code[^>]*>.*?</code></pre>",
        _mark_plain_pre,
        html_text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    def _mark_literal(match: re.Match[str]) -> str:
        block = match.group(0)
        if "nm-diagram" in block:
            return block
        code = _extract_code_inner(block)
        if code is None or not is_ascii_diagram(code):
            return block
        return block.replace('class="literal-block"', 'class="literal-block nm-diagram"', 1)

    html_text = re.sub(
        r'<pre class="literal-block"[^>]*>.*?</pre>',
        _mark_literal,
        html_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return html_text
