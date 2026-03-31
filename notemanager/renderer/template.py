"""HTML template wrapper for rendered content."""

import re
from pathlib import Path

try:
    from pygments.formatters import HtmlFormatter
    _PYGMENTS_CSS = HtmlFormatter(style="friendly").get_style_defs(".highlight")
    # Also generate for docutils class scheme: pre.code
    _PYGMENTS_CSS += "\n" + HtmlFormatter(style="friendly").get_style_defs("pre.code")
except ImportError:
    _PYGMENTS_CSS = ""

try:
    from pygments import highlight as _pyg_highlight
    from pygments.lexers import RstLexer, MarkdownLexer
    from pygments.formatters import HtmlFormatter as _HtmlFormatter
    _SOURCE_FMT = _HtmlFormatter(style="monokai", nowrap=True, noclasses=True)
    _RST_LEXER = RstLexer()
    _MD_LEXER = MarkdownLexer()
except ImportError:
    _pyg_highlight = None

# Compute relative path from an HTML file to the _static directory
# _static/ is at .NoteManager/html/_static/, html files are at .NoteManager/html/...
# So from html/a/b/c.html, _static is at ../../_static/ (depth dependent)


def _highlight_source(source: str, source_path: str) -> str:
    """Highlight source text using Pygments server-side."""
    if _pyg_highlight is None:
        return _escape(source)
    if source_path.endswith(".md"):
        lexer = _MD_LEXER
    elif source_path.endswith(".rst"):
        lexer = _RST_LEXER
    else:
        return _escape(source)
    return _pyg_highlight(source, lexer, _SOURCE_FMT)


def _static_prefix(html_rel_path: str) -> str:
    """Compute relative path from an HTML file to the static/ directory."""
    depth = Path(html_rel_path).parent
    if str(depth) == ".":
        return ""
    parts = depth.parts
    return "/".join([".."] * len(parts)) + "/"


def wrap_html(body: str, title: str, html_rel_path: str,
              toc: str = "", source: str = "", warnings: list | None = None,
              source_path: str = "") -> str:
    """Wrap an HTML body fragment in a full HTML5 page.

    Args:
        body: Rendered HTML body content.
        title: Page title.
        html_rel_path: Relative path of this HTML file (for computing static paths).
        toc: Optional TOC HTML fragment.
        source: Optional raw source text for source view.
        warnings: Optional list of warning strings for diagnostics view.
        source_path: Optional original source file path for display.

    Returns:
        Complete HTML document string.
    """
    prefix = _static_prefix(html_rel_path)
    warnings = warnings or []

    # Highlight source server-side with Pygments
    source_path_display = _escape(source_path) if source_path else ""
    highlighted_source = _highlight_source(source, source_path) if source else ""
    # Escape curly braces for f-string embedding
    highlighted_source = highlighted_source.replace("{", "{{").replace("}", "}}")
    warnings_text = "\\n".join(warnings) if warnings else "无诊断信息"

    return f"""\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_escape(title)}</title>
<link rel="stylesheet" href="{prefix}_static/notemanager.css">
<link rel="stylesheet" href="{prefix}_static/katex.min.css">
<style>
{_PYGMENTS_CSS}
.nm-toolbar {{ position: fixed; top: 10px; right: 10px; z-index: 100; display: flex; gap: 8px; }}
.nm-toolbar button {{ padding: 6px 12px; border: 1px solid #ddd; background: white; border-radius: 4px;
                      cursor: pointer; font-size: 14px; }}
.nm-toolbar button:hover {{ background: #f0f0f0; }}
.nm-toolbar button.active {{ background: #0066cc; color: white; border-color: #0066cc; }}
.nm-toc-panel {{ position: fixed; top: 50px; right: 10px; width: 280px; max-height: calc(100vh - 60px);
                 overflow-y: auto; background: white; border: 1px solid #ddd; border-radius: 5px;
                 padding: 15px; display: none; z-index: 99; box-shadow: 0 2px 8px rgba(0,0,0,0.15); }}
.nm-toc-panel.show {{ display: block; }}
.nm-toc-panel h4 {{ margin: 0 0 10px 0; font-size: 14px; color: #666; border-bottom: 1px solid #eee; padding-bottom: 5px; }}
.toc {{ margin: 0; }}
.toc ul {{ list-style: none; padding-left: 0; margin: 0; }}
.toc ul ul {{ padding-left: 15px; margin-top: 2px; }}
.toc li {{ margin: 3px 0; }}
.toc a {{ color: #0066cc; text-decoration: none; font-size: 13px; }}
.toc a:hover {{ text-decoration: underline; }}
.section-number {{ color: #666; margin-right: 0.5em; }}
#nm-source-view {{ display: none; white-space: pre-wrap; font-family: 'SFMono-Regular', Consolas, monospace;
                   font-size: 13px; line-height: 1.5; background: #282c34; color: #abb2bf; padding: 20px;
                   border-radius: 5px; overflow-x: auto; position: fixed; top: 50px; right: 10px;
                   width: calc(100% - 30px); max-width: 900px; max-height: calc(100vh - 60px);
                   z-index: 99; box-shadow: 0 2px 8px rgba(0,0,0,0.15); user-select: text; }}
#nm-source-view.show {{ display: block; }}
#nm-source-view pre, #nm-source-view code {{ background: none; color: #f8f8f2; padding: 0; border-radius: 0; }}
.nm-source-header {{ display: flex; justify-content: space-between; align-items: center;
                          margin-bottom: 10px; padding-bottom: 8px; border-bottom: 1px solid #555; }}
.nm-source-path {{ color: #98c379; font-size: 12px; font-family: 'SFMono-Regular', Consolas, monospace; }}
.nm-source-close {{ background: none; border: 1px solid #666; color: #abb2bf; padding: 2px 10px;
                        border-radius: 3px; cursor: pointer; font-size: 12px; }}
.nm-source-close:hover {{ border-color: #abb2bf; color: #e5c07b; }}
#nm-warnings-view {{ display: none; white-space: pre-wrap; font-family: 'SFMono-Regular', Consolas, monospace;
                     font-size: 13px; line-height: 1.5; background: #fffaf0; color: #5f4b32; padding: 20px;
                     border: 1px solid #ecd9b0; border-radius: 5px; overflow-x: auto; position: fixed;
                     top: 50px; right: 10px; width: calc(100% - 30px); max-width: 900px;
                     max-height: calc(100vh - 60px); z-index: 99; box-shadow: 0 2px 8px rgba(0,0,0,0.15); }}
#nm-warnings-view.show {{ display: block; }}
:target {{ background-color: #fff8dc; padding-top: 20px; margin-top: -20px; }}
</style>
</head>
<body>
<div class="nm-toolbar">
<button onclick="nmToggleToc()">目录</button>
<button onclick="nmToggleSource()">源文件</button>
<button onclick="nmToggleWarnings()">诊断</button>
</div>
<div id="nm-toc-panel" class="nm-toc-panel"><h4>目录</h4>{toc}</div>
<div class="nm-nav">
<a href="{prefix}index.html" class="nm-home">Home</a>
<input type="text" id="nm-search" placeholder="搜索...">
</div>
<div id="nm-search-results"></div>
<div class="nm-content">
{body}
</div>
<div id="nm-source-view"><div class="nm-source-header"><span class="nm-source-path">{source_path_display}</span><button class="nm-source-close" onclick="nmCloseAllPanels()">关闭</button></div><pre><code>{highlighted_source}</code></pre></div>
<pre id="nm-warnings-view">{_escape(warnings_text)}</pre>
<script>window.NM_PREFIX="{prefix}";</script>
<script src="{prefix}_static/notemanager.js"></script>
<script src="{prefix}_static/katex.min.js"></script>
<script src="{prefix}_static/katex-auto-render.min.js"></script>
<script src="{prefix}_static/mermaid.min.js"></script>
<script>if(typeof mermaid!=='undefined')mermaid.initialize({{startOnLoad:true,theme:'default'}});</script>
<script>
function nmCloseAllPanels() {{
    ['nm-toc-panel','nm-source-view','nm-warnings-view'].forEach(function(id){{document.getElementById(id).classList.remove('show');}});
    document.querySelectorAll('.nm-toolbar button').forEach(function(b){{b.classList.remove('active');}});
}}
function nmTogglePanel(id,btn) {{
    var el=document.getElementById(id);
    var wasOpen=el.classList.contains('show');
    nmCloseAllPanels();
    if(!wasOpen) {{
        el.classList.add('show');
        btn.classList.add('active');
    }}
}}
function nmToggleToc() {{ nmTogglePanel('nm-toc-panel',event.target); }}
function nmToggleSource() {{ nmTogglePanel('nm-source-view',event.target); }}
function nmToggleWarnings() {{ nmTogglePanel('nm-warnings-view',event.target); }}
document.querySelectorAll('.nm-toc-panel a').forEach(function(a){{a.addEventListener('click',function(e){{e.preventDefault();var t=document.getElementById(this.getAttribute('href').substring(1));if(t)t.scrollIntoView({{behavior:'smooth',block:'start'}});nmCloseAllPanels();}});}});
document.addEventListener('click',function(e){{if(!e.target.closest('.nm-toc-panel')&&!e.target.closest('.nm-toolbar')){{var tp=document.getElementById('nm-toc-panel');if(tp&&tp.classList.contains('show'))nmCloseAllPanels();}}}});
if(typeof renderMathInElement!=='undefined')renderMathInElement(document.querySelector('.nm-content'),{{delimiters:[{{left:'$$',right:'$$',display:true}},{{left:'$',right:'$',display:false}},{{left:'\\\\[',right:'\\\\]',display:true}},{{left:'\\\\(',right:'\\\\)',display:false}}],throwOnError:false,strict:'ignore'}});
</script>
</body>
</html>
"""


def _escape(text: str) -> str:
    """Escape HTML special characters."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

