"""HTML template wrapper for rendered content."""

import re
from pathlib import Path

try:
    from pygments.formatters import HtmlFormatter
    _PYGMENTS_CSS = HtmlFormatter(style="friendly").get_style_defs(".highlight")
except ImportError:
    _PYGMENTS_CSS = ""

# Compute relative path from an HTML file to the static directory
# static/ is at .NoteManager/static/, html files are at .NoteManager/html/...
# So from html/a/b/c.html, static is at ../../static/ (depth dependent)


def _static_prefix(html_rel_path: str) -> str:
    """Compute relative path from an HTML file to the static/ directory."""
    depth = Path(html_rel_path).parent
    if str(depth) == ".":
        return ""
    parts = depth.parts
    return "/".join([".."] * len(parts)) + "/"


def wrap_html(body: str, title: str, html_rel_path: str) -> str:
    """Wrap an HTML body fragment in a full HTML5 page.

    Args:
        body: Rendered HTML body content.
        title: Page title.
        html_rel_path: Relative path of this HTML file (for computing static paths).

    Returns:
        Complete HTML document string.
    """
    prefix = _static_prefix(html_rel_path)

    return f"""\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_escape(title)}</title>
<link rel="stylesheet" href="{prefix}../static/notemanager.css">
<style>
{_PYGMENTS_CSS}
</style>
</head>
<body>
<div class="nm-nav">
<a href="{prefix}index.html" class="nm-home">Home</a>
<input type="text" id="nm-search" placeholder="搜索...">
</div>
<div id="nm-search-results"></div>
<div class="nm-content">
{body}
</div>
<script src="{prefix}../static/notemanager.js"></script>
</body>
</html>
"""


def _escape(text: str) -> str:
    """Escape HTML special characters."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
