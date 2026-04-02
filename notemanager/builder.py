"""Build engine for NoteManager.

Provides both full-build and incremental single-file update capabilities.
"""

import json
import logging
import datetime
from collections.abc import Callable
from pathlib import Path

from notemanager.scanner import scan_directory, FileInfo
from notemanager.scan_manifest import build_manifest_payload, write_scan_manifest
from notemanager.renderer.rst_renderer import render_rst
from notemanager.renderer.md_renderer import render_md
from notemanager.renderer.template import wrap_html
from notemanager.ref_resolver import extract_labels, resolve_references
from notemanager.indexer import build_index, extract_rst_index_directives
from notemanager.indexer import extract_dtags_from_sources

logger = logging.getLogger(__name__)


def _merge_dtag_lists(
    from_source: list[str] | None,
    from_render: list[str] | None,
) -> list[str]:
    """合并源码抽取与渲染器得到的标签，保序去重。"""
    out: list[str] = []
    seen: set[str] = set()
    for t in (from_source or []) + (from_render or []):
        t = (t or "").strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def full_build(
    project_root: str | Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Full build: scan, render all files, rebuild index.

    Returns the .NoteManager directory path.
    """
    def p(msg: str) -> None:
        if progress:
            progress(msg)

    project_root = Path(project_root).resolve()
    files = scan_directory(project_root)
    n = len(files)
    p(f"  已扫描 {n} 个源文件")

    nm_dir = project_root / ".NoteManager"
    html_dir = nm_dir / "html"
    static_dir = html_dir / "_static"
    index_dir = html_dir / "_index"

    # Clean up old directories if they exist
    for old in (nm_dir / "index", nm_dir / "static"):
        if old.exists():
            import shutil
            shutil.rmtree(old, ignore_errors=True)

    for d in (html_dir, static_dir, index_dir):
        d.mkdir(parents=True, exist_ok=True)

    # First pass: render all files
    rendered = {}
    for fi in files:
        rendered[fi.rel_path] = _render_file(fi)
    if n:
        p(f"  语法转换完成 ({n} 个文件)")

    # Extract labels
    p("  解析标签、交叉引用与 index 指令…")
    ref_labels = extract_labels(project_root, files)

    # Extract .. index:: directives from RST sources
    rst_index_dirs = extract_rst_index_directives(files)

    # 文档标签：以源码抽取为准（含 Markdown #标签、reST :dtag:），并与渲染结果合并；
    # 避免仅依赖 render_md 返回值时，旧安装或未同步代码导致 rebuild 后 dtag 索引为空。
    dtags_extracted = extract_dtags_from_sources(files)

    # Second pass: resolve references and write HTML
    documents = []
    all_dtags: dict[str, list[str]] = {}
    last_report = 0
    step = max(1, n // 15) if n else 1
    for j, fi in enumerate(files):
        r = rendered[fi.rel_path]
        key = str(fi.rel_path)
        merged = _merge_dtag_lists(dtags_extracted.get(key), r.get("dtags"))
        if merged:
            all_dtags[key] = merged
        r_out = {**r, "dtags": merged}
        doc = _write_html(html_dir, fi, r_out, ref_labels)
        documents.append(doc)
        k = j + 1
        if n and (k == n or k <= 2 or k - last_report >= step):
            p(f"  生成 HTML {k}/{n} {fi.rel_path}")
            last_report = k

    # Build dtag index (tag -> list of html paths)
    dtag_index = _build_dtag_index(all_dtags)

    # Build search index (includes labels and index directives)
    p("  构建搜索索引…")
    index_count = build_index(documents, index_dir, ref_labels, rst_index_dirs, all_dtags)

    # Write ref_labels
    (index_dir / "ref_labels.json").write_text(
        json.dumps(ref_labels, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Write dtag index
    (index_dir / "dtag_index.json").write_text(
        json.dumps(dtag_index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Copy static assets
    p("  写入 _static（notemanager.js / css 等）…")
    _copy_static_assets(static_dir)

    # Copy project assets (images, etc.) to html directory
    _copy_project_assets(project_root, html_dir)

    # Generate landing page
    p("  生成首页与状态…")
    _generate_landing_page(html_dir, documents)

    # Write status
    _write_status(
        nm_dir,
        len(files),
        len(ref_labels),
        index_count,
        _unique_dtag_count(all_dtags),
    )

    write_scan_manifest(nm_dir, build_manifest_payload(project_root, files))

    return nm_dir


def update_file(
    project_root: str | Path,
    file_path: str | Path,
    *,
    refresh_static_assets: bool = True,
) -> None:
    """Incrementally update a single file.

    Re-renders the file, rebuilds the full index and ref_labels
    (since labels may have changed).

    Args:
        refresh_static_assets: 为 False 时跳过将 _static（含 CodeMirror 等）拷入 html；
            Web 保存源码时设为 False 可明显缩短耗时。文件监控触发的更新仍应用默认 True。
    """
    project_root = Path(project_root).resolve()
    file_path = Path(file_path).resolve()

    rel_path = file_path.relative_to(project_root)
    ext = file_path.suffix.lower()

    if ext not in (".rst", ".md"):
        return

    nm_dir = project_root / ".NoteManager"
    html_dir = nm_dir / "html"
    static_dir = html_dir / "_static"
    index_dir = html_dir / "_index"

    if not nm_dir.exists():
        # No build yet, do a full build instead
        full_build(project_root)
        return

    static_dir.mkdir(parents=True, exist_ok=True)
    if refresh_static_assets:
        _copy_static_assets(static_dir)
    _copy_project_assets(project_root, html_dir)

    # Re-scan to get current file list
    files = scan_directory(project_root)

    # Re-render just this file
    st = file_path.stat()
    fi = FileInfo(
        path=file_path,
        rel_path=rel_path,
        ext=ext,
        mtime=st.st_mtime,
        size=st.st_size,
    )
    rendered = _render_file(fi)

    # Re-extract all labels (this file may have changed labels)
    ref_labels = extract_labels(project_root, files)

    # Re-extract .. index:: directives
    rst_index_dirs = extract_rst_index_directives(files)

    # Re-extract dtags from source
    dtags_per_doc = extract_dtags_from_sources(files)

    # 侧栏徽章与索引一致：合并源码抽取与渲染结果
    merged = _merge_dtag_lists(dtags_per_doc.get(str(rel_path)), rendered.get("dtags"))
    rendered = {**rendered, "dtags": merged}

    # Write this file's HTML
    _write_html(html_dir, fi, rendered, ref_labels)

    # Rebuild full index
    documents = []
    for f in files:
        html_rel = f.rel_path.with_suffix(".html")
        html_path = html_dir / html_rel
        if html_path.exists():
            body = html_path.read_text(encoding="utf-8")
            content = f.path.read_text(encoding="utf-8")
            documents.append({
                "title": _extract_title(content, f.ext),
                "path": str(html_rel),
                "body": body,
            })

    index_dir.mkdir(parents=True, exist_ok=True)
    index_count = build_index(documents, index_dir, ref_labels, rst_index_dirs, dtags_per_doc)

    # Write dtag index
    dtag_index = _build_dtag_index(dtags_per_doc)
    (index_dir / "dtag_index.json").write_text(
        json.dumps(dtag_index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Update ref_labels
    (index_dir / "ref_labels.json").write_text(
        json.dumps(ref_labels, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Update landing page
    _generate_landing_page(html_dir, documents)

    # Update status
    _write_status(
        nm_dir,
        len(files),
        len(ref_labels),
        index_count,
        _unique_dtag_count(dtags_per_doc),
    )
    write_scan_manifest(nm_dir, build_manifest_payload(project_root, files))

    logger.info("已更新: %s", rel_path)


def remove_file(project_root: str | Path, file_path: str | Path) -> None:
    """Handle file deletion: remove HTML and rebuild index."""
    project_root = Path(project_root).resolve()
    file_path = Path(file_path).resolve()

    rel_path = file_path.relative_to(project_root)
    nm_dir = project_root / ".NoteManager"
    html_dir = nm_dir / "html"
    index_dir = html_dir / "_index"

    # Remove the HTML file
    html_rel = rel_path.with_suffix(".html")
    html_file = html_dir / html_rel
    if html_file.exists():
        html_file.unlink()
    logger.info("已删除: %s", html_rel)

    # Re-scan and rebuild index
    files = scan_directory(project_root)
    ref_labels = extract_labels(project_root, files)
    rst_index_dirs = extract_rst_index_directives(files)
    dtags_per_doc = extract_dtags_from_sources(files)

    documents = []
    for f in files:
        html_r = f.rel_path.with_suffix(".html")
        html_p = html_dir / html_r
        if html_p.exists():
            body = html_p.read_text(encoding="utf-8")
            content = f.path.read_text(encoding="utf-8")
            documents.append({
                "title": _extract_title(content, f.ext),
                "path": str(html_r),
                "body": body,
            })

    index_dir.mkdir(parents=True, exist_ok=True)
    index_count = build_index(documents, index_dir, ref_labels, rst_index_dirs, dtags_per_doc)

    (index_dir / "ref_labels.json").write_text(
        json.dumps(ref_labels, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    dtag_index = _build_dtag_index(dtags_per_doc)
    (index_dir / "dtag_index.json").write_text(
        json.dumps(dtag_index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _generate_landing_page(html_dir, documents)
    _write_status(
        nm_dir,
        len(files),
        len(ref_labels),
        index_count,
        _unique_dtag_count(dtags_per_doc),
    )
    write_scan_manifest(nm_dir, build_manifest_payload(project_root, files))


def _render_file(fi: FileInfo) -> dict:
    """Render a single file to HTML body."""
    content = fi.path.read_text(encoding="utf-8")
    if fi.ext == ".rst":
        result = render_rst(content, str(fi.rel_path))
    else:
        result = render_md(content, str(fi.rel_path))
    result["title"] = _extract_title_from_html(result.get("body", "")) or _extract_title(content, fi.ext)
    result["source"] = content
    return result


def _write_html(html_dir, fi, rendered, ref_labels):
    """Resolve references and write a single HTML file."""
    html_rel = fi.rel_path.with_suffix(".html")
    html_path = html_dir / html_rel
    html_path.parent.mkdir(parents=True, exist_ok=True)

    body = resolve_references(rendered["body"], str(html_rel), ref_labels)
    full_html = wrap_html(
        body,
        rendered["title"],
        str(html_rel),
        toc=rendered.get("toc", ""),
        source=rendered.get("source", ""),
        warnings=rendered.get("warnings", []),
        source_path=str(fi.rel_path),
        dtags=rendered.get("dtags", []),
        source_editable=True,
    )
    html_path.write_text(full_html, encoding="utf-8")

    return {
        "title": rendered["title"],
        "path": str(html_rel),
        "body": body,
    }


def _build_dtag_index(all_dtags: dict) -> dict:
    """Build a mapping of tag -> list of HTML paths."""
    index = {}
    for rel_path, tags in all_dtags.items():
        html_path = str(Path(rel_path).with_suffix(".html"))
        for tag in tags:
            index.setdefault(tag, []).append(html_path)
    return index


def _extract_title(content, ext):
    """Extract the first heading from rst or markdown content."""
    import re
    if ext == ".rst":
        lines = content.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("..") or stripped.startswith(":"):
                continue
            if re.match(r'^[=\-~`!@#$%^&*+<>:"#]{3,}$', stripped):
                if i + 1 < len(lines):
                    text = lines[i + 1].strip()
                    if text and not text.startswith("..") and not text.startswith(":"):
                        return text
                continue
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if re.match(r'^[=\-~`!@#$%^&*+<>:"#]{3,}$', next_line):
                    return stripped
            return stripped
    else:
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:]
    return "Untitled"


def _extract_title_from_html(body):
    """Extract the first heading text from rendered HTML body."""
    import re
    m = re.search(r'<h1[^>]*>(.*?)</h1>', body, re.DOTALL)
    if m:
        text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        if text:
            return text
    return None


def _copy_static_assets(static_dir):
    """Copy CSS and JS assets to the static directory."""
    (static_dir / "notemanager.css").write_text(_DEFAULT_CSS, encoding="utf-8")
    (static_dir / "notemanager.js").write_text(_DEFAULT_JS, encoding="utf-8")

    # Copy bundled third-party assets (KaTeX, Mermaid, Highlight.js)
    _ASSETS_SRC = Path(__file__).parent / "renderer" / "assets"
    if _ASSETS_SRC.is_dir():
        import shutil
        for item in _ASSETS_SRC.iterdir():
            if item.is_file():
                shutil.copy2(item, static_dir / item.name)
            elif item.is_dir():
                dest = static_dir / item.name
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(item, dest)


# File extensions to copy as project assets
_ASSET_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico",
    ".pdf", ".zip", ".tar", ".gz",
    ".css", ".js", ".woff", ".woff2", ".ttf", ".eot",
}


def _copy_project_assets(project_root, html_dir):
    """Copy non-document assets (images, etc.) to the html output directory."""
    import shutil
    root = Path(project_root).resolve()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        parts = path.relative_to(root).parts
        if any(part in {".NoteManager", "_build", "_static", "_extensions",
                        "__pycache__", ".git", ".svn", ".hg"} for part in parts):
            continue
        if path.suffix.lower() in _ASSET_EXTENSIONS:
            rel = path.relative_to(root)
            dest = html_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)


def _generate_landing_page(html_dir, documents):
    """Generate a top-level index.html with directory tree navigation."""
    # Build a nested tree from flat document paths
    tree = {}
    for doc in sorted(documents, key=lambda d: d["path"]):
        parts = Path(doc["path"]).parts
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = doc

    tree_html = _render_tree(tree, depth=0)
    body = '<h1>NoteManager</h1>'
    full_html = wrap_html(body, "NoteManager", "index.html", toc=tree_html)
    (html_dir / "index.html").write_text(full_html, encoding="utf-8")


def _render_tree(node, depth):
    """Recursively render a directory tree as nested HTML."""
    parts = []
    parts.append('<ul class="nm-tree">')
    for name, value in node.items():
        if isinstance(value, dict) and "path" not in value:
            # Directory node
            open_attr = ' open' if depth < 1 else ''
            parts.append(f'<li class="nm-dir"><details{open_attr}>')
            parts.append(f'<summary>{name}</summary>')
            parts.append(_render_tree(value, depth + 1))
            parts.append('</details></li>')
        else:
            # File node
            path = value["path"]
            title = value["title"]
            parts.append(f'<li class="nm-file"><a href="{path}">{title}</a></li>')
    parts.append('</ul>')
    return "\n".join(parts)


def _unique_dtag_count(dtags_per_doc: dict[str, list[str]]) -> int:
    """工程内不重复的文档标记（dtag / #标签）个数。"""
    seen: set[str] = set()
    for tags in dtags_per_doc.values():
        for t in tags:
            t = (t or "").strip()
            if t:
                seen.add(t)
    return len(seen)


def _write_status(
    nm_dir,
    file_count,
    label_count,
    index_count=0,
    dtag_count: int = 0,
):
    """Write build status file.

    ``label_count`` 为 reST 交叉引用锚点数量；``dtag_count`` 为文档标记去重后的个数。
    """
    status = {
        "last_build": datetime.datetime.now().isoformat(),
        "file_count": file_count,
        "label_count": label_count,
        "dtag_count": dtag_count,
        "index_count": index_count,
    }
    (nm_dir / "status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


_DEFAULT_CSS = """\
/* NoteManager default stylesheet */
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    margin: 0;
    line-height: 1.6;
    color: #333;
}
.nm-content { max-width: 900px; position: relative; }
pre {
    background: #f5f5f5;
    padding: 12px;
    border-radius: 4px;
    overflow-x: auto;
}
code {
    background: #f5f5f5;
    padding: 2px 4px;
    border-radius: 2px;
}
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
th { background: #f5f5f5; }
.admonition {
    padding: 12px;
    margin: 12px 0;
    border-left: 4px solid #0066cc;
    background: #f0f7ff;
}
.admonition-title { font-weight: bold; margin-bottom: 4px; }
img { max-width: 100%; }

/* Directory tree */
.nm-tree {
    list-style: none;
    padding-left: 0;
}
.nm-tree .nm-tree {
    padding-left: 20px;
}
.nm-tree li {
    margin: 2px 0;
}
.nm-dir > details > summary {
    cursor: pointer;
    font-weight: 500;
    padding: 2px 0;
    list-style: none;
}
.nm-dir > details > summary::before {
    content: "\\1F4C1 ";
}
.nm-dir > details[open] > summary::before {
    content: "\\1F4C2 ";
}
.nm-file a {
    text-decoration: none;
    color: #0066cc;
}
.nm-file a:hover { text-decoration: underline; }
.nm-file::before {
    content: "\\1F4C4 ";
}
"""

_DEFAULT_JS = """\
/* NoteManager sidebar search */
(function() {
    (function removeLegacyFulltextUI() {
        var g = document.getElementById('nm-search-grep');
        if (g) {
            var lb = g.closest('.nm-search-grep-label') || g.closest('label');
            if (lb) lb.remove();
            else g.remove();
        }
        var prog = document.getElementById('nm-grep-progress');
        if (prog) prog.remove();
        var oldRow = document.querySelector('.nm-search-row');
        var inp = document.getElementById('nm-search');
        if (oldRow && inp && oldRow.contains(inp)) {
            var par = oldRow.parentNode;
            var btn = oldRow.querySelector('.nm-search-help');
            par.insertBefore(inp, oldRow);
            if (btn) par.insertBefore(btn, oldRow);
            oldRow.remove();
        } else if (oldRow) {
            oldRow.remove();
        }
    })();

    var searchInput = document.getElementById('nm-search');
    if (!searchInput) return;

    var sidebarToc = document.getElementById('nm-sidebar-toc');
    var sidebarResults = document.getElementById('nm-sidebar-search-results');

    var searchData = null;
    var searchDataPrefix = null;
    var debounceTimer = null;

    var prefix = window.NM_PREFIX || '';

    var TYPE_LABELS = {
        chapter: '章节',
        label: '标签',
        index: '索引',
        dtag: '标记',
        grep: '渲染块'
    };

    function escapeHtml(s) {
        if (!s) return '';
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    function escAttr(s) {
        return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;');
    }

    function showIndexLoadError(err) {
        console.error(err || 'search index load failed');
        if (sidebarToc) sidebarToc.style.display = 'none';
        if (sidebarResults) {
            sidebarResults.innerHTML = '<div class="nm-no-results">无法加载搜索索引（请确认已构建并执行 nmr rebuild）</div>';
            sidebarResults.style.display = 'block';
        }
    }

    function loadIndex() {
        if (searchData && searchDataPrefix === prefix) {
            return Promise.resolve(searchData);
        }
        return fetch(prefix + '_index/search_index.json')
            .then(function(r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then(function(d) {
                searchData = d;
                searchDataPrefix = prefix;
                return d;
            });
    }

    function parseQuery(query) {
        var raw = String(query || '').trim();
        var match = raw.match(/^(chapter|label|index|dtag|grep):\\s*(.*)$/i);
        if (match) {
            var core = (match[2] || '').trim();
            return { type: match[1].toLowerCase(), query: core };
        }
        return { type: null, query: raw };
    }

    function regexSearch(query) {
        var parsed = parseQuery(query);
        if (!searchData || !searchData.documents || !parsed.query) return [];
        var re;
        try {
            re = new RegExp(parsed.query, 'i');
        } catch(e) {
            re = new RegExp(parsed.query.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&'), 'i');
        }
        var results = [];
        var docs = searchData.documents;
        for (var i = 0; i < docs.length; i++) {
            var doc = docs[i];
            if (parsed.type && doc.type !== parsed.type) continue;
            var sn = doc.snippet || '';
            re.lastIndex = 0;
            if (re.test(doc.title || '') || re.test(doc.text || '') || re.test(sn)) {
                results.push(doc);
                if (results.length >= 200) break;
            }
        }
        return results;
    }

    function docHref(path) {
        return prefix + path;
    }

    function typeBadge(type) {
        var label = TYPE_LABELS[type] || type;
        return '<span class="nm-type-badge nm-type-' + type + '">' + label + '</span>';
    }

    function renderResults(query, results) {
        if (!results.length) return '<div class="nm-no-results">无搜索结果</div>';
        var html = '<div class="nm-result-count">找到 ' + results.length + ' 个结果</div>';
        var parsedQuery = parseQuery(query).query;
        for (var i = 0; i < results.length; i++) {
            var doc = results[i];
            var snippet = doc.snippet || '';
            try {
                var rx = new RegExp('(' + parsedQuery + ')', 'gi');
                snippet = escapeHtml(snippet).replace(rx, '<mark>$1</mark>');
            } catch(e) {
                snippet = escapeHtml(snippet);
            }
            html += '<a class="nm-result-item" href="' + escAttr(docHref(doc.path)) + '">' +
                '<div class="nm-result-title">' + typeBadge(doc.type) + escapeHtml(doc.title || '') + '</div>' +
                '<div class="nm-result-path">' + escapeHtml(doc.path || '') + '</div>' +
                '<div class="nm-result-snippet">' + snippet + '</div>' +
                '</a>';
        }
        return html;
    }

    function showSearchResults(query, openFirst) {
        var q = String(query || '').trim();
        var results = regexSearch(q);
        if (openFirst && results.length === 1) {
            window.location.href = docHref(results[0].path);
            return;
        }
        var html = renderResults(q, results);
        if (sidebarToc) sidebarToc.style.display = 'none';
        if (sidebarResults) {
            sidebarResults.innerHTML = html;
            sidebarResults.style.display = 'block';
        }
    }

    function hideSearchResults() {
        if (sidebarToc) sidebarToc.style.display = '';
        if (sidebarResults) {
            sidebarResults.style.display = 'none';
            sidebarResults.innerHTML = '';
        }
    }

    searchInput.addEventListener('input', function() {
        clearTimeout(debounceTimer);
        var query = this.value.trim();
        if (!query) {
            hideSearchResults();
            return;
        }
        debounceTimer = setTimeout(function() {
            loadIndex().then(function() {
                showSearchResults(searchInput.value.trim(), false);
            }).catch(showIndexLoadError);
        }, 200);
    });

    searchInput.addEventListener('keydown', function(e) {
        if (e.key !== 'Enter') return;
        if (e.isComposing) return;
        e.preventDefault();
        var q = searchInput.value.trim();
        if (!q) return;
        clearTimeout(debounceTimer);
        loadIndex().then(function() {
            showSearchResults(q, true);
        }).catch(showIndexLoadError);
    });

    (function() {
        var params = new URLSearchParams(window.location.search);
        var q = params.get('q');
        if (q) {
            searchInput.value = q;
            loadIndex().then(function() {
                showSearchResults(q, true);
            }).catch(showIndexLoadError);
        }
    })();
    (function() {
        var p = window.location.pathname || '';
        if (p.indexOf('/temp/') === 0) {
            var h = document.querySelector('.nm-sidebar-home a');
            if (h) {
                h.addEventListener('click', function(e) {
                    e.preventDefault();
                    window.history.back();
                });
            }
        }
    })();
    document.querySelectorAll('.nm-dtag-badge').forEach(function(badge) {
        badge.addEventListener('click', function() {
            var tag = this.getAttribute('data-dtag');
            if (tag) {
                searchInput.value = 'dtag:' + tag;
                loadIndex().then(function() {
                    showSearchResults('dtag:' + tag, true);
                }).catch(showIndexLoadError);
            }
        });
    });
    (function() {
        var evtSource;
        try { evtSource = new EventSource('/api/events'); } catch(e) { return; }
        evtSource.addEventListener('cli_search', function(e) {
            var data = JSON.parse(e.data);
            if (data.open_url) {
                window.location.href = data.open_url;
                return;
            }
            var query = data.query || '';
            if (!query) return;
            var parts = window.location.pathname.split('/');
            if (parts.length > 2 && parts[1] === 'p') {
                if (data.project !== undefined && parseInt(parts[2], 10) !== data.project) {
                    window.location.href = '/p/' + data.project + '/?q=' + encodeURIComponent(query);
                    return;
                }
            }
            searchInput.value = query;
            clearTimeout(debounceTimer);
            loadIndex().then(function() {
                showSearchResults(query, true);
            }).catch(showIndexLoadError);
            window.focus();
        });
        evtSource.addEventListener('cli_view', function(e) {
            var data = JSON.parse(e.data);
            if (!data || !data.url) return;
            window.location.href = data.url;
        });
        evtSource.addEventListener('build_complete', function(e) {
            var data;
            try { data = JSON.parse(e.data); } catch (err) { return; }
            if (data.project === undefined || data.project === null) return;
            var parts = window.location.pathname.split('/').filter(function(s) { return s; });
            if (parts.length < 2 || parts[0] !== 'p') return;
            var cur = parseInt(parts[1], 10);
            if (isNaN(cur) || cur !== data.project) return;
            window.location.reload();
        });
    })();
})();
"""
