"""Markdown to HTML renderer."""

import re

import markdown


def render_md(content: str, source_path: str = "<string>") -> str:
    """Render Markdown content to an HTML body fragment.

    Args:
        content: The Markdown source text.
        source_path: Source file path for reference.

    Returns:
        HTML body fragment.
    """
    md = markdown.Markdown(
        extensions=[
            "fenced_code",
            "tables",
            "toc",
            "md_in_html",
        ],
        extension_configs={
            "fenced_code": {},
        },
    )
    html = md.convert(content)
    return _convert_links(html)


def _convert_links(html: str) -> str:
    """Convert .md/.rst links in HTML to .html links."""
    # Match href attributes containing .md or .rst
    def replace_link(match):
        prefix = match.group(1)
        path = match.group(2)
        if path.startswith(("http://", "https://", "#", "mailto:")):
            return match.group(0)
        # Replace .md or .rst suffix with .html
        for ext in (".md", ".rst"):
            if path.endswith(ext):
                path = path[:-len(ext)] + ".html"
                break
        return f'{prefix}"{path}"'

    return re.sub(r'(href=")([^"]*)(")', replace_link, html)
