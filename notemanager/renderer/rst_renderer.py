"""RST to HTML renderer using docutils."""

import contextlib
import io
import os
import re
from html.parser import HTMLParser

from docutils.core import publish_parts

from notemanager.renderer.roles import register_roles
from notemanager.renderer.directives import register_directives
from notemanager.renderer.postproc import (
    add_section_numbers,
    add_toc_numbers,
    clean_paragraph_newlines,
    fix_docutils_html5_heading_levels,
    generate_toc_from_headings,
)
from notemanager.renderer.preformatted import mark_diagram_code_blocks

_roles_registered = False


def _rst_diagnostics_enabled() -> bool:
    """环境变量 ``NOTEMANAGER_RST_DIAG=1`` 时，在 ``warnings`` 中附加 RST 标题诊断块。"""
    return os.environ.get("NOTEMANAGER_RST_DIAG", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _is_rst_separator_line(stripped: str) -> bool:
    """整行（已 strip）为同一非标点字符的重复，且长度 ≥2，视作候选装饰线。"""
    if len(stripped) < 2:
        return False
    c = stripped[0]
    if c.isalnum():
        return False
    return all(ch == c for ch in stripped)


def _collect_rst_separator_title_candidates(source: str) -> list[dict]:
    """按「装饰线行 + 上一行不长于装饰行的一行文字」启发式收集候选节标题（与 docutils 无关）。

    规则：若某行 strip 后为同一非标点字符填满，且上一行非空、本身不是装饰线、且
    ``len(上一行) <= len(装饰行)``（常见 RST：下划线至少与标题等长），则把上一行记为候选标题。
    """
    lines = source.splitlines()
    out: list[dict] = []
    for i, line in enumerate(lines):
        # "from line start": section adornments must not be indented.
        if line != line.lstrip():
            continue
        stripped = line.strip()
        if not _is_rst_separator_line(stripped):
            continue
        if i == 0:
            continue
        prev_raw = lines[i - 1]
        prev_stripped = prev_raw.strip()
        if not prev_stripped:
            continue
        if _is_rst_separator_line(prev_stripped):
            continue
        if len(prev_stripped) > len(stripped):
            continue
        out.append(
            {
                "line": i + 1,
                "title": prev_stripped,
                "underline_char": stripped[0],
                "underline_len": len(stripped),
            }
        )
    return out


def _rst_clean_title_text(text: str) -> str:
    """Clean RST title text (remove :index:`...` etc)."""
    t = (text or "").strip()
    # Prefer role-like titles: :index:`bash`
    m = re.search(r":[A-Za-z][\w-]*:`([^`]+)`", t)
    if not m:
        # Fallback: any inline code: `bash`
        m = re.search(r"`([^`]+)`", t)
    if m:
        t = m.group(1)

    # RST escaping: "\ " -> space
    t = re.sub(r"\\\s*", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _normalize_heading_text_for_match(text: str) -> str:
    """规范化标题文本用于源码行匹配：role 就地替换为内容、去行内标记与空白、小写。

    与 ``_rst_clean_title_text``（选文档标题用，会把整段替换为 role 内容）不同，
    这里保留标题全文，只把行内 role/标记替换成纯文本，以便源码行与渲染文本对齐。
    """
    t = (text or "").strip()
    t = re.sub(r":[A-Za-z][\w-]*:`([^`]+)`", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"[*_~]+", "", t)
    t = re.sub(r"\\\s*", " ", t)
    t = re.sub(r"\s+", "", t)
    return t.lower()


def _build_heading_line_map(source: str, headings: list[dict]) -> dict[str, int]:
    """按文档顺序把节标题 id 映射到源码行号（1-based，尽力而为）。

    依据 ``_collect_rst_separator_title_candidates`` 的启发式扫描（其 ``line`` 指向
    下划线行，标题在上一行），与 ``_extract_headings`` 输出的 (id, text) 按顺序做
    规范化文本匹配；匹配不到的标题不出现在结果里。
    """
    cands = _collect_rst_separator_title_candidates(source)
    out: dict[str, int] = {}
    idx = 0
    for h in headings:
        target = _normalize_heading_text_for_match(h.get("text", ""))
        if not target:
            continue
        for j in range(idx, len(cands)):
            cand = _normalize_heading_text_for_match(cands[j]["title"])
            hit = (
                cand == target
                or (len(target) >= 4 and cand.startswith(target))
                or (len(cand) >= 4 and target.startswith(cand))
            )
            if hit:
                out[h["id"]] = max(1, cands[j]["line"] - 1)
                idx = j + 1
                break
    return out


class _DocutilsRawH1OutlineParser(HTMLParser):
    """docutils html5 中节标题多为 ``<h1>``，用 ``<section>`` 嵌套深度表示层级。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[tuple[int, str]] = []
        self._section_depth = 0
        self._in_h1 = False
        self._h1_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "section":
            self._section_depth += 1
        elif tag == "h1":
            self._in_h1 = True
            self._h1_buf = []

    def handle_endtag(self, tag):
        if tag == "h1" and self._in_h1:
            text = "".join(self._h1_buf)
            text = re.sub(r"<[^>]+>", "", text).strip()
            if text:
                self.out.append((self._section_depth, text))
            self._in_h1 = False
        elif tag == "section":
            self._section_depth = max(0, self._section_depth - 1)

    def handle_data(self, data):
        if self._in_h1:
            self._h1_buf.append(data)


def _extract_docutils_raw_h1_outline(html: str) -> list[tuple[int, str]]:
    p = _DocutilsRawH1OutlineParser()
    try:
        p.feed(html)
        p.close()
    except Exception:
        return []
    return p.out


def _extract_html_heading_levels_in_order(html: str) -> list[tuple[int, str]]:
    """按 DOM 顺序列出 ``<h1>``…``<h6>`` 的级别与纯文本（用于对比 fix 后层级）。"""
    pat = re.compile(r"<(h[1-6])\b[^>]*>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
    out: list[tuple[int, str]] = []
    for m in pat.finditer(html):
        level = int(m.group(1)[1])
        text = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if text:
            out.append((level, text))
    return out


def _rst_diagnostic_source_block(source_path: str, processed: str) -> str:
    """调用 docutils 之前即可写入的源码侧候选节列表。"""
    cands = _collect_rst_separator_title_candidates(processed)
    src_lines = [
        f"[RST诊断] 源文件: {source_path}",
        "  (与送入 docutils 的文本一致：已做不支持的 role 剥离)",
        "  启发式候选节（装饰线行 + 上一行不长于装饰行且非装饰线）:",
    ]
    if not cands:
        src_lines.append("  (无)")
    else:
        for c in cands:
            src_lines.append(
                f"  L{c['line']}: 标题={c['title']!r}  下划线={c['underline_char']!r}×{c['underline_len']}"
            )
    return "\n".join(src_lines)


def _rst_diagnostic_html_blocks(
    html_before_heading_fix: str,
    html_after_heading_fix: str,
) -> list[str]:
    """docutils 输出并经层级修正后的 HTML 标题诊断（在 ``publish_parts`` 之后追加）。"""
    blocks: list[str] = []
    raw_outline = _extract_docutils_raw_h1_outline(html_before_heading_fix)
    raw_lines = [
        "[RST诊断] docutils 原始 HTML（节内标题均为 <h1>；depth = 进入该 <h1> 前未闭合的 <section> 层数）:",
    ]
    if not raw_outline:
        raw_lines.append("  (未解析到 <h1>，或 HTML 结构非预期)")
    else:
        for depth, text in raw_outline:
            raw_lines.append(f"  depth={depth}  {text!r}")
    blocks.append("\n".join(raw_lines))

    fixed = _extract_html_heading_levels_in_order(html_after_heading_fix)
    fix_lines = [
        "[RST诊断] 经 fix_docutils_html5_heading_levels 后的 <h1>…<h6>（DOM 顺序）:",
    ]
    if not fixed:
        fix_lines.append("  (无)")
    else:
        for lv, text in fixed:
            fix_lines.append(f"  h{lv}  {text!r}")
    blocks.append("\n".join(fix_lines))
    return blocks


def _rst_diagnostic_after_heading_repair(body: str) -> str:
    fixed = _extract_html_heading_levels_in_order(body)
    lines = [
        "[RST诊断] 经 _rst_repair_heading_levels_by_source_hierarchy 后的 <h1>…<h6>（DOM 顺序）:"
    ]
    if not fixed:
        lines.append("  (无)")
    else:
        for lv, text in fixed:
            lines.append(f"  h{lv}  {text!r}")
    return "\n".join(lines)


def _normalize_heading_text(text: str) -> str:
    """Normalize heading plain text for robust matching."""
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _rst_repair_heading_levels_by_source_hierarchy(
    source: str, html: str
) -> str:
    """Repair <h1>..<h6> levels based on RST adornment hierarchy.

    We first deduce the ordered set of adornment characters from the source
    (candidates where a separator line matches and its adjacent line is a title).
    Each distinct adornment char is mapped to an expected level: 1,2,3...

    Then we match each HTML heading (DOM order) to the next source candidate
    with the same text, and rewrite that heading's <hN> tag accordingly.

    This is done before TOC/section-numbering extraction so that TOC can be
    refreshed together with repaired levels.
    """
    candidates = _collect_rst_separator_title_candidates(source)
    if not candidates:
        return html

    distinct: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        ch = c.get("underline_char") or ""
        if ch and ch not in seen:
            distinct.append(ch)
            seen.add(ch)

    expected_level_by_char: dict[str, int] = {ch: i + 1 for i, ch in enumerate(distinct)}
    if not expected_level_by_char:
        return html

    heading_re = re.compile(
        r"<h([1-6])([^>]*)>(.*?)</h\1>",
        re.DOTALL | re.IGNORECASE,
    )

    def _extract_class(attrs: str) -> str:
        m = re.search(r"\bclass\s*=\s*([\"'])(.*?)\1", attrs, re.DOTALL | re.IGNORECASE)
        return (m.group(2) if m else "").strip()

    def _strip_tags(s: str) -> str:
        return re.sub(r"<[^>]+>", "", s)

    out: list[str] = []
    last = 0
    cand_i = 0
    for m in heading_re.finditer(html):
        out.append(html[last : m.start()])
        cur_lv = int(m.group(1))
        attrs = m.group(2)
        inner = m.group(3)

        cls = _extract_class(attrs)
        is_title = cls == "title" or "title" in cls.split()
        desired_lv: int | None = None

        if is_title:
            desired_lv = 1
        else:
            hv = _normalize_heading_text(_strip_tags(inner))
            # Find the next matching candidate in order.
            found = False
            for k in range(cand_i, len(candidates)):
                cand_text = _normalize_heading_text(candidates[k]["title"])
                if cand_text == hv:
                    underline_char = candidates[k]["underline_char"]
                    desired_lv = expected_level_by_char.get(underline_char)
                    cand_i = k + 1
                    found = True
                    break
            if not found:
                desired_lv = None

        if desired_lv is None:
            out.append(m.group(0))
        else:
            desired_lv = max(1, min(6, int(desired_lv)))
            if desired_lv == cur_lv:
                out.append(m.group(0))
            else:
                out.append(f"<h{desired_lv}{attrs}>{inner}</h{desired_lv}>")
        last = m.end()

    out.append(html[last:])
    return "".join(out)


def _ensure_roles_registered():
    """Register custom roles and directives once."""
    global _roles_registered
    if not _roles_registered:
        register_roles()
        register_directives()
        _roles_registered = True


def render_rst(content: str, source_path: str = "<string>") -> dict:
    """Render RST content to HTML.

    设置环境变量 ``NOTEMANAGER_RST_DIAG=1``（或 ``true``/``yes``/``on``）时，会在
    ``warnings`` 末尾附加多段 ``[RST诊断]`` 文本：源码启发式候选节、docutils 原始
    ``<h1>``+``<section>`` 深度、以及 ``fix_docutils_html5_heading_levels`` 之后的
    ``<h1>``…``<h6>`` 列表，便于与 docutils 行为对照。

    Args:
        content: The RST source text.
        source_path: Source file path for error reporting.

    Returns:
        Dict with keys: body (str), toc (str), warnings (list[str]).
    """
    _ensure_roles_registered()

    # Pre-process: strip unsupported roles to prevent error nodes
    processed, ignored_roles = _strip_unsupported_roles(content)

    warnings = []
    if ignored_roles:
        unique = sorted(set(r[0] for r in ignored_roles))
        if unique:
            details = "; ".join(f":{r[0]}:`{r[1]}`" for r in ignored_roles if r[1])
            msg = f"已忽略不支持的 role: {', '.join(unique)}"
            if details:
                msg += f" (如: {details})"
            warnings.append(msg)

    warning_stream = io.StringIO()

    if _rst_diagnostics_enabled():
        warnings.append(_rst_diagnostic_source_block(source_path, processed))

    settings = {
        "input_encoding": "utf-8",
        "output_encoding": "utf-8",
        # Collect docutils warnings/errors (unknown directives, malformed
        # markup, missing targets) into the diagnostics panel.  System-message
        # HTML is still stripped from the rendered body below.
        "report_level": 2,
        "halt_level": 6,
        "file_insertion_enabled": False,
        "raw_enabled": True,
        "stylesheet_path": None,
        "embed_stylesheet": False,
        "initial_header_level": 1,
        # Emit TeX wrappers (\(...\), \begin{equation*}...\end{equation*})
        # instead of HTML fallback glyph layout, so frontend KaTeX can typeset
        # consistent fractions/square roots across browsers.
        "math_output": "MathJax",
        "warning_stream": warning_stream,
    }

    # Some docutils math backends print parser diagnostics directly to stderr
    # (e.g. "* Unknown command \\iff"), bypassing warning_stream.
    # Capture them to keep CLI/export progress output clean.
    stderr_stream = io.StringIO()
    with contextlib.redirect_stderr(stderr_stream):
        parts = publish_parts(
            source=processed,
            source_path=source_path,
            writer_name="html5_polyglot",
            settings_overrides=settings,
        )

    body = parts.get("html_body", "")

    # Collect docutils warnings
    warning_text = warning_stream.getvalue().strip()
    if warning_text:
        for line in warning_text.splitlines():
            line = line.strip()
            if line:
                line = re.sub(r"^<string>:\d+:\s*", "", line)
                warnings.append(line)
    stderr_text = stderr_stream.getvalue().strip()
    if stderr_text:
        for line in stderr_text.splitlines():
            line = line.strip()
            if not line:
                continue
            # These lines are high-frequency parser noise in export logs; keep
            # other stderr diagnostics visible to users.
            if line.startswith("* Unknown command "):
                continue
            warnings.append(line)

    # Post-processing pipeline
    body = _strip_system_messages(body)
    body = _strip_problematic_spans(body)
    html_before_heading_fix = body
    # docutils html5_polyglot uses <h1> for every section; fix before TOC/numbering
    body = fix_docutils_html5_heading_levels(body)
    html_after_heading_fix = body
    body, docinfo = _extract_and_strip_docinfo(body)
    if docinfo:
        warnings.append(f"文档信息: {docinfo}")
    dtags = _extract_dtags(body)
    body = _strip_dtags(body)
    body = _normalize_literal_block_markers(body)
    body = mark_diagram_code_blocks(body)
    body = clean_paragraph_newlines(body)
    body = _inject_term_anchors(body)

    # Repair heading levels (and therefore TOC levels) based on source hierarchy
    # before we extract headings and generate TOC/section numbers.
    body = _rst_repair_heading_levels_by_source_hierarchy(processed, body)

    # (Optional) keep a debug snapshot of final heading levels.
    # Extract heading info for TOC (before numbering)
    headings = _extract_headings(body)

    # Add section numbers
    body, id_to_number = add_section_numbers(body)

    # Generate TOC
    toc_html = generate_toc_from_headings(headings)
    if toc_html and id_to_number:
        toc_html = add_toc_numbers(toc_html, id_to_number)

    if _rst_diagnostics_enabled():
        warnings.extend(
            _rst_diagnostic_html_blocks(html_before_heading_fix, html_after_heading_fix)
        )
        warnings.append(_rst_diagnostic_after_heading_repair(body))

    heading_lines = _build_heading_line_map(content, headings)

    return {"body": body, "toc": toc_html, "warnings": warnings, "dtags": dtags,
            "heading_lines": heading_lines}


# ---------------------------------------------------------------------------
# Heading extraction
# ---------------------------------------------------------------------------

def _extract_headings(html):
    """Extract heading info (level, id, text) from HTML.

    docutils puts the id on the wrapping <section> tag, not on the <hN> tag.
    We match <section id="X"> followed by <hN>text</hN> to extract headings.
    """
    headings = []
    # Match <section id="X">...<hN>text</hN> (non-greedy between section and heading)
    for m in re.finditer(
        r'<section[^>]*\bid="([^"]*)"[^>]*>.*?<(h[1-6])\b[^>]*>(.*?)</\2>',
        html,
        re.DOTALL,
    ):
        hid, tag, text = m.group(1), m.group(2), m.group(3)
        text = re.sub(r"<[^>]+>", "", text).strip()
        if text:
            headings.append({"level": int(tag[1]), "id": hid, "text": text})
    return headings


# ---------------------------------------------------------------------------
# Pre-processing
# ---------------------------------------------------------------------------

def _strip_unsupported_roles(text):
    """Remove unsupported RST roles to prevent error nodes in output."""
    from docutils.parsers.rst import roles as _roles_mod

    known_roles = set()
    known_roles.update(name.lower() for name in getattr(_roles_mod, "_roles", {}).keys())
    for name, handler in getattr(_roles_mod, "_role_registry", {}).items():
        if getattr(handler, "__name__", "") == "unimplemented_role":
            continue
        known_roles.add(name.lower())

    if not known_roles:
        return text, []

    passthrough_roles = set()
    ignored = []

    def _repl(match):
        role_name = match.group(1)
        role_content = match.group(2)
        if role_name.lower() in passthrough_roles:
            return role_content
        if role_name.lower() in known_roles:
            return match.group(0)
        ignored.append((role_name, role_content))
        return ""

    sanitized = re.sub(r":([a-zA-Z][\w-]*):`([^`\n]*)`", _repl, text)
    return sanitized, ignored


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------

def _strip_system_messages(html):
    """Remove docutils system-message divs from HTML output."""
    html = re.sub(
        r'<div class="system-message[^"]*">.*?</div>',
        "",
        html,
        flags=re.DOTALL,
    )
    return html.strip()


def _strip_problematic_spans(html):
    """Remove docutils problematic spans (error markers)."""
    return re.sub(
        r'<span class="problematic"[^>]*>.*?</span>',
        "",
        html,
        flags=re.DOTALL,
    )


def _extract_and_strip_docinfo(html):
    """Extract metadata from docinfo and strip it from body.

    Returns (html, docinfo_text) where docinfo_text is a human-readable
    summary like "Author: X, Version: Y".
    """
    docinfo_text = ""
    m = re.search(r'<dl class="docinfo[^"]*"[^>]*>(.*?)</dl>', html, re.DOTALL)
    if m:
        # Extract field name-value pairs
        fields = re.findall(
            r'<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>',
            m.group(1),
            re.DOTALL,
        )
        parts = []
        for name, value in fields:
            name = re.sub(r"<[^>]+>", "", name).strip().rstrip(":")
            value = re.sub(r"<[^>]+>", "", value).strip()
            parts.append(f"{name}: {value}")
        docinfo_text = ", ".join(parts)
        html = html[:m.start()] + html[m.end():]
    return html, docinfo_text


def _extract_dtags(html):
    """Extract dtag text from HTML before stripping."""
    tags = []
    for m in re.finditer(r'<span class="dtag"[^>]*>([^<]+)</span>', html):
        for tag in m.group(1).split(","):
            tag = tag.strip()
            if tag and tag not in tags:
                tags.append(tag)
    return tags


def _strip_dtags(html):
    """Remove dtag spans from body — they are for index only."""
    # Remove <span class="dtag">...</span> and clean up empty <p> wrappers
    html = re.sub(r'<span class="dtag"[^>]*>.*?</span>', "", html, flags=re.DOTALL)
    # Clean up empty paragraphs left behind
    html = re.sub(r'<p>\s*</p>', "", html)
    return html


def _normalize_literal_block_markers(html):
    """Remove visible colons before literal blocks."""
    pattern = re.compile(
        r'(<p\b[^>]*>.*?)(\s*:+)(</p>\s*<pre class="literal-block")',
        re.DOTALL,
    )
    return pattern.sub(r"\1\3", html)


def _inject_term_anchors(html: str) -> str:
    """Inject stable ids for index-term / dtag spans to improve result jump precision."""
    idx = 0
    dtag = 0

    def repl_index(m):
        nonlocal idx
        attrs = m.group(1) or ""
        if re.search(r'\bid="[^"]+"', attrs):
            return m.group(0)
        idx += 1
        return f'<span class="index-term" id="nm-index-{idx}"{attrs}>{m.group(2)}</span>'

    def repl_dtag(m):
        nonlocal dtag
        attrs = m.group(1) or ""
        if re.search(r'\bid="[^"]+"', attrs):
            return m.group(0)
        dtag += 1
        return f'<span class="dtag" id="nm-dtag-{dtag}"{attrs}>{m.group(2)}</span>'

    html = re.sub(r'<span class="index-term"([^>]*)>(.*?)</span>', repl_index, html, flags=re.DOTALL)
    html = re.sub(r'<span class="dtag"([^>]*)>(.*?)</span>', repl_dtag, html, flags=re.DOTALL)
    return html
