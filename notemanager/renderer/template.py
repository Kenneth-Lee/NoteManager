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
              source_path: str = "", dtags: list | None = None) -> str:
    """Wrap an HTML body fragment in a full HTML5 page.

    Args:
        body: Rendered HTML body content.
        title: Page title.
        html_rel_path: Relative path of this HTML file (for computing static paths).
        toc: Optional TOC HTML fragment.
        source: Optional raw source text for source view.
        warnings: Optional list of warning strings for diagnostics view.
        source_path: Optional original source file path for display.
        dtags: Optional list of dtag strings for this document.

    Returns:
        Complete HTML document string.
    """
    prefix = _static_prefix(html_rel_path)
    warnings = warnings or []
    dtags = dtags or []

    # Highlight source server-side with Pygments
    source_path_display = _escape(source_path) if source_path else ""
    highlighted_source = _highlight_source(source, source_path) if source else ""
    # Escape curly braces for f-string embedding
    highlighted_source = highlighted_source.replace("{", "{{").replace("}", "}}")
    warnings_text = "\\n".join(warnings) if warnings else "无诊断信息"

    # Build dtag badge HTML
    dtag_badges = ""
    if dtags:
        parts = ['<div class="nm-sidebar-dtags">']
        parts.append('<div class="nm-sidebar-section-title">标签</div>')
        for tag in dtags:
            tag_escaped = _escape(tag)
            parts.append(f'<span class="nm-dtag-badge" data-dtag="{tag_escaped}">{tag_escaped}</span>')
        parts.append('</div>')
        dtag_badges = "\n".join(parts)

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
/* Sidebar */
.nm-sidebar {{ position: fixed; top: 0; left: 0; width: 280px; height: 100vh;
               background: #f8f9fa; border-right: 1px solid #e0e0e0; overflow-y: auto;
               z-index: 50; display: flex; flex-direction: column; }}
.nm-sidebar-home {{ padding: 12px 16px; border-bottom: 1px solid #e0e0e0; font-size: 15px; font-weight: 600; }}
.nm-sidebar-home a {{ text-decoration: none; color: #333; }}
.nm-sidebar-home a:hover {{ color: #0066cc; }}
.nm-sidebar-search {{ padding: 8px 12px; border-bottom: 1px solid #e0e0e0; position: relative; }}
.nm-sidebar-search input {{ width: calc(100% - 30px); padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px;
                            font-size: 13px; box-sizing: border-box; }}
.nm-sidebar-search input:focus {{ outline: none; border-color: #0066cc; }}
.nm-search-help {{ position: absolute; right: 16px; top: 50%; transform: translateY(-50%);
                    background: none; border: none; color: #999; cursor: pointer; font-size: 15px;
                    padding: 0 4px; line-height: 1; }}
.nm-search-help:hover {{ color: #0066cc; }}
.nm-search-help-popup {{ display: none; position: absolute; top: 100%; left: 12px; right: 12px;
    background: white; border: 1px solid #ddd; border-radius: 6px; padding: 12px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.15); z-index: 200; font-size: 12px; color: #333; line-height: 1.6; }}
.nm-search-help-popup.show {{ display: block; }}
.nm-search-help-popup h4 {{ margin: 0 0 6px 0; font-size: 13px; color: #333; }}
.nm-search-help-popup code {{ background: #f0f0f0; padding: 1px 4px; border-radius: 2px; font-size: 11px; }}
.nm-search-help-popup ul {{ margin: 4px 0; padding-left: 16px; }}
.nm-search-help-popup li {{ margin: 2px 0; }}
.nm-sidebar-toc {{ flex: 1; overflow-y: auto; padding: 8px 12px; }}
.nm-sidebar-search-results {{ display: none; flex: 1; overflow-y: auto; padding: 0; }}
.nm-sidebar-search-results .nm-result-item {{ display: block; padding: 8px 12px; text-decoration: none;
    color: #333; border-bottom: 1px solid #eee; font-size: 13px; }}
.nm-sidebar-search-results .nm-result-item:hover {{ background: #e8f0fe; }}
.nm-sidebar-search-results .nm-result-title {{ font-weight: 500; color: #0066cc; }}
.nm-sidebar-search-results .nm-result-path {{ font-size: 11px; color: #888; font-family: monospace; }}
.nm-sidebar-search-results .nm-result-snippet {{ font-size: 12px; color: #666; margin-top: 2px; }}
.nm-sidebar-search-results .nm-result-snippet mark {{ background: #ffe066; padding: 0 2px; border-radius: 2px; }}
.nm-sidebar-search-results .nm-no-results {{ padding: 20px; text-align: center; color: #999; font-size: 13px; }}
.nm-sidebar-search-results .nm-result-count {{ padding: 6px 12px; font-size: 12px; color: #888;
    border-bottom: 1px solid #eee; background: #f0f0f0; }}
.nm-sidebar-dtags {{ padding: 8px 12px; border-top: 1px solid #e0e0e0; }}
.nm-sidebar-section-title {{ font-size: 12px; color: #888; font-weight: 600; margin-bottom: 6px;
                              text-transform: uppercase; letter-spacing: 0.5px; }}
.nm-dtag-badge {{ display: inline-block; font-size: 12px; padding: 2px 8px; margin: 2px;
                   background: #e3f2fd; color: #1565c0; border-radius: 12px; cursor: pointer;
                   border: 1px solid #bbdefb; }}
.nm-dtag-badge:hover {{ background: #bbdefb; }}
/* TOC styles */
.toc {{ margin: 0; }}
.toc ul {{ list-style: none; padding-left: 0; margin: 0; }}
.toc ul ul {{ padding-left: 15px; margin-top: 2px; }}
.toc li {{ margin: 3px 0; }}
.toc a {{ color: #0066cc; text-decoration: none; font-size: 13px; }}
.toc a:hover {{ text-decoration: underline; }}
.section-number {{ color: #666; margin-right: 0.5em; }}
/* Main content area with tabs */
.nm-main {{ margin-left: 280px; min-height: 100vh; display: flex; flex-direction: column; }}
.nm-tabs {{ display: flex; border-bottom: 1px solid #e0e0e0; background: #fafafa; padding: 0 20px; }}
.nm-tab {{ padding: 8px 16px; cursor: pointer; border: 1px solid transparent; border-bottom: none;
           background: none; font-size: 13px; color: #666; border-radius: 4px 4px 0 0; margin-top: 4px; }}
.nm-tab:hover {{ color: #333; background: #f0f0f0; }}
.nm-tab.active {{ background: white; color: #333; border-color: #e0e0e0; font-weight: 500; }}
.nm-tab-panels {{ flex: 1; position: relative; }}
.nm-tab-panel {{ display: none; padding: 20px; overflow-y: auto; }}
.nm-tab-panel.active {{ display: block; }}
/* Source view */
.nm-source-panel {{ background: #282c34; color: #f8f8f2; }}
.nm-source-panel pre, .nm-source-panel code {{ background: none; color: #f8f8f2; padding: 0; border-radius: 0;
    white-space: pre-wrap; font-size: 13px; line-height: 1.5; }}
.nm-source-path {{ color: #98c379; font-size: 12px; font-family: 'SFMono-Regular', Consolas, monospace;
                   margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid #555; }}
/* Warnings panel */
.nm-warnings-panel {{ white-space: pre-wrap; font-family: 'SFMono-Regular', Consolas, monospace;
                      font-size: 13px; line-height: 1.5; background: #fffaf0; color: #5f4b32; }}
:target {{ background-color: #fff8dc; padding-top: 20px; margin-top: -20px; }}
/* Type badges */
.nm-type-badge {{ display: inline-block; font-size: 10px; padding: 1px 5px; border-radius: 3px;
                   margin-right: 4px; font-weight: 600; vertical-align: middle; }}
.nm-type-chapter {{ background: #e8f5e9; color: #2e7d32; }}
.nm-type-label {{ background: #e3f2fd; color: #1565c0; }}
.nm-type-index {{ background: #fff3e0; color: #e65100; }}
.nm-type-dtag {{ background: #f3e5f5; color: #7b1fa2; }}
/* Directory tree */
.nm-tree {{ list-style: none; padding-left: 0; }}
.nm-tree .nm-tree {{ padding-left: 20px; }}
.nm-tree li {{ margin: 2px 0; }}
.nm-dir > details > summary {{ cursor: pointer; font-weight: 500; padding: 2px 0; list-style: none; }}
.nm-dir > details > summary::before {{ content: "\\1F4C1 "; }}
.nm-dir > details[open] > summary::before {{ content: "\\1F4C2 "; }}
.nm-file a {{ text-decoration: none; color: #0066cc; }}
.nm-file a:hover {{ text-decoration: underline; }}
.nm-file::before {{ content: "\\1F4C4 "; }}
</style>
</head>
<body>
<div class="nm-sidebar">
<div class="nm-sidebar-home"><a href="{prefix}index.html">Home</a></div>
<div class="nm-sidebar-search">
<input type="text" id="nm-search" placeholder="搜索...">
<button class="nm-search-help" onclick="nmToggleHelp()" title="搜索语法帮助">?</button>
<div id="nm-search-help" class="nm-search-help-popup">
<h4>搜索语法</h4>
<ul>
<li><code>关键词</code> — 搜索所有类型</li>
<li><code>chapter:关键词</code> — 仅搜索章节标题</li>
<li><code>label:关键词</code> — 仅搜索锚点标签</li>
<li><code>index:关键词</code> — 仅搜索索引条目</li>
<li><code>dtag:关键词</code> — 仅搜索文档标签</li>
</ul>
<div style="margin-top:6px;color:#888;">支持正则表达式</div>
</div>
</div>
<div id="nm-sidebar-toc" class="nm-sidebar-toc">{toc}</div>
<div id="nm-sidebar-search-results" class="nm-sidebar-search-results"></div>
{dtag_badges}
</div>
<div class="nm-main">
<div class="nm-tabs">
<button class="nm-tab active" data-tab="render">渲染</button>
<button class="nm-tab" data-tab="source">源文件</button>
<button class="nm-tab" data-tab="warnings">诊断</button>
</div>
<div class="nm-tab-panels">
<div id="nm-panel-render" class="nm-tab-panel active">
{body}
</div>
<div id="nm-panel-source" class="nm-tab-panel nm-source-panel">
<div class="nm-source-path">{source_path_display}</div>
<pre><code>{highlighted_source}</code></pre>
</div>
<div id="nm-panel-warnings" class="nm-tab-panel nm-warnings-panel">
{_escape(warnings_text)}
</div>
</div>
</div>
<script>window.NM_PREFIX="{prefix}";</script>
<script src="{prefix}_static/notemanager.js"></script>
<script src="{prefix}_static/katex.min.js"></script>
<script src="{prefix}_static/katex-auto-render.min.js"></script>
<script src="{prefix}_static/mermaid.min.js"></script>
<script>if(typeof mermaid!=='undefined')mermaid.initialize({{startOnLoad:true,theme:'default'}});</script>
<script>
/* Tab switching */
document.querySelectorAll('.nm-tab').forEach(function(tab){{
    tab.addEventListener('click',function(){{
        var name=this.getAttribute('data-tab');
        document.querySelectorAll('.nm-tab').forEach(function(t){{t.classList.remove('active');}});
        document.querySelectorAll('.nm-tab-panel').forEach(function(p){{p.classList.remove('active');}});
        this.classList.add('active');
        document.getElementById('nm-panel-'+name).classList.add('active');
    }});
}});
/* Search help toggle */
function nmToggleHelp(){{
    var p=document.getElementById('nm-search-help');
    p.classList.toggle('show');
}}
document.addEventListener('click',function(e){{
    if(!e.target.closest('.nm-sidebar-search')){{
        var p=document.getElementById('nm-search-help');
        if(p)p.classList.remove('show');
    }}
}});
/* TOC link smooth scroll */
document.querySelectorAll('.nm-sidebar-toc a').forEach(function(a){{
    a.addEventListener('click',function(e){{
        var href=this.getAttribute('href');
        if(href.charAt(0)==='#'){{
            e.preventDefault();
            var t=document.getElementById(href.substring(1));
            if(t)t.scrollIntoView({{behavior:'smooth',block:'start'}});
        }}
    }});
}});
/* Math rendering */
if(typeof renderMathInElement!=='undefined')renderMathInElement(document.getElementById('nm-panel-render'),{{delimiters:[{{left:'$$',right:'$$',display:true}},{{left:'$',right:'$',display:false}},{{left:'\\\\[',right:'\\\\]',display:true}},{{left:'\\\\(',right:'\\\\)',display:false}}],throwOnError:false,strict:'ignore'}});
</script>
</body>
</html>
"""


def _escape(text: str) -> str:
    """Escape HTML special characters."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

