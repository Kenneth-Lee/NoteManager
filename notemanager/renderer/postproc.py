"""HTML post-processing utilities shared by RST and MD renderers.

Provides:
- Section numbering (auto chapter/section numbers)
- TOC numbering (apply section numbers to TOC links)
"""

import re
from html.parser import HTMLParser


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
    lines = ['<div class="toc"><ul>']
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
