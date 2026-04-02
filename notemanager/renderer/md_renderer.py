"""Markdown to HTML renderer."""

import re

import markdown

from notemanager.indexer import extract_md_hashtags
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
    body = _convert_mermaid_blocks(body)

    # TOC from markdown toc extension
    toc_html = getattr(md, "toc", "") or ""

    # Add section numbers
    body, id_to_number = add_section_numbers(body)
    if toc_html and id_to_number:
        toc_html = add_toc_numbers(toc_html, id_to_number)

    return {"body": body, "toc": toc_html, "warnings": [], "dtags": extract_md_hashtags(content)}


def _convert_links(html: str) -> str:
    """Convert .md/.rst links in HTML to .html links, fix bare URLs."""
    def replace_link(match):
        prefix = match.group(1)
        path = match.group(2)
        suffix = match.group(3)
        if path.startswith(("http://", "https://", "#", "mailto:")):
            return match.group(0)
        # Fix bare www. URLs
        if path.startswith("www."):
            return f'{prefix}http://{path}{suffix}'
        for ext in (".md", ".rst"):
            if path.endswith(ext):
                path = path[: -len(ext)] + ".html"
                break
        return f'{prefix}{path}{suffix}'

    return re.sub(r'(href=")([^"]*)(")', replace_link, html)


def _convert_mermaid_blocks(html: str) -> str:
    """Convert mermaid code blocks to <pre class="mermaid"> for client-side rendering."""
    # codehilite wraps them in <div class="highlight"><pre>...<code>CONTENT</code></pre></div>
    # where CONTENT starts with a mermaid keyword
    mermaid_start = r'(?:graph |pie\s|flowchart |sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt |journey |gitGraph)'
    pattern = (
        r'<div class="highlight"><pre[^>]*><span></span>'
        r'<code>(' + mermaid_start + r'.*?)</code></pre></div>'
    )
    def _replace(m):
        code = m.group(1)
        code = code.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        code = code.replace("&quot;", '"').replace("&#39;", "'")
        return f'<pre class="mermaid">{code}</pre>'
    html = re.sub(pattern, _replace, html, flags=re.DOTALL)
    # Also handle plain <pre><code> without highlight wrapper
    pattern2 = (
        r'<pre><code>(' + mermaid_start + r'.*?)</code></pre>'
    )
    html = re.sub(pattern2, _replace, html, flags=re.DOTALL)
    return html
