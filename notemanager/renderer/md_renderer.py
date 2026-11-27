"""Markdown to HTML renderer."""

import html as _htmlmod
import re

import markdown

from notemanager.indexer import extract_md_hashtags
from notemanager.renderer.postproc import clean_paragraph_newlines
from notemanager.renderer.preformatted import mark_diagram_code_blocks
from notemanager.renderer.frontmatter import extract_frontmatter

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


def _md_collect_headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """收集 Markdown 标题：(级别 1–6, 0-based 行号, 文本)。忽略 fenced code block 内行。"""
    result: list[tuple[int, int, str]] = []
    fence = False
    i = 0
    n = len(lines)
    while i < n:
        s = lines[i].strip()
        if s.startswith("```"):
            fence = not fence
            i += 1
            continue
        if fence:
            i += 1
            continue
        stripped = lines[i].lstrip()
        if not stripped:
            i += 1
            continue
        m = re.match(r"^(#{1,6})(?:\s+|$)(.*)$", stripped)
        if m:
            level = len(m.group(1))
            rest = (m.group(2) or "").strip()
            rest = re.sub(r"\s+#+\s*$", "", rest).strip()
            if rest:
                result.append((level, i, rest))
            i += 1
            continue
        if i + 1 < n:
            nxt = lines[i + 1].strip()
            cur = stripped
            if cur and not cur.startswith("#"):
                if len(nxt) >= 3 and all(c == "=" for c in nxt):
                    result.append((1, i, cur))
                    i += 2
                    continue
                if len(nxt) >= 3 and all(c == "-" for c in nxt):
                    result.append((2, i, cur))
                    i += 2
                    continue
        i += 1
    return result


def _flatten_toc_tokens(tokens) -> list[dict]:
    """把 toc 扩展的嵌套 token 树展平为文档顺序列表。"""
    out: list[dict] = []
    for t in tokens or []:
        out.append(t)
        out.extend(_flatten_toc_tokens(t.get("children")))
    return out


def _norm_md_heading(text: str) -> str:
    """规范化 MD 标题文本：去链接/强调等标记与空白，小写。"""
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text or "")
    t = re.sub(r"[`*_~\[\]!]", "", t)
    t = re.sub(r"\s+", "", t)
    return t.lower()


def _build_heading_line_map(content: str, toc_tokens) -> dict[str, int]:
    """按文档顺序把 toc token 的 id 映射到源码行号（1-based，尽力而为）。"""
    src_heads = _md_collect_headings(content.splitlines())
    flat = _flatten_toc_tokens(toc_tokens)
    out: dict[str, int] = {}
    idx = 0
    for tok in flat:
        target = _norm_md_heading(str(tok.get("name", "")))
        if not target:
            continue
        for j in range(idx, len(src_heads)):
            level, line0, text = src_heads[j]
            cand = _norm_md_heading(text)
            hit = (
                cand == target
                or (len(target) >= 4 and cand.startswith(target))
                or (len(cand) >= 4 and target.startswith(cand))
            )
            if hit:
                tid = str(tok.get("id", ""))
                if tid:
                    out[tid] = line0 + 1
                idx = j + 1
                break
    return out


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
            "attr_list",
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
    source, fm_tags, _metadata, fm_warnings = extract_frontmatter(content)
    body, math_store = _protect_math(source)
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
    # 行号扫描用原始 content（数学 stash 会把多行公式替换成单行 token，改变行数）
    heading_lines = _build_heading_line_map(content, getattr(md, "toc_tokens", None))

    dtags = list(fm_tags)
    for tag in extract_md_hashtags(source):
        if tag not in dtags:
            dtags.append(tag)
    return {"body": body, "toc": toc_html, "warnings": fm_warnings, "dtags": dtags,
            "heading_lines": heading_lines}


def _convert_links(html: str) -> str:
    """Convert .md/.rst/.txt links in HTML to .html links, fix bare URLs."""
    def replace_link(match):
        prefix = match.group(1)
        path = match.group(2)
        suffix = match.group(3)
        keyword = path.lower()
        if keyword.startswith(("dtag:", "index:")):
            data = _htmlmod.escape(path, quote=True)
            return f'{prefix}#{suffix} data-nm-keyword-ref="{data}"'
        if path.startswith(("http://", "https://", "#", "mailto:")):
            return match.group(0)
        # Fix bare www. URLs
        if path.startswith("www."):
            return f'{prefix}http://{path}{suffix}'
        # Keep URL fragments intact: a.md#section -> a.html#section.
        base, sep, fragment = path.partition("#")
        for ext in (".md", ".rst", ".txt"):
            if base.endswith(ext):
                path = base[: -len(ext)] + ".html" + (sep + fragment if sep else "")
                break
        return f'{prefix}{path}{suffix}'

    return re.sub(r'(href=")([^"]*)(")', replace_link, html)


def _convert_mermaid_blocks(html: str) -> str:
    """Convert mermaid code blocks to <pre class="mermaid"> for client-side rendering."""
    # codehilite wraps them in <div class="highlight"><pre>...<code>CONTENT</code></pre></div>
    # where CONTENT starts with a mermaid keyword
    mermaid_start = (
        r'(?:graph\b|pie\b|flowchart\b|sequenceDiagram\b|classDiagram\b|'
        r'stateDiagram(?:-v2)?\b|erDiagram\b|gantt\b|journey\b|gitGraph\b|'
        r'mindmap\b|timeline\b|quadrantChart\b|block-beta\b)'
    )
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
