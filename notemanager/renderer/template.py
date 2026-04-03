"""HTML template wrapper for rendered content."""

import json
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
              source_path: str = "", dtags: list | None = None,
              *, source_editable: bool = False) -> str:
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
        source_editable: When True, source tab uses CodeMirror + save API (managed projects only).

    Returns:
        Complete HTML document string.
    """
    prefix = _static_prefix(html_rel_path)
    warnings = warnings or []
    dtags = dtags or []

    source_path_display = _escape(source_path) if source_path else ""

    if source_editable and source_path:
        highlighted_source = ""
        cm_head = (
            f'<link rel="stylesheet" href="{prefix}_static/codemirror/lib/codemirror.css">\n'
            f'<link rel="stylesheet" href="{prefix}_static/codemirror/theme/dracula.css">\n'
            f'<link rel="stylesheet" href="{prefix}_static/codemirror/addon/dialog/dialog.css">'
        )
        sjson = json.dumps(source, ensure_ascii=False)
        meta = json.dumps(
            {
                "editable": True,
                "source_rel": source_path.replace("\\", "/"),
                "source_ext": Path(source_path).suffix.lower(),
            },
            ensure_ascii=False,
        )
        source_panel_inner = (
            '<div class="nm-source-toolbar">'
            '<button type="button" class="nm-source-save" id="nm-source-save">保存并刷新渲染</button>'
            '<button type="button" class="nm-source-secondary" id="nm-source-new" title="在与当前笔记相同的目录下新建文件">新建笔记</button>'
            '<button type="button" class="nm-source-secondary nm-source-delete" id="nm-source-delete" title="删除磁盘上的当前源文件">删除当前笔记</button>'
            '<label class="nm-source-vim-wrap" title="启用 CodeMirror 内置 Vim 子集（默认关闭；偏好会保存在本机）">'
            '<input type="checkbox" id="nm-source-vim" autocomplete="off"> Vim 键位</label>'
            '<span class="nm-source-status" id="nm-source-status"></span></div>'
            f'<div class="nm-source-path">{source_path_display}</div>'
            f'<script type="application/json" id="nm-page-meta">{meta}</script>'
            f'<script type="application/json" id="nm-source-json">{sjson}</script>'
            '<div id="nm-editor-mount"></div>'
        )
        cm_foot = (
            f'<script src="{prefix}_static/codemirror/lib/codemirror.js"></script>\n'
            f'<script src="{prefix}_static/codemirror/mode/xml/xml.js"></script>\n'
            f'<script src="{prefix}_static/codemirror/mode/markdown/markdown.js"></script>\n'
            f'<script src="{prefix}_static/codemirror/addon/edit/matchbrackets.js"></script>\n'
            f'<script src="{prefix}_static/codemirror/addon/search/searchcursor.js"></script>\n'
            f'<script src="{prefix}_static/codemirror/addon/dialog/dialog.js"></script>\n'
            f'<script src="{prefix}_static/codemirror/keymap/vim.js"></script>\n'
            f'<script src="{prefix}_static/nm-editor.js"></script>'
        )
    else:
        cm_head = ""
        cm_foot = ""
        highlighted_source = _highlight_source(source, source_path) if source else ""
        highlighted_source = highlighted_source.replace("{", "{{").replace("}", "}}")
        source_panel_inner = (
            f'<div class="nm-source-path">{source_path_display}</div>\n'
            f"<pre><code>{highlighted_source}</code></pre>"
        )
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
{cm_head}
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
.nm-tabs {{ display: flex; align-items: center; justify-content: space-between;
           border-bottom: 1px solid #e0e0e0; background: #fafafa; padding: 0 20px; }}
.nm-tabs-left {{ display: flex; align-items: center; gap: 0; }}
.nm-console-btn {{ text-decoration: none; font-size: 12px; color: #1565c0; border: 1px solid #bbdefb;
                  background: #e3f2fd; border-radius: 4px; padding: 4px 10px; }}
.nm-console-btn:hover {{ background: #bbdefb; color: #0d47a1; }}
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
.nm-source-toolbar {{ margin-bottom: 10px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
.nm-source-vim-wrap {{ font-size: 13px; color: #abb2bf; cursor: pointer; user-select: none; display: inline-flex;
    align-items: center; gap: 6px; }}
.nm-source-vim-wrap input {{ margin: 0; cursor: pointer; }}
.nm-source-save {{ padding: 6px 14px; background: #98c379; color: #1e1e1e; border: none; border-radius: 4px;
                   cursor: pointer; font-size: 13px; font-weight: 600; }}
.nm-source-save:hover {{ filter: brightness(1.08); }}
.nm-source-secondary {{ padding: 6px 12px; background: #3d4451; color: #e0e0e0; border: 1px solid #5c6370;
    border-radius: 4px; cursor: pointer; font-size: 13px; }}
.nm-source-secondary:hover {{ background: #4a5260; }}
.nm-source-delete {{ color: #e5a3a3; border-color: #8b4d4d; }}
.nm-source-delete:hover {{ background: #5c3d3d; }}
.nm-source-status {{ font-size: 12px; min-height: 1.2em; }}
/* CodeMirror：避免在 display:none 中初始化后行号与正文重叠；由 JS refresh 配合 */
.nm-source-panel.nm-tab-panel {{ overflow-x: hidden; }}
#nm-editor-mount .CodeMirror {{
  border: 1px solid #444; border-radius: 4px; font-size: 13px;
  height: auto !important;
}}
#nm-editor-mount .CodeMirror-scroll {{ min-height: 320px; overflow-y: auto !important; }}
#nm-editor-mount .CodeMirror-gutters {{
  background: #21222c !important; border-right: 1px solid #444 !important;
  width: auto !important; min-width: 3.2em;
}}
#nm-editor-mount .CodeMirror-linenumber {{
  color: #6272a4 !important; padding: 0 10px 0 6px !important; text-align: right;
}}
#nm-editor-mount .CodeMirror-sizer {{ min-width: 0 !important; }}
.nm-source-readonly-hint {{
  font-size: 12px; color: #bd93f9; margin-bottom: 10px; line-height: 1.5;
  padding: 8px 10px; background: #3d3f4a; border-radius: 4px; border: 1px solid #6272a4;
}}
.nm-source-static-fallback {{
  white-space: pre-wrap; word-break: break-word; font-family: 'SFMono-Regular', Consolas, monospace;
  font-size: 13px; line-height: 1.5; color: #f8f8f2; margin: 0; padding: 12px;
  background: #21222c; border: 1px solid #444; border-radius: 4px; max-height: 70vh; overflow: auto;
}}
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
.nm-type-grep {{ background: #eceff1; color: #37474f; }}
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
<li><code>dtag:关键词</code> — 仅搜索文档标签（reST 用 <code>:dtag:`标签`</code>；Markdown 用行内 <code>#标签</code>，须跟在空格或 <code>，。：</code> 等标点之后）</li>
<li><code>grep:正则</code> — 在当前工程内全局扫描源码（按 Enter 触发，结果逐步更新）</li>
</ul>
<div style="margin-top:6px;color:#888;">支持正则；可写多条件（空格分隔，AND），如 <code>dtag:数学 grep:\"pi.*=\"</code>；值含空格请用引号。带映射条件键入即查；含 <code>grep:</code> 时按 Enter 执行并渐进返回结果。</div>
</div>
</div>
<div id="nm-sidebar-toc" class="nm-sidebar-toc">{toc}</div>
<div id="nm-sidebar-search-results" class="nm-sidebar-search-results"></div>
{dtag_badges}
</div>
<div class="nm-main">
<div class="nm-tabs">
<div class="nm-tabs-left">
<button class="nm-tab active" data-tab="render">渲染</button>
<button class="nm-tab" data-tab="source">源文件</button>
<button class="nm-tab" data-tab="warnings">诊断</button>
</div>
<a class="nm-console-btn" href="/" title="返回控制台">控制台</a>
</div>
<div class="nm-tab-panels">
<div id="nm-panel-render" class="nm-tab-panel active">
{body}
</div>
<div id="nm-panel-source" class="nm-tab-panel nm-source-panel">
{source_panel_inner}
</div>
<div id="nm-panel-warnings" class="nm-tab-panel nm-warnings-panel">
{_escape(warnings_text)}
</div>
</div>
</div>
<script>window.NM_PREFIX="{prefix}";</script>
{cm_foot}
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

