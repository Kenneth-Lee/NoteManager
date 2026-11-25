"""Plain text to simple HTML renderer.

TXT is intentionally close to the source: everything except NoteManager
``nMr:`` comment lines is preserved literally (whitespace and line breaks
included) inside a ``<pre>`` block.
"""

import html
import re

from notemanager.renderer.frontmatter import extract_frontmatter

_COMMENT_PREFIX = "nMr:"
_COMMENT_TAG_RE = re.compile(r"#([^#\n]+)#")


def render_txt(content: str, source_path: str = "<string>") -> dict:
    """Render plain text while stripping only ``nMr:`` comment lines.

    ``nMr:`` comments are case-sensitive and must start at column 0.  Tags in
    comments use ``#tag#``; comma-separated values inside each tag group are
    accepted, matching reST ``:dtag:`` semantics.
    """
    _ = source_path
    body, fm_tags, _metadata, warnings = extract_frontmatter(content)
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    kept: list[str] = []
    dtags: list[str] = list(fm_tags)

    for lineno, line in enumerate(text.splitlines(keepends=True), 1):
        if line.startswith(_COMMENT_PREFIX):
            comment = line[len(_COMMENT_PREFIX):].strip()
            if comment:
                warnings.append(f"TXT 注释 (第 {lineno} 行): {comment}")
            for match in _COMMENT_TAG_RE.finditer(comment):
                for tag in match.group(1).split(","):
                    tag = tag.strip()
                    if tag and tag not in dtags:
                        dtags.append(tag)
            continue
        kept.append(line)

    raw = "".join(kept)
    body = "<pre class=\"nm-plaintext\">" + html.escape(raw) + "</pre>" if raw else "<pre class=\"nm-plaintext\"></pre>"
    return {"body": body, "toc": "", "warnings": warnings, "dtags": dtags}
