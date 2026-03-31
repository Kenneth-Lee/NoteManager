"""RST to HTML renderer using docutils."""

import re
import io

from docutils.core import publish_parts

from notemanager.renderer.roles import register_roles
from notemanager.renderer.directives import register_directives
from notemanager.renderer.postproc import (
    add_section_numbers,
    add_toc_numbers,
    generate_toc_from_headings,
)

_roles_registered = False


def _ensure_roles_registered():
    """Register custom roles and directives once."""
    global _roles_registered
    if not _roles_registered:
        register_roles()
        register_directives()
        _roles_registered = True


def render_rst(content: str, source_path: str = "<string>") -> dict:
    """Render RST content to HTML.

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
        unique = sorted(set(ignored_roles))
        warnings.append(f"已忽略不支持的 role: {', '.join(unique)}")

    warning_stream = io.StringIO()

    settings = {
        "input_encoding": "utf-8",
        "output_encoding": "utf-8",
        "report_level": 5,
        "halt_level": 6,
        "file_insertion_enabled": False,
        "raw_enabled": True,
        "stylesheet_path": None,
        "embed_stylesheet": False,
        "initial_header_level": 1,
        "warning_stream": warning_stream,
    }

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

    # Post-processing pipeline
    body = _strip_system_messages(body)
    body = _strip_problematic_spans(body)
    body, docinfo = _extract_and_strip_docinfo(body)
    if docinfo:
        warnings.append(f"文档信息: {docinfo}")
    dtags = _extract_dtags(body)
    body = _strip_dtags(body)
    body = _normalize_literal_block_markers(body)
    body = _clean_paragraph_linebreaks(body)

    # Extract heading info for TOC (before numbering)
    headings = _extract_headings(body)

    # Add section numbers
    body, id_to_number = add_section_numbers(body)

    # Generate TOC
    toc_html = generate_toc_from_headings(headings)
    if toc_html and id_to_number:
        toc_html = add_toc_numbers(toc_html, id_to_number)

    return {"body": body, "toc": toc_html, "warnings": warnings, "dtags": dtags}


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

    passthrough_roles = {"index"}
    ignored = []

    def _repl(match):
        role_name = match.group(1)
        role_content = match.group(2)
        if role_name.lower() in passthrough_roles:
            return role_content
        if role_name.lower() in known_roles:
            return match.group(0)
        ignored.append(role_name)
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


def _clean_paragraph_linebreaks(html):
    """Clean unnecessary line breaks inside <p> tags."""
    from html.parser import HTMLParser

    class _Cleaner(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
            self._in_p = False
            self._p_content = []

        def handle_starttag(self, tag, attrs):
            if tag == "p":
                self._in_p = True
                self._p_content = []
                self.parts.append(_fmt(tag, attrs))
            else:
                s = _fmt(tag, attrs)
                if self._in_p:
                    self._p_content.append(s)
                else:
                    self.parts.append(s)

        def handle_endtag(self, tag):
            if tag == "p" and self._in_p:
                content = "".join(self._p_content)
                content = content.replace("\n", " ")
                content = re.sub(r"  +", " ", content)
                self.parts.append(content)
                self.parts.append("</p>")
                self._in_p = False
                self._p_content = []
            else:
                s = f"</{tag}>"
                if self._in_p:
                    self._p_content.append(s)
                else:
                    self.parts.append(s)

        def handle_data(self, data):
            if self._in_p:
                self._p_content.append(data)
            else:
                self.parts.append(data)

        def result(self):
            return "".join(self.parts)

    def _fmt(tag, attrs):
        if attrs:
            a = " ".join(f'{k}="{v}"' for k, v in attrs)
            return f"<{tag} {a}>"
        return f"<{tag}>"

    parser = _Cleaner()
    try:
        parser.feed(html)
    except Exception:
        return html
    return parser.result()
