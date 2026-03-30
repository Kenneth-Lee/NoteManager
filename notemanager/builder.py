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
from notemanager.indexer import build_index

logger = logging.getLogger(__name__)


def full_build(project_root: str | Path) -> Path:
    """Full build: scan, render all files, rebuild index.

    Returns the .NoteManager directory path.
    """
    project_root = Path(project_root).resolve()
    files = scan_directory(project_root)

    nm_dir = project_root / ".NoteManager"
    html_dir = nm_dir / "html"
    static_dir = nm_dir / "static"
    index_dir = nm_dir / "index"

    for d in (html_dir, static_dir, index_dir):
        d.mkdir(parents=True, exist_ok=True)

    # First pass: render all files
    rendered = {}
    for fi in files:
        rendered[fi.rel_path] = _render_file(fi)

    # Extract labels
    ref_labels = extract_labels(project_root, files)

    # Second pass: resolve references and write HTML
    documents = []
    for fi in files:
        doc = _write_html(html_dir, fi, rendered[fi.rel_path], ref_labels)
        documents.append(doc)

    # Build search index
    build_index(documents, index_dir)

    # Write ref_labels
    (index_dir / "ref_labels.json").write_text(
        json.dumps(ref_labels, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Copy static assets
    _copy_static_assets(static_dir)

    # Generate landing page
    _generate_landing_page(html_dir, documents)

    # Write status
    _write_status(nm_dir, len(files), len(ref_labels))

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
    index_dir = nm_dir / "index"

    if not nm_dir.exists():
        # No build yet, do a full build instead
        full_build(project_root)
        return

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

    # Write this file's HTML
    _write_html(html_dir, fi, rendered, ref_labels)

    # Rebuild full index (simple approach - correct and fast enough for now)
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

    build_index(documents, index_dir)

    # Update ref_labels
    (index_dir / "ref_labels.json").write_text(
        json.dumps(ref_labels, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Update landing page
    _generate_landing_page(html_dir, documents)

    # Update status
    _write_status(nm_dir, len(files), len(ref_labels))

    logger.info("已更新: %s", rel_path)


def remove_file(project_root: str | Path, file_path: str | Path) -> None:
    """Handle file deletion: remove HTML and rebuild index."""
    project_root = Path(project_root).resolve()
    file_path = Path(file_path).resolve()

    rel_path = file_path.relative_to(project_root)
    nm_dir = project_root / ".NoteManager"
    html_dir = nm_dir / "html"

    # Remove the HTML file
    html_rel = rel_path.with_suffix(".html")
    html_file = html_dir / html_rel
    if html_file.exists():
        html_file.unlink()
    logger.info("已删除: %s", html_rel)

    # Re-scan and rebuild index
    files = scan_directory(project_root)
    ref_labels = extract_labels(project_root, files)

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

    index_dir = nm_dir / "index"
    build_index(documents, index_dir)

    (index_dir / "ref_labels.json").write_text(
        json.dumps(ref_labels, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _generate_landing_page(html_dir, documents)
    _write_status(nm_dir, len(files), len(ref_labels))


def _render_file(fi: FileInfo) -> dict:
    """Render a single file to HTML body."""
    content = fi.path.read_text(encoding="utf-8")
    if fi.ext == ".rst":
        body = render_rst(content, str(fi.rel_path))
    else:
        body = render_md(content, str(fi.rel_path))
    return {
        "body": body,
        "title": _extract_title(content, fi.ext),
    }


def _write_html(html_dir, fi, rendered, ref_labels):
    """Resolve references and write a single HTML file."""
    html_rel = fi.rel_path.with_suffix(".html")
    html_path = html_dir / html_rel
    html_path.parent.mkdir(parents=True, exist_ok=True)

    body = resolve_references(rendered["body"], str(html_rel), ref_labels)
    full_html = wrap_html(body, rendered["title"], str(html_rel))
    html_path.write_text(full_html, encoding="utf-8")

    return {
        "title": rendered["title"],
        "path": str(html_rel),
        "body": body,
    }


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


def _copy_static_assets(static_dir):
    """Copy CSS and JS assets to the static directory."""
    css = static_dir / "notemanager.css"
    if not css.exists():
        css.write_text(_DEFAULT_CSS, encoding="utf-8")
    js = static_dir / "notemanager.js"
    if not js.exists():
        js.write_text(_DEFAULT_JS, encoding="utf-8")


def _generate_landing_page(html_dir, documents):
    """Generate a top-level index.html listing all documents."""
    body_parts = ["<h1>NoteManager</h1>", "<ul>"]
    for doc in sorted(documents, key=lambda d: d["path"]):
        path = doc["path"]
        title = doc["title"]
        body_parts.append(f'<li><a href="{path}">{title}</a> — <code>{path}</code></li>')
    body_parts.append("</ul>")
    body = "\n".join(body_parts)
    full_html = wrap_html(body, "NoteManager", "index.html")
    (html_dir / "index.html").write_text(full_html, encoding="utf-8")


def _write_status(nm_dir, file_count, label_count):
    """Write build status file."""
    status = {
        "last_build": datetime.datetime.now().isoformat(),
        "file_count": file_count,
        "label_count": label_count,
    }
    (nm_dir / "status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


_DEFAULT_CSS = """\
/* NoteManager default stylesheet */
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    max-width: 900px;
    margin: 0 auto;
    padding: 20px;
    line-height: 1.6;
    color: #333;
}
.nm-nav {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 8px 0;
    border-bottom: 1px solid #ddd;
    margin-bottom: 20px;
}
.nm-nav a { text-decoration: none; color: #0066cc; }
.nm-nav a:hover { text-decoration: underline; }
#nm-search {
    padding: 4px 8px;
    border: 1px solid #ccc;
    border-radius: 4px;
    width: 200px;
}
.nm-content { max-width: 100%; }
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
#nm-search-results {
    position: absolute;
    background: white;
    border: 1px solid #ccc;
    max-height: 300px;
    overflow-y: auto;
    display: none;
    z-index: 100;
    width: 400px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.15);
}
#nm-search-results a {
    display: block;
    padding: 8px;
    text-decoration: none;
    color: #333;
    border-bottom: 1px solid #eee;
}
#nm-search-results a:hover { background: #f0f7ff; }
img { max-width: 100%; }
"""

_DEFAULT_JS = """\
/* NoteManager search functionality */
(function() {
    var searchInput = document.getElementById('nm-search');
    var resultsDiv = document.getElementById('nm-search-results');
    if (!searchInput || !resultsDiv) return;

    var searchData = null;
    var debounceTimer = null;

    function loadIndex() {
        if (searchData) return Promise.resolve(searchData);
        var pathParts = window.location.pathname.split('/');
        var depth = pathParts.length - 2;
        var prefix = '';
        for (var i = 0; i < depth; i++) prefix += '../';
        return fetch(prefix + 'index/search_index.json')
            .then(function(r) { return r.json(); })
            .then(function(d) { searchData = d; return d; });
    }

    function search(query) {
        if (!searchData || !query) return [];
        var q = query.toLowerCase();
        var results = [];
        for (var i = 0; i < searchData.documents.length; i++) {
            var doc = searchData.documents[i];
            if (doc.title.toLowerCase().indexOf(q) >= 0 ||
                doc.text.toLowerCase().indexOf(q) >= 0) {
                results.push(doc);
                if (results.length >= 20) break;
            }
        }
        return results;
    }

    function getPath(doc) {
        var pathParts = window.location.pathname.split('/');
        var depth = pathParts.length - 2;
        var prefix = '';
        for (var i = 0; i < depth; i++) prefix += '../';
        return prefix + doc.path;
    }

    searchInput.addEventListener('input', function() {
        clearTimeout(debounceTimer);
        var query = this.value.trim();
        if (!query) { resultsDiv.style.display = 'none'; return; }
        debounceTimer = setTimeout(function() {
            loadIndex().then(function() {
                var results = search(query);
                if (!results.length) {
                    resultsDiv.innerHTML = '<div style="padding:8px">无搜索结果</div>';
                } else {
                    var html = '';
                    for (var i = 0; i < results.length; i++) {
                        html += '<a href="' + getPath(results[i]) + '">' +
                                results[i].title + '</a>';
                    }
                    resultsDiv.innerHTML = html;
                }
                resultsDiv.style.display = 'block';
            });
        }, 300);
    });

    document.addEventListener('click', function(e) {
        if (!resultsDiv.contains(e.target) && e.target !== searchInput) {
            resultsDiv.style.display = 'none';
        }
    });
})();
"""
