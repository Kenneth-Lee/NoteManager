"""Markdown to HTML renderer."""

import html as _htmlmod
import re

import markdown

from notemanager.indexer import extract_md_hashtags
from notemanager.renderer.postproc import clean_paragraph_newlines
from notemanager.renderer.preformatted import mark_diagram_code_blocks

# Math spans ($$..$$, $..$, \(..\), \[..\], \begin{align}..) are rendered client-side
# by KaTeX auto-render. Python-Markdown applies backslash escapes (\\ -> \, \{ -> {,
# \! -> ! ...) which silently corrupts LaTeX row separators and commands, so math
# spans are stashed as placeholders before conversion and restored afterwards.
_MATH_TOKEN_PREFIX = "nmrMathSpan"
_MATH_TOKEN_RE = re.compile(r"nmrMathSpan\d+Zqx")
_MATH_INLINE_DOLLAR = re.compile(r"(?<![\\$])\$[^$\n]+?\$")
_MATH_DISPLAY_DOLLAR = re.compile(r"\$\$[\s\S]+?\$\$")
_MATH_BRACE_PAREN = re.compile(r"\\\([\s\S]+?\\\)")
_MATH_BRACE_SQUARE = re.compile(r"\\\[[\s\S]+?\\\]")
_MATH_ENV = re.compile(r"\\begin\{(align\*?|equation\*?|gather\*?)\}[\s\S]*?\\end\{\1\}")
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


def _protect_math(content: str) -> tuple[str, dict]:
    """Replace math spans with markdown-safe placeholder tokens.

    Fenced code blocks (``` / ~~~) are left untouched so shell snippets with $$
    cannot swallow surrounding text into one placeholder.
    """
    if _MATH_TOKEN_PREFIX in content:
        # Placeholder collision with user text: keep source untouched.
        return content, {}

    store: dict[str, str] = {}

    def _stash(match: re.Match) -> str:
        token = f"{_MATH_TOKEN_PREFIX}{len(store)}Zqx"
        store[token] = match.group(0)
        return token

    def _substitute(text: str) -> str:
        # Order matters: display delimiters before inline ones.
        text = _MATH_DISPLAY_DOLLAR.sub(_stash, text)
        text = _MATH_ENV.sub(_stash, text)
        text = _MATH_BRACE_SQUARE.sub(_stash, text)
        text = _MATH_BRACE_PAREN.sub(_stash, text)
        text = _MATH_INLINE_DOLLAR.sub(_stash, text)
        return text

    out_parts: list[str] = []
    segment: list[str] = []
    in_fence = False
    for line in content.splitlines(keepends=True):
        if _FENCE_RE.match(line):
            out_parts.append(_substitute("".join(segment)))
            segment = []
            in_fence = not in_fence
            out_parts.append(line)
            continue
        if in_fence:
            out_parts.append(_substitute("".join(segment)))
            segment = []
            out_parts.append(line)
        else:
            segment.append(line)
    out_parts.append(_substitute("".join(segment)))
    return "".join(out_parts), store


def _restore_math(html_text: str, store: dict) -> str:
    """Put stashed math back, HTML-escaped so textContent matches the source."""
    if not store:
        return html_text

    def _unstash(match: re.Match) -> str:
        tex = store.get(match.group(0))
        if tex is None:
            return match.group(0)
        return _htmlmod.escape(tex, quote=False)

    return _MATH_TOKEN_RE.sub(_unstash, html_text)


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
    body, math_store = _protect_math(content)
    body = md.convert(body)
    body = _restore_math(body, math_store)
    body = _convert_links(body)
    body = _convert_mermaid_blocks(body)
    body = mark_diagram_code_blocks(body)
    # Merge soft-wrapped newlines inside <p> (browsers render "\n" as a space;
    # between CJK chars that space is unwanted). <pre>/<code> untouched.
    body = clean_paragraph_newlines(body)

    # TOC from markdown toc extension
    toc_html = _restore_math(getattr(md, "toc", "") or "", math_store)

    return {"body": body, "toc": toc_html, "warnings": [], "dtags": extract_md_hashtags(content)}


def _convert_links(html: str) -> str:
    """Convert .md/.rst/.txt links in HTML to .html links, fix bare URLs."""
    def replace_link(match):
        prefix = match.group(1)
        path = match.group(2)
        suffix = match.group(3)
        if path.startswith(("http://", "https://", "#", "mailto:")):
            return match.group(0)
        # Fix bare www. URLs
        if path.startswith("www."):
            return f'{prefix}http://{path}{suffix}'
        for ext in (".md", ".rst", ".txt"):
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
        # Keep HTML entities encoded so browser textContent preserves Mermaid
        # source literally (e.g. <<interface>>) instead of parsing as tags.
        return f'<pre class="mermaid">{code}</pre>'
    html = re.sub(pattern, _replace, html, flags=re.DOTALL)
    # Also handle plain <pre><code> without highlight wrapper
    pattern2 = (
        r'<pre><code>(' + mermaid_start + r'.*?)</code></pre>'
    )
    html = re.sub(pattern2, _replace, html, flags=re.DOTALL)
    return html
