"""Build engine for NoteManager.

Provides both full-build and incremental single-file update capabilities.
"""

import json
import logging
import datetime
from pathlib import Path

from notemanager.scanner import scan_directory, FileInfo
from notemanager.renderer.rst_renderer import render_rst
from notemanager.renderer.md_renderer import render_md
from notemanager.renderer.template import wrap_html
from notemanager.ref_resolver import extract_labels, resolve_references
from notemanager.indexer import build_index, extract_rst_index_directives
from notemanager.indexer import extract_dtags_from_sources

logger = logging.getLogger(__name__)


def full_build(project_root: str | Path) -> Path:
    """Full build: scan, render all files, rebuild index.

    Returns the .NoteManager directory path.
    """
    project_root = Path(project_root).resolve()
    files = scan_directory(project_root)

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

    # Extract labels
    ref_labels = extract_labels(project_root, files)

    # Extract .. index:: directives from RST sources
    rst_index_dirs = extract_rst_index_directives(files)

    # Second pass: resolve references and write HTML
    documents = []
    all_dtags = {}  # rel_path -> list of tag strings
    for fi in files:
        r = rendered[fi.rel_path]
        doc = _write_html(html_dir, fi, r, ref_labels)
        documents.append(doc)
        if r.get("dtags"):
            all_dtags[str(fi.rel_path)] = r["dtags"]

    # Build dtag index (tag -> list of html paths)
    dtag_index = _build_dtag_index(all_dtags)

    # Build search index (includes labels and index directives)
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
    _copy_static_assets(static_dir)

    # Copy project assets (images, etc.) to html directory
    _copy_project_assets(project_root, html_dir)

    # Generate landing page
    _generate_landing_page(html_dir, documents)

    # Write status
    _write_status(nm_dir, len(files), len(ref_labels), index_count)

    return nm_dir


def update_file(project_root: str | Path, file_path: str | Path) -> None:
    """Incrementally update a single file.

    Re-renders the file, rebuilds the full index and ref_labels
    (since labels may have changed).
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

    # Refresh static assets (JS/CSS may have been updated)
    static_dir.mkdir(parents=True, exist_ok=True)
    _copy_static_assets(static_dir)
    _copy_project_assets(project_root, html_dir)

    # Re-scan to get current file list
    files = scan_directory(project_root)

    # Re-render just this file
    fi = FileInfo(
        path=file_path,
        rel_path=rel_path,
        ext=ext,
        mtime=file_path.stat().st_mtime,
    )
    rendered = _render_file(fi)

    # Re-extract all labels (this file may have changed labels)
    ref_labels = extract_labels(project_root, files)

    # Re-extract .. index:: directives
    rst_index_dirs = extract_rst_index_directives(files)

    # Re-extract dtags from source
    dtags_per_doc = extract_dtags_from_sources(files)

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
    _write_status(nm_dir, len(files), len(ref_labels), index_count)

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

    _generate_landing_page(html_dir, documents)
    _write_status(nm_dir, len(files), len(ref_labels), index_count)


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


def _write_status(nm_dir, file_count, label_count, index_count=0):
    """Write build status file."""
    status = {
        "last_build": datetime.datetime.now().isoformat(),
        "file_count": file_count,
        "label_count": label_count,
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
    var searchInput = document.getElementById('nm-search');
    if (!searchInput) return;

    var sidebarToc = document.getElementById('nm-sidebar-toc');
    var sidebarResults = document.getElementById('nm-sidebar-search-results');

    var searchData = null;
    var debounceTimer = null;

    var prefix = window.NM_PREFIX || '';

    var TYPE_LABELS = {
        chapter: '章节',
        label: '标签',
        index: '索引',
        dtag: '标记'
    };

    function loadIndex() {
        if (searchData) return Promise.resolve(searchData);
        return fetch(prefix + '_index/search_index.json')
            .then(function(r) { return r.json(); })
            .then(function(d) { searchData = d; return d; });
    }

    function parseQuery(query) {
        var match = query.match(/^(chapter|label|index|dtag):(.+)$/i);
        if (match) return { type: match[1].toLowerCase(), query: match[2] };
        return { type: null, query: query };
    }

    function regexSearch(query) {
        var parsed = parseQuery(query);
        if (!searchData || !parsed.query) return [];
        var re;
        try {
            re = new RegExp(parsed.query, 'i');
        } catch(e) {
            re = new RegExp(parsed.query.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&'), 'i');
        }
        var results = [];
        for (var i = 0; i < searchData.documents.length; i++) {
            var doc = searchData.documents[i];
            if (parsed.type && doc.type !== parsed.type) continue;
            if (re.test(doc.title) || re.test(doc.text)) {
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
                var re = new RegExp('(' + parsedQuery + ')', 'gi');
                snippet = snippet.replace(re, '<mark>$1</mark>');
            } catch(e) {}
            html += '<a class="nm-result-item" href="' + docHref(doc.path) + '">' +
                '<div class="nm-result-title">' + typeBadge(doc.type) + doc.title + '</div>' +
                '<div class="nm-result-path">' + doc.path + '</div>' +
                '<div class="nm-result-snippet">' + snippet + '</div>' +
                '</a>';
        }
        return html;
    }

    function showSearchResults(query) {
        var results = regexSearch(query);
        var html = renderResults(query, results);
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
                showSearchResults(query);
            });
        }, 200);
    });

    // Handle URL query parameters
    (function() {
        var params = new URLSearchParams(window.location.search);
        var q = params.get('q');
        if (q) {
            searchInput.value = q;
            loadIndex().then(function() {
                showSearchResults(q);
            });
        }
    })();
    // Show "global" anchor only when served by daemon /p/* route.
    (function() {
        var anchor = document.getElementById('nm-global-anchor');
        if (!anchor) return;
        var p = window.location.pathname || '';
        if (p.indexOf('/p/') === 0) {
            anchor.style.display = 'inline-block';
        }
    })();
    // Dtag badge click: search for that tag
    document.querySelectorAll('.nm-dtag-badge').forEach(function(badge) {
        badge.addEventListener('click', function() {
            var tag = this.getAttribute('data-dtag');
            if (tag) {
                searchInput.value = 'dtag:' + tag;
                loadIndex().then(function() {
                    showSearchResults('dtag:' + tag);
                });
            }
        });
    });
    // SSE: listen for CLI-triggered searches
    (function() {
        var evtSource;
        try { evtSource = new EventSource('/api/events'); } catch(e) { return; }
        evtSource.addEventListener('cli_search', function(e) {
            var data = JSON.parse(e.data);
            var query = data.query || '';
            if (!query) return;
            var parts = window.location.pathname.split('/');
            if (parts.length > 2 && parts[1] === 'p') {
                if (data.project !== undefined && parseInt(parts[2]) !== data.project) {
                    window.location.href = '/p/' + data.project + '/?q=' + encodeURIComponent(query);
                    return;
                }
            }
            searchInput.value = query;
            clearTimeout(debounceTimer);
            loadIndex().then(function() {
                showSearchResults(query);
            }).catch(function(err) {
                console.error('CLI search failed:', err);
            });
            window.focus();
        });
    })();
})();
"""
