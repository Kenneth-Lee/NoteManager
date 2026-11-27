"""HTML template wrapper for rendered content."""

import json
import re
import time
from pathlib import Path
from urllib.parse import quote

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


# 静态资源缓存版本号：进程启动时确定一次，所有页面共用。
_ASSET_VERSION = str(int(time.time()))


def wrap_html(body: str, title: str, html_rel_path: str,
              toc: str = "", source: str = "", warnings: list | None = None,
              source_path: str = "", dtags: list | None = None,
              source_abs_path: str = "",
              *, source_editable: bool = False,
              heading_lines: dict[str, int] | None = None,
              extra_css_files: list[str] | None = None,
              extra_js_files: list[str] | None = None,
              home_href: str | None = None,
              project_id: str = "",
              project_name: str = "") -> str:
    """Wrap an HTML body fragment in a full HTML5 page.

    Args:
        body: Rendered HTML body content.
        title: Page title.
        html_rel_path: Relative path of this HTML file (for computing static paths).
        toc: Optional TOC HTML fragment.
        source: Optional raw source text for source view.
        warnings: Optional list of warning strings for diagnostics view.
        source_path: Optional original source file path for display.
        source_abs_path: Optional absolute source path for the copyable badge.
        dtags: Optional list of dtag strings for this document.
        source_editable: When True, source tab uses CodeMirror + save API (managed projects only).
        heading_lines: Optional mapping of heading anchor id -> 1-based source line number,
            used by the frontend to jump from TOC entries to source positions.
        home_href: Optional target of the sidebar Home link. Defaults to the project
            landing page next to this document. Temp single-file views have no
            project home and pass "/" so Home falls back to the console.

    Returns:
        Complete HTML document string.
    """
    prefix = _static_prefix(html_rel_path)
    if home_href is None:
        home_href = prefix + "index.html"
    doc_label = "首页" if project_name and title == project_name else title
    project_crumb_html = (
        '<a href="' + _escape(home_href) + '" title="项目首页">' +
        _escape(project_name) + '</a><span class="nm-crumb-sep">›</span>'
        if project_name else ""
    )
    dir_crumb_html = ""
    if project_name:
        current_parts: list[str] = []
        for part in Path(html_rel_path).parent.parts:
            if part in ("", "."):
                continue
            current_parts.append(str(part))
            dir_path = "/".join(current_parts)
            dir_href = f"{home_href}#nm-dir={quote(dir_path, safe='')}"
            dir_crumb_html += (
                f'<a href="{_escape(dir_href)}" title="打开目录 {_escape(dir_path)}">'
                f'{_escape(str(part))}</a><span class="nm-crumb-sep">›</span>'
            )
    warnings = warnings or []
    dtags = dtags or []
    extra_css_files = extra_css_files or []
    extra_js_files = extra_js_files or []

    source_path_display = _escape(source_path) if source_path else ""
    source_abs_display = _escape(source_abs_path or source_path) if (source_abs_path or source_path) else ""
    source_badge_html = (
        f'<span id="nm-source-file-badge" class="nm-source-file-badge" '
        f'data-nm-copy="{source_abs_display}" data-nm-copy-label="源: {source_abs_display}" '
        f'title="源文件: {source_abs_display}（点击复制）">源: {source_abs_display}</span>'
        if source_abs_display else ""
    )
    delete_current_button = ""
    if source_editable and source_path:
        delete_current_button = (
            f'<button type="button" class="nm-delete-current-btn" id="nm-delete-current" '
            f'data-nm-delete-source="{_escape(source_path)}" '
            f'data-nm-delete-title="{_escape(str(title))}" '
            f'title="删除当前笔记（移到回收站）">删除</button>'
        )
    heading_map_json = (
        json.dumps(heading_lines or {}, ensure_ascii=False).replace("<", "\\u003c")
    )
    # 版本号在进程启动时固定一次（见 _ASSET_VERSION）。以前每次渲染都取一次
    # time.time()，同一站点里不同页面的 ?v= 各不相同，每次重建后浏览器都要
    # 整包重新下载 JS/CSS，白白拖慢导航。
    asset_version = _ASSET_VERSION

    extra_css_tags = "\n".join(
        f'<link rel="stylesheet" href="{prefix}_static/{_escape(name)}">'
        for name in extra_css_files
    )
    extra_js_tags = "\n".join(
        f'<script src="{prefix}_static/{_escape(name)}"></script>'
        for name in extra_js_files
    )

    source_panel_attrs = ""
    if source_editable and source_path:
        highlighted_source = ""
        cm_head = (
            f'<link rel="stylesheet" href="{prefix}_static/codemirror/lib/codemirror.css?v={asset_version}">\n'
            f'<link rel="stylesheet" href="{prefix}_static/codemirror/theme/dracula.css?v={asset_version}">\n'
            f'<link rel="stylesheet" href="{prefix}_static/codemirror/addon/dialog/dialog.css?v={asset_version}">'
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
            '<button type="button" class="nm-source-secondary" id="nm-source-paste-image" title="把剪贴板中的图片粘贴到当前笔记目录的 _static">粘贴图片</button>'
            '<button type="button" class="nm-source-secondary" id="nm-source-import" title="把本地文件或 URL 导入当前笔记目录的 _static">导入资源</button>'
            '<button type="button" class="nm-source-secondary nm-source-delete" id="nm-source-delete" title="删除磁盘上的当前源文件">删除当前笔记</button>'
            '<label class="nm-source-vim-wrap" title="启用 CodeMirror 内置 Vim 子集（默认关闭；:help 查看命令；偏好会保存在本机）">'
            '<input type="checkbox" id="nm-source-vim" autocomplete="off"> Vim 键位</label>'
            '<span class="nm-source-status" id="nm-source-status"></span></div>'
            f'<div class="nm-source-path">{source_path_display}</div>'
            f'<script type="application/json" id="nm-page-meta">{meta}</script>'
            f'<script type="application/json" id="nm-source-json">{sjson}</script>'
            '<div id="nm-editor-mount"></div>'
        )
        cm_foot = (
            f'<script src="{prefix}_static/codemirror/lib/codemirror.js?v={asset_version}"></script>\n'
            f'<script src="{prefix}_static/codemirror/mode/xml/xml.js?v={asset_version}"></script>\n'
            f'<script src="{prefix}_static/codemirror/mode/markdown/markdown.js?v={asset_version}"></script>\n'
            f'<script src="{prefix}_static/codemirror/addon/edit/matchbrackets.js?v={asset_version}"></script>\n'
            f'<script src="{prefix}_static/codemirror/addon/search/searchcursor.js?v={asset_version}"></script>\n'
            f'<script src="{prefix}_static/codemirror/addon/dialog/dialog.js?v={asset_version}"></script>\n'
            f'<script src="{prefix}_static/codemirror/keymap/vim.js?v={asset_version}"></script>\n'
            f'<script src="{prefix}_static/nm-editor.js?v={asset_version}"></script>\n'
            f'<script src="{prefix}_static/nm-table-editor.js?v={asset_version}"></script>\n'
            f'<script src="{prefix}_static/nm-math-editor.js?v={asset_version}"></script>\n'
            f'<script src="{prefix}_static/nm-object-editor.js?v={asset_version}"></script>'
        )
    else:
        cm_head = ""
        cm_foot = ""
        highlighted_source = _highlight_source(source, source_path) if source else ""
        highlighted_source = highlighted_source.replace("{", "{{").replace("}", "}}")
        source_panel_inner = (
            f'<div class="nm-source-path">{source_path_display}</div>\n'
            '<div class="nm-source-readonly-note">'
            '只读视图；如需编辑，请使用「外部编辑」。</div>'
            f"<pre><code>{highlighted_source}</code></pre>"
        )
        source_panel_attrs = ' tabindex="0"'
    warnings_text = "\\n".join(warnings) if warnings else "无诊断信息"

    temp_monitor_script = ""
    if source_path and not source_editable:
        temp_monitor_script = f"""
<script type="application/json" id="nm-temp-meta">{json.dumps({"source_path": source_path}, ensure_ascii=False)}</script>
<script>
(function() {{
    var metaEl = document.getElementById('nm-temp-meta');
    if (!metaEl) return;
    var meta;
    try {{ meta = JSON.parse(metaEl.textContent || '{{}}'); }} catch (e) {{ return; }}
    var file = meta.source_path || '';
    if (!file) return;
    var url = '/temp/check?file=' + encodeURIComponent(file);
    var timer = setInterval(function() {{
        fetch(url, {{cache: 'no-store'}})
          .then(function(r) {{ return r.ok ? r.json() : null; }})
          .then(function(d) {{
            if (d && d.reload) {{
              window.location.reload();
            }}
          }})
          .catch(function() {{}});
    }}, 2000);
    if (typeof window !== 'undefined') {{
        window.addEventListener('beforeunload', function() {{ clearInterval(timer); }});
    }}
}})();
</script>
"""

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
<link rel="stylesheet" href="{prefix}_static/notemanager.css?v={asset_version}">
<link rel="stylesheet" href="{prefix}_static/preformatted.css">
<link rel="stylesheet" href="{prefix}_static/katex.min.css">
{extra_css_tags}
{cm_head}
<style>
{_PYGMENTS_CSS}
/* Sidebar */
.nm-sidebar {{ position: fixed; top: 0; left: 0; width: 280px; height: 100vh;
               background: #f8f9fa; border-right: 1px solid #e0e0e0; overflow-y: auto;
               z-index: 50; display: flex; flex-direction: column; }}
.nm-sidebar-crumbs {{ padding: 10px 14px 8px; border-bottom: 1px solid #e0e0e0; font-size: 12px;
                      display: flex; align-items: center; gap: 5px; row-gap: 4px;
                      flex-wrap: wrap; min-width: 0; }}
.nm-sidebar-crumbs a {{ color: #1565c0; text-decoration: none; font-weight: 600; white-space: nowrap; }}
.nm-sidebar-crumbs a:hover {{ text-decoration: underline; }}
.nm-crumb-sep {{ color: #b0bec5; flex: 0 0 auto; }}
.nm-crumb-doc {{ color: #37474f; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
                 min-width: 0; }}
.nm-sidebar-tools {{ padding: 6px 14px 9px; border-bottom: 1px solid #e0e0e0;
                     display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }}
.nm-new-note-btn {{ border: 1px solid #bbdefb; background: #f5f9ff; color: #1565c0; border-radius: 4px;
                    font-size: 11px; padding: 2px 6px; cursor: pointer; font-weight: 400; white-space: nowrap; }}
.nm-new-note-btn:hover {{ background: #e3f2fd; }}
.nm-trash-btn {{ border: 1px solid #e0e0e0; background: #fafafa; color: #777; border-radius: 4px;
                 font-size: 10px; padding: 2px 5px; cursor: pointer; font-weight: 400; white-space: nowrap; }}
.nm-trash-btn:hover {{ background: #f0f0f0; color: #333; }}
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
    box-shadow: 0 4px 12px rgba(0,0,0,0.15); z-index: 200; font-size: 12px; color: #333; line-height: 1.6;
    max-height: 72vh; overflow-y: auto; }}
.nm-search-help-popup.show {{ display: block; }}
.nm-search-help-popup h4 {{ margin: 0 0 6px 0; font-size: 13px; color: #333; }}
.nm-search-help-popup code {{ background: #f0f0f0; padding: 1px 4px; border-radius: 2px; font-size: 11px; }}
.nm-search-help-popup ul {{ margin: 4px 0; padding-left: 16px; }}
.nm-search-help-popup li {{ margin: 2px 0; }}
.nm-regex-help {{ margin-top: 8px; border-top: 1px solid #eee; padding-top: 7px; }}
.nm-regex-help summary {{ cursor: pointer; color: #1565c0; font-weight: 600; }}
.nm-regex-table {{ width: 100%; border-collapse: collapse; margin: 6px 0 2px; font-size: 11px; }}
.nm-regex-table th, .nm-regex-table td {{ border-bottom: 1px solid #eee; padding: 3px 4px;
    text-align: left; vertical-align: top; }}
.nm-regex-table th {{ color: #777; font-weight: 500; }}
.nm-regex-table code {{ white-space: nowrap; }}
.nm-search-shortcut-hint {{ display: flex; align-items: center; gap: 5px; flex-wrap: wrap;
    margin-top: 6px; padding: 4px 6px; background: #f5f9ff; border: 1px solid #e1ecf8;
    border-radius: 4px; color: #607d8b; font-size: 11px; }}
.nm-search-shortcut-hint kbd {{ border: 1px solid #c5d9ed; border-bottom-width: 2px; border-radius: 3px;
    background: #fff; color: #455a64; padding: 1px 4px; font-family: monospace; font-size: 10px; }}
.nm-shortcut-link {{ border: 0; background: none; color: #1565c0; cursor: pointer; padding: 0;
    font-size: 11px; text-decoration: none; }}
.nm-shortcut-link:hover {{ color: #0d47a1; text-decoration: underline; }}
.nm-sidebar-switch {{ padding: 8px 12px; border-bottom: 1px solid #e0e0e0; display: flex; gap: 8px; }}
.nm-sidebar-switch-btn {{ flex: 1; padding: 6px 8px; border: 1px solid #bbdefb; border-radius: 4px;
    background: #fff; color: #1565c0; cursor: pointer; font-size: 12px; }}
.nm-sidebar-switch-btn:hover {{ background: #e3f2fd; }}
.nm-sidebar-switch-btn.active {{ background: #e3f2fd; font-weight: 600; }}
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
.nm-main {{ margin-left: 280px; height: 100vh; display: flex; flex-direction: column; }}
.nm-tabs {{ display: flex; align-items: center; justify-content: space-between;
           border-bottom: 1px solid #e0e0e0; background: #fafafa; padding: 0 20px; flex: 0 0 auto; }}
.nm-tabs-left {{ display: flex; align-items: center; gap: 0; }}
.nm-tabs-right {{ display: flex; align-items: center; gap: 10px; }}
.nm-page-id {{ max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
              font-size: 11px; color: #607d8b; background: #f5f7f8; border: 1px solid #dde5e8;
              border-radius: 10px; padding: 3px 9px; cursor: pointer; user-select: none; }}
.nm-page-id:hover {{ background: #e8f1f5; border-color: #b0bec5; color: #37474f; }}
.nm-page-id.copied {{ background: #e8f5e9; border-color: #a5d6a7; color: #2e7d32; }}
.nm-source-file-badge {{ max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
                        font-size: 11px; color: #607d8b; background: #f5f7f8; border: 1px solid #dde5e8;
                        border-radius: 10px; padding: 3px 9px; cursor: pointer; user-select: none;
                        font-family: 'SFMono-Regular', Consolas, monospace; }}
.nm-source-file-badge:hover {{ background: #e8f1f5; border-color: #b0bec5; color: #37474f; }}
.nm-source-file-badge.copied {{ background: #e8f5e9; border-color: #a5d6a7; color: #2e7d32; }}
.nm-split-btn {{ font-size: 12px; color: #1565c0; border: 1px solid #bbdefb; background: #e3f2fd;
                 border-radius: 4px; padding: 4px 10px; cursor: pointer; }}
.nm-split-btn:hover {{ background: #bbdefb; color: #0d47a1; }}
.nm-split-btn.active {{ background: #1565c0; color: #fff; border-color: #1565c0; font-weight: 600; }}
@media (max-width: 1000px) {{ .nm-split-btn {{ display: none; }} }}
body:not(.nm-split) #nm-sync-btn {{ display: none; }}
.nm-delete-current-btn {{ font-size: 12px; color: #b71c1c; border: 1px solid #ef9a9a;
                         background: #ffebee; border-radius: 4px; padding: 4px 9px; cursor: pointer; }}
.nm-delete-current-btn:hover {{ background: #ffcdd2; color: #8e0000; }}
.nm-console-btn {{ text-decoration: none; font-size: 12px; color: #1565c0; border: 1px solid #bbdefb;
                  background: #e3f2fd; border-radius: 4px; padding: 4px 10px; }}
.nm-console-btn:hover {{ background: #bbdefb; color: #0d47a1; }}
.nm-tab {{ padding: 8px 16px; cursor: pointer; border: 1px solid transparent; border-bottom: none;
           background: none; font-size: 13px; color: #666; border-radius: 4px 4px 0 0; margin-top: 4px; }}
.nm-tab:hover {{ color: #333; background: #f0f0f0; }}
.nm-tab.active {{ background: white; color: #333; border-color: #e0e0e0; font-weight: 500; }}
.nm-tab-panels {{ flex: 1 1 auto; position: relative; overflow: hidden; min-height: 0; }}
.nm-tab-panel {{ display: none; padding: 20px 20px 48px; overflow-y: auto; height: 100%;
                 box-sizing: border-box; overscroll-behavior: contain; }}
.nm-tab-panel.active {{ display: block; position: absolute; top: 0; left: 0; right: 0; bottom: 0; }}
/* Split view: render + source side by side */
body.nm-split .nm-tab-panels {{ display: flex; }}
body.nm-split .nm-tab-panel {{ position: static; }}
body.nm-split #nm-panel-render, body.nm-split #nm-panel-source {{ display: block !important;
    flex: 1 1 0; min-width: 0; height: 100%; }}
body.nm-split #nm-panel-source {{ border-left: 1px solid #e0e0e0; }}
body.nm-split #nm-panel-warnings {{ display: none !important; }}
/* Anchor jump offset inside the scrolling panel */
#nm-panel-render [id] {{ scroll-margin-top: 16px; }}
/* Back to top */
#nm-backtop {{ position: fixed; right: 26px; bottom: 26px; width: 40px; height: 40px; border-radius: 50%;
               border: 1px solid #ccc; background: #fff; color: #555; font-size: 18px; line-height: 1;
               cursor: pointer; box-shadow: 0 2px 10px rgba(0,0,0,0.18); display: none; z-index: 90; }}
#nm-backtop.show {{ display: block; }}
#nm-backtop:hover {{ background: #eef4fb; color: #0066cc; }}
/* Source line jump flash highlight */
.nm-src-flash {{ position: fixed; background: rgba(255,213,79,0.4); border-radius: 3px;
                 pointer-events: none; z-index: 95; transition: opacity 0.6s ease; }}
.nm-src-flash.fade {{ opacity: 0; }}
#nm-editor-mount .CodeMirror .nm-flash-line {{ background: rgba(255,213,79,0.22); }}
@media print {{
  .nm-sidebar, .nm-tabs, #nm-backtop {{ display: none !important; }}
  .nm-main {{ margin-left: 0; height: auto; }}
  .nm-tab-panels {{ overflow: visible; display: block; }}
  .nm-tab-panel {{ display: none !important; height: auto; overflow: visible; position: static; }}
  .nm-tab-panel.active {{ display: block !important; }}
  body.nm-split .nm-tab-panels {{ display: block; }}
  body.nm-split #nm-panel-render, body.nm-split #nm-panel-source {{ display: block !important; }}
}}
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
.nm-dir > details > summary {{ cursor: pointer; font-weight: 500; padding: 2px 0; list-style: none;
                               display: flex; align-items: center; gap: 3px; }}
.nm-dir > details > summary::before {{ content: "\\1F4C1 "; }}
.nm-dir > details[open] > summary::before {{ content: "\\1F4C2 "; }}
.nm-dir-label {{ flex: 1; min-width: 0; overflow-wrap: anywhere; }}
.nm-tree-add {{ border: 0; background: transparent; color: #1565c0; cursor: pointer; font-size: 15px;
                line-height: 1; padding: 0 3px; opacity: 0; }}
.nm-dir > details > summary:hover .nm-tree-add,
.nm-dir > details > summary:focus-within .nm-tree-add {{ opacity: 1; }}
.nm-tree-add:hover {{ color: #0d47a1; }}
.nm-file {{ display: flex; align-items: center; gap: 3px; }}
.nm-file a {{ text-decoration: none; color: #0066cc; flex: 1; min-width: 0; overflow-wrap: anywhere; }}
.nm-file a:hover {{ text-decoration: underline; }}
.nm-file::before {{ content: "\\1F4C4 "; flex: 0 0 auto; }}
.nm-file-del {{ border: 0; background: transparent; color: #b71c1c; cursor: pointer; font-size: 15px;
                line-height: 1; padding: 0 3px; opacity: 0; }}
.nm-file:hover .nm-file-del, .nm-file:focus-within .nm-file-del {{ opacity: 1; }}
.nm-file-del:hover {{ background: #ffebee; border-radius: 3px; }}
@media (hover: none) {{
    .nm-tree-add, .nm-file-del {{ opacity: 1; }}
}}
</style>
</head>
<body data-nm-project="{_escape(project_id)}" data-nm-page-path="{_escape(str(html_rel_path))}" data-nm-source-path="{_escape(source_path)}">
<div class="nm-sidebar">
<div class="nm-sidebar-crumbs">
<a href="/" title="返回控制台">控制台</a>
<span class="nm-crumb-sep">›</span>
{project_crumb_html}{dir_crumb_html}<span class="nm-crumb-doc" title="{_escape(title)}">{_escape(doc_label)}</span>
</div>
<div class="nm-sidebar-tools">
<button type="button" id="nm-new-root" class="nm-new-note-btn" title="在当前目录新建笔记">＋ 新建</button>
<button type="button" id="nm-trash-open" class="nm-trash-btn" title="回收站">回收站</button>
<button type="button" id="nm-assets-open" class="nm-trash-btn" title="清理未引用图片">资源清理</button>
</div>
<div class="nm-sidebar-search">
<input type="text" id="nm-search" placeholder="搜索...">
<button class="nm-search-help" onclick="nmToggleHelp()" title="搜索语法帮助">?</button>
<div id="nm-search-help" class="nm-search-help-popup">
<h4>搜索语法</h4>
<ul>
<li><code>关键词</code> — 搜索所有类型</li>
<li><code>chapter:关键词</code> — 仅搜索章节标题</li>
<li><code>chapter:^pi$</code> — 章节标题完全等于 pi（区分大小写时仍忽略大小写）</li>
<li><code>chapter:&#92;bpi&#92;b</code> — 章节标题中的全词 pi；<code>&#92;bpi&#92;b</code> 可搜索所有类型</li>
<li><code>label:关键词</code> — 仅搜索锚点标签</li>
<li><code>index:关键词</code> — 仅搜索索引条目</li>
<li><code>dtag:关键词</code> — 仅搜索文档标签（reST 用 <code>:dtag:`标签`</code>；Markdown 用行内 <code>#标签</code>，须跟在空格或 <code>，。：</code> 等标点之后）</li>
<li><code>grep:正则</code> — 在当前工程内全局扫描源码（按 Enter 触发，结果逐步更新）</li>
</ul>
<div style="margin-top:6px;color:#888;">支持正则；可写多条件（空格分隔，AND），如 <code>dtag:数学 grep:\"pi.*=\"</code>；值含空格请用引号。带映射条件键入即查；含 <code>grep:</code> 时按 Enter 执行并渐进返回结果。</div>
<details class="nm-regex-help">
<summary>正则表达式速查</summary>
<p style="margin:4px 0;color:#888;">默认忽略大小写。搜索框会保留反斜杠转义；引号只用于包含空格，不会自动变成全词匹配。</p>
<table class="nm-regex-table">
<tr><th>写法</th><th>含义</th><th>例子</th></tr>
<tr><td><code>pi</code></td><td>包含子串 pi</td><td><code>chapter:pi</code> 会命中 acpi、API</td></tr>
<tr><td><code>^pi$</code></td><td>整个字段就是 pi</td><td><code>chapter:^pi$</code></td></tr>
<tr><td><code>&#92;bpi&#92;b</code></td><td>完整单词 pi</td><td><code>chapter:&#92;bpi&#92;b</code></td></tr>
<tr><td><code>pi|acpi</code></td><td>二选一</td><td><code>chapter:pi|acpi</code></td></tr>
<tr><td><code>pi.*</code> / <code>.*pi</code></td><td>以 pi 开头 / 结尾</td><td><code>chapter:^pi.*</code></td></tr>
<tr><td><code>[Pp]i</code></td><td>字符集合</td><td><code>chapter:[Pp]i</code></td></tr>
<tr><td><code>(pi|acpi)</code></td><td>分组</td><td><code>chapter:(pi|acpi)</code></td></tr>
<tr><td><code>&#92;d+</code> / <code>&#92;s+</code></td><td>数字 / 空白</td><td><code>grep:&#92;d+</code></td></tr>
<tr><td><code>&#92;.</code></td><td>字面点号</td><td><code>grep:pi&#92;.</code></td></tr>
</table>
<p style="margin:4px 0 0;color:#888;">&#92;b 是 ASCII 单词边界，对中文通常不起作用；中文关键词直接写即可。</p>
</details>
<h4 style="margin-top:10px;">快捷键</h4>
<ul>
<li><code>Ctrl/⌘ K</code> — 打开/关闭命令面板</li>
<li><code>Ctrl/⌘+Alt+1/2/3</code> 或 <code>Ctrl/⌘+Alt+C</code> — 切换到渲染 / 源文件 / 诊断；部分桌面环境会占用数字组合键，可用 C</li>
<li><code>Ctrl/⌘+Alt+S</code> — 切换分屏</li>
<li><code>Ctrl/⌘+Alt+E</code> — 用外部编辑器打开当前源文件</li>
<li><code>Alt+↑</code> / <code>Ctrl/⌘+Alt+U</code> — 回到项目主页并定位当前目录</li>
<li><code>↑</code> / <code>↓</code> — 在命令面板中选择</li>
<li><code>Enter</code> — 打开选中项</li>
<li><code>Esc</code> — 关闭命令面板/帮助</li>
</ul>
</div>
<div class="nm-search-shortcut-hint">
<kbd>Ctrl/⌘ K</kbd>
<button type="button" id="nm-command-hint-btn" class="nm-shortcut-link">命令面板</button>
<span>·</span>
<button type="button" id="nm-shortcuts-help-link" class="nm-shortcut-link">快捷键说明</button>
</div>
</div>
<div class="nm-sidebar-switch">
<button id="nm-sidebar-view-toc" class="nm-sidebar-switch-btn active" data-view="toc" type="button">目录</button>
<button id="nm-sidebar-view-results" class="nm-sidebar-switch-btn" data-view="results" type="button">搜索结果</button>
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
<div class="nm-tabs-right">
<span id="nm-page-id" class="nm-page-id" role="button" tabindex="0"
      title="当前浏览器标签页 ID；点击复制，可用于 nmr view/search --id">页面: …</span>
{source_badge_html}
<span id="nm-reading-controls" class="nm-reading-controls"></span>
{delete_current_button}
<button class="nm-split-btn" id="nm-open-external" type="button" title="在外部编辑器中打开当前源文件（默认 gvim；Ctrl/⌘+Alt+E）" style="display:none">外部编辑</button>
<button class="nm-split-btn" id="nm-split-btn" type="button" title="左右分屏，同时查看渲染与源文件">分屏</button>
<button class="nm-split-btn" id="nm-sync-btn" type="button" title="分屏时双向同步滚动（点击开/关）">同步</button>
</div>
</div>
<div class="nm-tab-panels">
<div id="nm-panel-render" class="nm-tab-panel active">
{body}
<div id="nm-related" class="nm-related"
     data-nm-related-src="{prefix}_index/relations.json"
     data-nm-related-path="{_escape(str(html_rel_path))}"
     data-nm-related-prefix="{prefix}"></div>
</div>
<div id="nm-panel-source" class="nm-tab-panel nm-source-panel"{source_panel_attrs}>
{source_panel_inner}
</div>
<div id="nm-panel-warnings" class="nm-tab-panel nm-warnings-panel">
{_escape(warnings_text)}
</div>
</div>
</div>
<button id="nm-backtop" type="button" title="回到文档开头">\u2191</button>
<script type="application/json" id="nm-heading-map">{heading_map_json}</script>
{temp_monitor_script}
<script>window.NM_PREFIX="{prefix}";</script>
{cm_foot}
<script src="{prefix}_static/notemanager.js?v={asset_version}"></script>
<script src="{prefix}_static/nm-external-editor.js?v={asset_version}"></script>
<script src="{prefix}_static/nm-relations.js?v={asset_version}"></script>
<script src="{prefix}_static/nm-annotations.js?v={asset_version}"></script>
<script src="{prefix}_static/nm-refs.js?v={asset_version}"></script>
<script src="{prefix}_static/nm-reading.js?v={asset_version}"></script>
<script src="{prefix}_static/nm-command.js?v={asset_version}"></script>
<script src="{prefix}_static/nm-filetree.js?v={asset_version}"></script>
<script src="{prefix}_static/katex.min.js"></script>
<script src="{prefix}_static/katex-auto-render.min.js"></script>
<script src="{prefix}_static/mermaid.min.js"></script>
{extra_js_tags}
<script>
if(typeof mermaid!=='undefined'){{
    function nmRenderMermaidError(node, title, msg) {{
        if(!node) return;
        var text = msg && msg.message ? msg.message : String(msg || 'Mermaid error');
        node.setAttribute('data-nm-mermaid-error', text);
        node.innerHTML = '<div class="nmMermaidError"><strong>' + title + '</strong><div>' + text + '</div></div>';
    }}

    mermaid.initialize({{startOnLoad:false,theme:'default'}});
    mermaid.parseError = function(err, hash) {{
        var node = document.querySelector('pre.mermaid[data-mermaid-hash="' + (hash || '') + '"]');
        if(node) {{
            var msg = err && err.message ? err.message : String(err || 'Mermaid parse error');
            nmRenderMermaidError(node, 'Mermaid 解析错误', msg);
        }}
    }};
    document.addEventListener('DOMContentLoaded', function() {{
        document.querySelectorAll('pre.mermaid').forEach(function(node) {{
            node.setAttribute('data-nm-mermaid-root', 'true');
            var text = (node.textContent || '').trim();
            if(!text) return;
            try {{
                mermaid.parse(text).then(function() {{
                    return mermaid.run({{nodes:[node]}});
                }}).catch(function(e) {{
                    var msg = e && e.message ? e.message : String(e || 'Mermaid render error');
                    nmRenderMermaidError(node, 'Mermaid 运行错误', msg);
                }});
            }} catch (e) {{
                var msg = e && e.message ? e.message : String(e || 'Mermaid render error');
                nmRenderMermaidError(node, 'Mermaid 语法错误', msg);
            }}
        }});
    }});
}}
</script>
<style>
.nmMermaidError {{
    border: 1px solid #f0b8b8;
    background: #fff4f4;
    color: #8f2d2d;
    padding: 10px 12px;
    border-radius: 6px;
    font-family: sans-serif;
    line-height: 1.5;
}}
.nmMermaidError strong {{
    display: block;
    margin-bottom: 4px;
}}
</style>
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
if(typeof renderMathInElement!=='undefined')renderMathInElement(document.getElementById('nm-panel-render'),{{delimiters:[{{left:'$$',right:'$$',display:true}},{{left:'$',right:'$',display:false}},{{left:'\\\\[',right:'\\\\]',display:true}},{{left:'\\\\(',right:'\\\\)',display:false}},{{left:'\\\\begin{{equation*}}',right:'\\\\end{{equation*}}',display:true}},{{left:'\\\\begin{{equation}}',right:'\\\\end{{equation}}',display:true}},{{left:'\\\\begin{{align*}}',right:'\\\\end{{align*}}',display:true}},{{left:'\\\\begin{{align}}',right:'\\\\end{{align}}',display:true}}],throwOnError:false,strict:'ignore'}});
</script>
</body>
</html>
"""


def _escape(text: str) -> str:
    """Escape HTML special characters."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

