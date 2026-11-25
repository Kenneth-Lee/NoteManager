"""Export a project to a standalone .html.zip for mobile readers (no .NoteManager/html reuse)."""

from __future__ import annotations

import io
import json
import logging
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

from notemanager.builder import (
    _build_dtag_index,
    _merge_dtag_lists,
    _render_file,
    _render_tree,
)
from notemanager.indexer import (
    build_index,
    extract_dtags_from_sources,
    extract_rst_index_directives,
)
from notemanager.ref_resolver import extract_labels, resolve_references
from notemanager.renderer.export_template import (
    wrap_export_index_html,
    wrap_export_note_html,
)
from notemanager.scanner import scan_directory, is_ignored_path, load_ignore_rules

logger = logging.getLogger(__name__)

_ASSETS_DIR = Path(__file__).parent / "renderer" / "assets"

_EXPORT_COPY_FILES = frozenset(
    {
        "katex.min.css",
        "katex.min.js",
        "katex-auto-render.min.js",
        "mermaid.min.js",
        "export.css",
        "export.js",
    }
)


def _copy_export_static(static_dir: Path) -> None:
    static_dir.mkdir(parents=True, exist_ok=True)
    if not _ASSETS_DIR.is_dir():
        return
    for name in _EXPORT_COPY_FILES:
        src = _ASSETS_DIR / name
        if src.is_file():
            shutil.copy2(src, static_dir / name)
    fonts = _ASSETS_DIR / "katex-fonts"
    if fonts.is_dir():
        dest = static_dir / "katex-fonts"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(fonts, dest)


# File extensions mirrored like builder._copy_project_assets
_ASSET_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico",
    ".pdf", ".zip", ".tar", ".gz",
    ".css", ".js", ".woff", ".woff2", ".ttf", ".eot",
}


def _copy_project_assets(project_root: Path, html_dir: Path) -> None:
    root = project_root.resolve()
    rules = load_ignore_rules(root)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if is_ignored_path(root, path, rules=rules):
            continue
        parts = path.relative_to(root).parts
        if any(
            part in {".NoteManager", "_build", "_static", "_extensions",
                      "__pycache__", ".git", ".svn", ".hg"}
            for part in parts
        ):
            continue
        if path.suffix.lower() in _ASSET_EXTENSIONS:
            rel = path.relative_to(root)
            dest = html_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)


def _build_landing_tree(documents: list[dict]) -> str:
    tree: dict = {}
    for doc in sorted(documents, key=lambda d: d["path"]):
        parts = Path(doc["path"]).parts
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = doc
    return _render_tree(tree, 0)


def _write_export_pages(
    project_root: Path,
    html_dir: Path,
    progress: Callable[[str], None] | None,
) -> None:
    def p(msg: str) -> None:
        if progress:
            progress(msg)

    files = scan_directory(project_root)
    n = len(files)
    p(f"  已扫描 {n} 个源文件")

    rendered = {fi.rel_path: _render_file(fi) for fi in files}
    if n:
        p(f"  语法转换完成 ({n} 个文件)")

    p("  解析标签与交叉引用…")
    ref_labels, ref_candidates = extract_labels(
        project_root, files, with_candidates=True
    )
    rst_index_dirs = extract_rst_index_directives(files)
    dtags_extracted = extract_dtags_from_sources(files)

    documents: list[dict] = []
    all_dtags: dict[str, list[str]] = {}

    for fi in files:
        r = rendered[fi.rel_path]
        key = str(fi.rel_path)
        merged = _merge_dtag_lists(dtags_extracted.get(key), r.get("dtags"))
        if merged:
            all_dtags[key] = merged
        r_out = {**r, "dtags": merged}
        html_rel = fi.rel_path.with_suffix(".html")
        body = resolve_references(
            r_out["body"], str(html_rel), ref_labels, ref_candidates,
        )
        full = wrap_export_note_html(
            body,
            r_out["title"],
            str(html_rel),
            toc=r_out.get("toc", "") or "",
        )
        out_path = html_dir / html_rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(full, encoding="utf-8")
        documents.append(
            {
                "title": r_out["title"],
                "path": str(html_rel),
                "source_path": str(fi.rel_path),
                "body": body,
            }
        )

    index_dir = html_dir / "_index"
    index_dir.mkdir(parents=True, exist_ok=True)

    p("  构建搜索索引…")
    build_index(
        documents,
        index_dir,
        ref_labels,
        rst_index_dirs,
        all_dtags,
    )
    (index_dir / "ref_labels.json").write_text(
        json.dumps(ref_labels, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    dtag_index = _build_dtag_index(all_dtags)
    (index_dir / "dtag_index.json").write_text(
        json.dumps(dtag_index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    static_dir = html_dir / "_static"
    p("  写入导出静态资源…")
    _copy_export_static(static_dir)
    p("  同步工程内图片等资源…")
    _copy_project_assets(project_root, html_dir)

    tree_html = _build_landing_tree(documents)
    landing = wrap_export_index_html(project_root.name, tree_html)
    (html_dir / "index.html").write_text(landing, encoding="utf-8")


def _zip_html_dir(html_dir: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(html_dir.rglob("*")):
            if f.is_file():
                arc = f.relative_to(html_dir).as_posix()
                zf.write(f, arcname=arc)


def _zip_html_dir_to_bytes(html_dir: Path) -> bytes:
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(html_dir.rglob("*")):
            if f.is_file():
                arc = f.relative_to(html_dir).as_posix()
                zf.write(f, arcname=arc)
    return bio.getvalue()


def export_mobile_html_zip(
    project_root: str | Path,
    output_path: Path | None = None,
    *,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Render into a temp tree and write ``<项目名>.html.zip``.

    Does not read or write ``工程/.NoteManager/html``.
    """
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"工程目录不存在: {root}")

    out = output_path or (Path.cwd() / f"{root.name}.html.zip")
    out = Path(out).expanduser().resolve()

    with tempfile.TemporaryDirectory(prefix="nm-export-") as td:
        html_dir = Path(td)
        html_dir.mkdir(parents=True, exist_ok=True)
        _write_export_pages(root, html_dir, progress)
        _zip_html_dir(html_dir, out)

    logger.info("已导出: %s -> %s", root, out)
    return out


def export_mobile_html_zip_bytes(
    project_root: str | Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> tuple[bytes, str]:
    """Same as export zip but return bytes and suggested filename ``name.html.zip``."""
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"工程目录不存在: {root}")
    with tempfile.TemporaryDirectory(prefix="nm-export-") as td:
        html_dir = Path(td)
        html_dir.mkdir(parents=True, exist_ok=True)
        _write_export_pages(root, html_dir, progress)
        data = _zip_html_dir_to_bytes(html_dir)
    return data, f"{root.name}.html.zip"
