"""Markdown to HTML renderer."""

import re

import markdown

from notemanager.renderer.postproc import add_section_numbers, add_toc_numbers


def _slugify(text, separator):
    """Generate heading ID, preserving Chinese characters."""
    slug = ""
    for char in text:
        if char.isalnum() or "\u4e00" <= char <= "\u9fff":
            slug += char
        elif char in "-_ ":
            slug += separator
        else:
            slug += separator
    slug = re.sub(rf"{separator}+", separator, slug).strip(separator)
    return slug.lower()


def render_md(content: str, source_path: str = "<string>") -> dict:
    """Render Markdown content to HTML.

    Args:
        content: The Markdown source text.
        source_path: Source file path for reference.

    Returns:
        Dict with keys: body (str), toc (str), warnings (list[str]).
    """
    md = markdown.Markdown(
        extensions=[
            "fenced_code",
            "codehilite",
            "tables",
            "toc",
            "md_in_html",
        ],
        extension_configs={
            "codehilite": {
                "css_class": "highlight",
                "guess_lang": False,
            },
            "toc": {
                "slugify": _slugify,
                "separator": "-",
            },
        },
    )
    body = md.convert(content)
    body = _convert_links(body)

    # TOC from markdown toc extension
    toc_html = getattr(md, "toc", "") or ""

    # Add section numbers
    body, id_to_number = add_section_numbers(body)
    if toc_html and id_to_number:
        toc_html = add_toc_numbers(toc_html, id_to_number)

    return {"body": body, "toc": toc_html, "warnings": []}


def _convert_links(html: str) -> str:
    """Convert .md/.rst links in HTML to .html links."""
    def replace_link(match):
        prefix = match.group(1)
        path = match.group(2)
        if path.startswith(("http://", "https://", "#", "mailto:")):
            return match.group(0)
        for ext in (".md", ".rst"):
            if path.endswith(ext):
                path = path[: -len(ext)] + ".html"
                break
        return f'{prefix}"{path}"'

    return re.sub(r'(href=")([^"]*)(")', replace_link, html)
