"""Mobile / offline zip export: single page at a time — search only on index.html."""

import json

from notemanager.renderer.template import _static_prefix


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def wrap_export_index_html(title: str, tree_html: str) -> str:
    """首页：唯一提供全局查找与文档树；无弹层、无 KaTeX（首页无公式正文）。"""
    title_esc = _escape(title)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title_esc}</title>
<link rel="stylesheet" href="_static/export.css">
</head>
<body class="nm-export nm-export-index">
<header class="nm-exp-header nm-exp-header-index">
  <span class="nm-exp-title">{title_esc}</span>
</header>
<main class="nm-exp-main nm-exp-index-main">
  <p class="nm-exp-index-lead">打开一篇笔记后，屏幕只显示该篇内容。若要<strong>再次查找其他笔记</strong>，请点笔记页顶栏的<strong>首页</strong>回到本页。</p>
  <section class="nm-exp-block" aria-labelledby="nm-h-search">
    <h2 id="nm-h-search" class="nm-exp-block-title">查找</h2>
    <p class="nm-exp-muted">支持 <code>chapter:</code> / <code>label:</code> / <code>index:</code> / <code>dtag:</code> / <code>fname:</code> / <code>grep:</code>。</p>
    <input type="search" id="nm-export-search-input" class="nm-exp-search-input" placeholder="关键词 或 chapter: / label: / dtag: / index: / grep:" autocomplete="off">
    <div id="nm-export-grep-status" class="nm-exp-grep-status"></div>
    <button type="button" id="nm-export-grep-cancel" class="nm-exp-grep-cancel" hidden>中止 grep</button>
    <div id="nm-export-search-results" class="nm-exp-search-results"></div>
  </section>
  <section class="nm-exp-block" aria-labelledby="nm-h-tree">
    <h2 id="nm-h-tree" class="nm-exp-block-title">文档列表</h2>
    <div class="nm-exp-tree-wrap">{tree_html}</div>
  </section>
</main>
<script>window.NM_PREFIX="";</script>
<script src="_static/export.js"></script>
</body>
</html>
"""


def wrap_export_note_html(
    body: str,
    title: str,
    html_rel_path: str,
    *,
    toc: str = "",
) -> str:
    """笔记页：仅顶栏「首页」+ 标题；本页目录用 details 内联；无查找、无弹层。

    ``id="nm-panel-render"`` 供 ``build_index`` 使用。
    """
    prefix = _static_prefix(html_rel_path)
    title_esc = _escape(title)
    meta = json.dumps({"rel_path": html_rel_path.replace("\\", "/")}, ensure_ascii=False)
    toc_block = ""
    if toc.strip():
        toc_block = f"""<details class="nm-exp-toc-details">
<summary class="nm-exp-toc-summary">本页目录</summary>
<div class="nm-exp-toc-inner">{toc}</div>
</details>
"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title_esc}</title>
<link rel="stylesheet" href="{prefix}_static/export.css">
<link rel="stylesheet" href="{prefix}_static/katex.min.css">
</head>
<body class="nm-export nm-export-note">
<header class="nm-exp-header">
  <a class="nm-exp-home" href="{prefix}index.html">首页</a>
  <span class="nm-exp-title">{title_esc}</span>
</header>
<main class="nm-exp-main">
{toc_block}<div id="nm-panel-render" class="nm-exp-body">
{body}
</div>
</main>
<script type="application/json" id="nm-export-meta">{meta}</script>
<script>window.NM_PREFIX={json.dumps(prefix)};</script>
<script src="{prefix}_static/katex.min.js"></script>
<script src="{prefix}_static/katex-auto-render.min.js"></script>
<script src="{prefix}_static/mermaid.min.js"></script>
<script>if(typeof mermaid!=="undefined")mermaid.initialize({{startOnLoad:true,theme:"default"}});</script>
<script>
if(typeof renderMathInElement!=="undefined")renderMathInElement(document.getElementById("nm-panel-render"),{{delimiters:[{{left:"$$",right:"$$",display:true}},{{left:"$",right:"$",display:false}},{{left:"\\\\[",right:"\\\\]",display:true}},{{left:"\\\\(",right:"\\\\)",display:false}}],throwOnError:false,strict:"ignore"}});
</script>
</body>
</html>
"""
