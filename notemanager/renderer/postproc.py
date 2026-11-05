"""HTML post-processing utilities shared by RST and MD renderers.

Provides:
- Section numbering (auto chapter/section numbers)
- TOC numbering (apply section numbers to TOC links)
"""

import re
from html.parser import HTMLParser


def fix_docutils_html5_heading_levels(html: str) -> str:
    """Remap docutils ``html5_polyglot`` headings to a real hierarchy.

    Docutils emits ``<h1>`` for every section title nested in ``<section>``,
    which makes multiple RST section titles look like sibling top-level
    headings (and breaks hierarchical section numbering).

    We remap non-title ``<h1>`` inside nested ``<section>`` to
    ``<h{depth+1}>`` (capped at 6) based on ``<section>`` nesting depth.
    The document title ``<h1 class="title">`` is left as ``<h1>``.
    """

    class _DocutilsHeadingFixer(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=False)
            self.parts: list[str] = []
            self.section_depth = 0
            self._h_close_stack: list[str] = []

        def get_result(self) -> str:
            return "".join(self.parts)

        def handle_starttag(self, tag: str, attrs):
            if tag == "section":
                self.section_depth += 1
            if tag == "h1":
                ad = dict(attrs)
                cls = ad.get("class", "")
                if isinstance(cls, list):
                    cls = " ".join(cls)
                is_title = cls == "title" or "title" in cls.split()
                if is_title or self.section_depth == 0:
                    self.parts.append(_fmt_tag("h1", attrs))
                    self._h_close_stack.append("h1")
                else:
                    # docutils html5_polyglot 会把顶层 section 的标题也包进
                    # <section> 中，因此此时 self.section_depth==1。为了让顶层
                    # 标题仍落到 h1（非 document title），这里把层级整体上移一档。
                    level = min(6, self.section_depth)
                    nt = f"h{level}"
                    self.parts.append(_fmt_tag(nt, attrs))
                    self._h_close_stack.append(nt)
                return
            self.parts.append(_fmt_tag(tag, attrs))

        def handle_endtag(self, tag: str):
            if tag == "h1":
                t = self._h_close_stack.pop() if self._h_close_stack else "h1"
                self.parts.append(f"</{t}>")
                return
            if tag == "section":
                self.section_depth = max(0, self.section_depth - 1)
            self.parts.append(f"</{tag}>")

        def handle_data(self, data: str):
            self.parts.append(data)

        def handle_entityref(self, name: str):
            self.parts.append(f"&{name};")

        def handle_charref(self, name: str):
            self.parts.append(f"&#{name};")

        def handle_comment(self, data: str):
            self.parts.append(f"<!--{data}-->")

        def handle_decl(self, decl: str):
            self.parts.append(f"<!{decl}>")

        def handle_pi(self, data: str):
            self.parts.append(f"<?{data}>")

    parser = _DocutilsHeadingFixer()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return html
    return parser.get_result()


class SectionNumberingParser(HTMLParser):
    """Add section numbers to HTML headings.

    Walks all <h1>..<h6> tags and prepends ``<span class="section-number">N.N.</span>``
    based on hierarchical position.  Also builds an ``id_to_number`` mapping so
    that a TOC can be synchronised afterwards.
    """

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._in_heading = False
        self._level = 0
        self._text_parts: list[str] = []
        self._attrs: dict[str, str] = {}
        self._heading_id: str = ""
        self.counters = [0] * 6
        self.id_to_number: dict[str, str] = {}

    # --- HTMLParser callbacks ---

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._in_heading = True
            self._level = int(tag[1])
            self._text_parts = []
            self._attrs = dict(attrs)
            self._heading_id = self._attrs.get("id", "")
        else:
            self.parts.append(_fmt_tag(tag, attrs))

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._in_heading:
            level = self._level
            for i in range(level, 6):
                self.counters[i] = 0
            self.counters[level - 1] += 1
            number = ".".join(
                str(self.counters[i])
                for i in range(level)
                if self.counters[i] > 0
            )
            if self._heading_id:
                self.id_to_number[self._heading_id] = number
            text = "".join(self._text_parts).strip()
            numbered = f'<span class="section-number">{number}.</span> {text}'
            self.parts.append(_fmt_tag(tag, list(self._attrs.items())) + numbered + f"</{tag}>")
            self._in_heading = False
        else:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if self._in_heading:
            self._text_parts.append(data)
        else:
            self.parts.append(data)

    def get_result(self) -> str:
        return "".join(self.parts)


class TocNumberingParser(HTMLParser):
    """Add section numbers to TOC links."""

    def __init__(self, id_to_number: dict[str, str]):
        super().__init__()
        self.parts: list[str] = []
        self._id_to_number = id_to_number
        self._in_link = False
        self._link_text_parts: list[str] = []
        self._link_attrs: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._in_link = True
            self._link_attrs = dict(attrs)
            self._link_text_parts = []
        else:
            self.parts.append(_fmt_tag(tag, attrs))

    def handle_endtag(self, tag):
        if tag == "a" and self._in_link:
            href = self._link_attrs.get("href", "")
            link_text = "".join(self._link_text_parts).strip()
            anchor = href.lstrip("#")
            if anchor in self._id_to_number:
                num = self._id_to_number[anchor]
                link_text = f'<span class="section-number">{num}.</span> {link_text}'
            self.parts.append(_fmt_tag(tag, list(self._link_attrs.items())) + link_text + "</a>")
            self._in_link = False
        else:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if self._in_link:
            self._link_text_parts.append(data)
        else:
            self.parts.append(data)

    def get_result(self) -> str:
        return "".join(self.parts)


def add_section_numbers(html: str) -> tuple[str, dict[str, str]]:
    """Add section numbers to all headings.  Returns (html, id_to_number)."""
    parser = SectionNumberingParser()
    try:
        parser.feed(html)
    except Exception:
        return html, {}
    return parser.get_result(), parser.id_to_number


def add_toc_numbers(toc_html: str, id_to_number: dict[str, str]) -> str:
    """Add section numbers to TOC links."""
    parser = TocNumberingParser(id_to_number)
    try:
        parser.feed(toc_html)
    except Exception:
        return toc_html
    return parser.get_result()


def generate_toc_from_headings(headings: list[dict]) -> str:
    """Generate a TOC ``<div>`` from heading info dicts.

    Each dict: ``{"level": int, "id": str, "text": str}``.
    """
    if not headings:
        return ""
    # RST 文档标题常不在 section 里，首个条目可能是 h2/h3；归一化避免顶层多包一层 <ul>
    min_lv = min(h["level"] for h in headings)
    headings = [{**h, "level": h["level"] - min_lv + 1} for h in headings]
    # 首项用 prev_level==0 分支展开 <ul>；不要在此处先写死一层 <ul>，否则会多嵌套一层
    lines = ['<div class="toc">']
    prev_level = 0
    for h in headings:
        level = h["level"]
        hid = h["id"]
        text = h["text"]
        if prev_level == 0:
            for _ in range(level):
                lines.append("<ul>")
        elif level > prev_level:
            for _ in range(prev_level, level):
                lines.append("<ul>")
        elif level < prev_level:
            for _ in range(prev_level - level):
                lines.append("</li></ul>")
            lines.append("</li>")
        else:
            lines.append("</li>")
        prev_level = level
        lines.append(f'<li><a href="#{hid}">{text}</a>')
    for _ in range(prev_level):
        lines.append("</li></ul>")
    lines.append("</div>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _fmt_tag(tag, attrs) -> str:
    if attrs:
        a = " ".join(f'{k}="{v}"' for k, v in attrs)
        return f"<{tag} {a}>"
    return f"<{tag}>"


# ---------------------------------------------------------------------------
# Soft-newline merging (CJK-aware)
# ---------------------------------------------------------------------------

# A newline inside a paragraph ("soft wrap") is rendered by browsers as a
# space. That is correct for Western text ("text wraps" needs the space) but
# wrong between CJK characters, where no space should appear.
_CJK_RANGES = (
    (0x3001, 0x303F),    # CJK 标点（。、《》…），不含 U+3000 全角空格
    (0x3040, 0x30FF),    # 平假名 / 片假名
    (0x31F0, 0x31FF),
    (0x3400, 0x4DBF),    # CJK 扩展 A
    (0x4E00, 0x9FFF),    # CJK 基本区
    (0xF900, 0xFAFF),    # CJK 兼容表意
    (0xFF01, 0xFF60),    # 全角标点 / 字母 / 数字（！？，：）
    (0x20000, 0x2FFFF),  # CJK 扩展 B 及以上
)


def _is_cjk_char(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def _prev_text_char(content: str, pos: int) -> str:
    """Nearest visible text char before ``pos``, skipping tags and whitespace."""
    j = pos - 1
    while j >= 0:
        c = content[j]
        if c == ">":
            j = content.rfind("<", 0, j)
            if j < 0:
                return ""
            j -= 1
            continue
        if c.isspace():
            j -= 1
            continue
        return c
    return ""


def _next_text_char(content: str, pos: int) -> str:
    """Nearest visible text char after ``pos``, skipping tags and whitespace."""
    j = pos + 1
    n = len(content)
    while j < n:
        c = content[j]
        if c == "<":
            k = content.find(">", j)
            if k < 0:
                return ""
            j = k + 1
            continue
        if c.isspace():
            j += 1
            continue
        return c
    return ""


def _merge_soft_newlines(content: str) -> str:
    """Replace newlines with spaces, or nothing between CJK text chars."""
    if "\n" not in content:
        return content
    out = []
    for i, ch in enumerate(content):
        if ch != "\n":
            out.append(ch)
            continue
        prev = _prev_text_char(content, i)
        nxt = _next_text_char(content, i)
        if prev and nxt and _is_cjk_char(prev) and _is_cjk_char(nxt):
            continue  # CJK|CJK: drop the newline entirely
        out.append(" ")  # Western/other context: newline renders as a space
    return "".join(out)


def clean_paragraph_newlines(html: str) -> str:
    """Merge soft-wrapped newlines inside <p> tags only.

    - ``中文\\n中文`` -> ``中文中文``（换行处不留空格）
    - ``text\\nwraps`` -> ``text wraps``（西文保留空格）
    Newlines elsewhere (``<pre>``, code blocks, table cells) are untouched.
    """
    class _PCleaner(HTMLParser):
        def __init__(self):
            # convert_charrefs=False: keep entities verbatim so that e.g.
            # "&lt;" is not decoded and re-emitted as a raw "<".
            super().__init__(convert_charrefs=False)
            self.parts = []
            self._in_p = False
            self._p_content = []

        def handle_starttag(self, tag, attrs):
            s = self._fmt(tag, attrs)
            if tag == "p" and not self._in_p:
                self._in_p = True
                self._p_content = []
                self.parts.append(s)
            elif self._in_p:
                self._p_content.append(s)
            else:
                self.parts.append(s)

        def handle_endtag(self, tag):
            s = f"</{tag}>"
            if tag == "p" and self._in_p:
                content = "".join(self._p_content)
                content = _merge_soft_newlines(content)
                content = re.sub(r"  +", " ", content)
                self.parts.append(content)
                self.parts.append(s)
                self._in_p = False
                self._p_content = []
            elif self._in_p:
                self._p_content.append(s)
            else:
                self.parts.append(s)

        def handle_startendtag(self, tag, attrs):
            s = self._fmt(tag, attrs, self_closing=True)
            if self._in_p:
                self._p_content.append(s)
            else:
                self.parts.append(s)

        def handle_data(self, data):
            if self._in_p:
                self._p_content.append(data)
            else:
                self.parts.append(data)

        def handle_entityref(self, name):
            s = f"&{name};"
            if self._in_p:
                self._p_content.append(s)
            else:
                self.parts.append(s)

        def handle_charref(self, name):
            s = f"&#{name};"
            if self._in_p:
                self._p_content.append(s)
            else:
                self.parts.append(s)

        def handle_comment(self, data):
            s = f"<!--{data}-->"
            if self._in_p:
                self._p_content.append(s)
            else:
                self.parts.append(s)

        @staticmethod
        def _fmt(tag, attrs, self_closing=False):
            if attrs:
                a = " ".join(f'{k}="{v}"' for k, v in attrs)
                s = f"<{tag} {a}>"
            else:
                s = f"<{tag}>"
            return s[:-1] + " />" if self_closing else s

        def result(self):
            return "".join(self.parts)

    parser = _PCleaner()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return html
    return parser.result()
