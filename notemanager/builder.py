"""Build engine for NoteManager.

Provides both full-build and incremental single-file update capabilities.
"""

import hashlib
import json
import logging
import datetime
from collections.abc import Callable
from dataclasses import dataclass
from html import escape as html_escape
from pathlib import Path

from notemanager.scanner import scan_directory, FileInfo, load_ignore_rules
from notemanager.scan_manifest import build_manifest_payload, write_scan_manifest
from notemanager.renderer.rst_renderer import render_rst, _rst_clean_title_text
from notemanager.renderer.md_renderer import render_md, _md_collect_headings
from notemanager.renderer.txt_renderer import render_txt
from notemanager.renderer.template import wrap_html
from notemanager.ref_resolver import extract_labels, resolve_references
from notemanager.index_body import extract_render_panel_inner_html
from notemanager.indexer import (
    build_index,
    extract_rst_index_directives,
    extract_rst_title_index_roles,
)
from notemanager.indexer import extract_dtags_from_sources
from notemanager.scanner import is_ignored_path
from notemanager.web_client_js import CLIENT_BOOTSTRAP_JS
from notemanager import overview as overview_mod
from notemanager import relations as relations_mod
from notemanager import activity as activity_mod
from notemanager import assets as assets_mod

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UiPluginBundle:
    css_files: list[str]
    js_files: list[str]


def _load_ui_plugin_bundle(project_root: Path) -> UiPluginBundle:
    """Load optional UI plugin config from project root .nmrui.json.

    Supported config (minimal):
      {"plugin": "modern"}
      {"enabled": false}
    """
    cfg_file = project_root / ".nmrui.json"
    if not cfg_file.is_file():
        return UiPluginBundle(css_files=[], js_files=[])

    try:
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("UI 插件配置读取失败，忽略: %s", cfg_file)
        return UiPluginBundle(css_files=[], js_files=[])

    if not isinstance(data, dict):
        return UiPluginBundle(css_files=[], js_files=[])
    if data.get("enabled", True) is False:
        return UiPluginBundle(css_files=[], js_files=[])

    plugin = str(data.get("plugin", "")).strip().lower()
    if plugin == "modern":
        return UiPluginBundle(css_files=["notemanager-theme-modern.css"], js_files=[])

    return UiPluginBundle(css_files=[], js_files=[])


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


def _add_output_conflict_warnings(files, rendered: dict) -> None:
    """Warn when two source formats would overwrite the same HTML path."""
    groups: dict[str, list] = {}
    for fi in files:
        html_rel = str(fi.rel_path.with_suffix(".html"))
        groups.setdefault(html_rel, []).append(fi.rel_path)
    for html_rel, sources in groups.items():
        if len(sources) < 2:
            continue
        names = ", ".join(str(x) for x in sources)
        msg = f"输出路径冲突: {html_rel} 由多个源文件生成 ({names})，请重命名其中一个"
        for source in sources:
            item = rendered.get(source)
            if item is not None:
                item.setdefault("warnings", []).append(msg)


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
    ui_plugin = _load_ui_plugin_bundle(project_root)
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
    _add_output_conflict_warnings(files, rendered)

    # Extract labels
    p("  解析标签、交叉引用与 index 指令…")
    ref_labels, ref_candidates = extract_labels(
        project_root, files, with_candidates=True
    )

    # Extract .. index:: directives from RST sources
    rst_index_dirs = extract_rst_index_directives(files)
    rst_title_index_roles = extract_rst_title_index_roles(files)

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
        doc = _write_html(
            html_dir, fi, r_out, ref_labels, ui_plugin, ref_candidates
        )
        documents.append(doc)
        k = j + 1
        if n and (k == n or k <= 2 or k - last_report >= step):
            p(f"  生成 HTML {k}/{n} {fi.rel_path}")
            last_report = k

    # Build dtag index (tag -> list of html paths)
    dtag_index = _build_dtag_index(all_dtags)

    # Build search index (includes labels and index directives)
    p("  构建搜索索引…")
    index_count = build_index(
        documents, index_dir, ref_labels, rst_index_dirs, all_dtags,
        rst_title_index_roles,
    )

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

    # Build backlinks / related documents for the per-page panel.
    _build_relations_safe(html_dir, documents)
    _build_activity_safe(project_root, html_dir, documents)
    _build_assets_safe(project_root, html_dir, documents)

    # Copy static assets
    p("  写入 _static（notemanager.js / css 等）…")
    _copy_static_assets(static_dir)

    # Copy project assets (images, etc.) to html directory
    _copy_project_assets(project_root, html_dir)

    # Generate the project overview/topic map (best effort).
    overview_data = _build_overview_safe(project_root, html_dir, documents)

    # Generate landing page
    p("  生成首页与状态…")
    _generate_landing_page(html_dir, documents, ui_plugin, overview_data)

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
    refresh_project_assets: bool = False,
) -> None:
    """Incrementally update a single file.

    Re-renders the file, rebuilds the full index and ref_labels
    (since labels may have changed).

    Args:
        refresh_static_assets: 为 False 时跳过将 _static（含 CodeMirror 等）拷入 html；
            Web 保存源码时设为 False 可明显缩短耗时。文件监控触发的更新仍应用默认 True。
        refresh_project_assets: 为 True 时同步工程内静态资源（图片等）到 html 目录。
            仅源码文本更新时通常不需要，默认关闭以减少全目录遍历。
    """
    project_root = Path(project_root).resolve()
    ui_plugin = _load_ui_plugin_bundle(project_root)
    file_path = Path(file_path).resolve()

    rel_path = file_path.relative_to(project_root)
    ext = file_path.suffix.lower()

    if ext not in (".rst", ".md", ".txt"):
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
    if refresh_project_assets:
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
    ref_labels, ref_candidates = extract_labels(
        project_root, files, with_candidates=True
    )

    # Re-extract .. index:: directives
    rst_index_dirs = extract_rst_index_directives(files)
    rst_title_index_roles = extract_rst_title_index_roles(files)

    # Re-extract dtags from source
    dtags_per_doc = extract_dtags_from_sources(files)

    # 侧栏徽章与索引一致：合并源码抽取与渲染结果
    merged = _merge_dtag_lists(dtags_per_doc.get(str(rel_path)), rendered.get("dtags"))
    rendered = {**rendered, "dtags": merged}

    # Write this file's HTML
    _write_html(
        html_dir, fi, rendered, ref_labels, ui_plugin, ref_candidates
    )

    # Rebuild full index
    documents = []
    for f in files:
        html_rel = f.rel_path.with_suffix(".html")
        html_path = html_dir / html_rel
        if html_path.exists():
            body = html_path.read_text(encoding="utf-8")
            title = _document_list_title(f, body)
            documents.append({
                "title": title,
                "path": str(html_rel),
                "source_path": str(f.rel_path),
                "body": body,
            })

    index_dir.mkdir(parents=True, exist_ok=True)
    index_count = build_index(
        documents, index_dir, ref_labels, rst_index_dirs, dtags_per_doc,
        rst_title_index_roles,
    )

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

    _build_relations_safe(html_dir, documents)
    _build_activity_safe(project_root, html_dir, documents)
    _build_assets_safe(project_root, html_dir, documents)

    # Update landing page
    overview_data = _build_overview_safe(project_root, html_dir, documents)
    _generate_landing_page(html_dir, documents, ui_plugin, overview_data)

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

    ui_plugin = _load_ui_plugin_bundle(project_root)

    # Re-scan and rebuild index
    files = scan_directory(project_root)
    ref_labels, ref_candidates = extract_labels(
        project_root, files, with_candidates=True
    )
    rst_index_dirs = extract_rst_index_directives(files)
    rst_title_index_roles = extract_rst_title_index_roles(files)
    dtags_per_doc = extract_dtags_from_sources(files)

    documents = []
    for f in files:
        html_r = f.rel_path.with_suffix(".html")
        html_p = html_dir / html_r
        if html_p.exists():
            body = html_p.read_text(encoding="utf-8")
            title = _document_list_title(f, body)
            documents.append({
                "title": title,
                "path": str(html_r),
                "source_path": str(f.rel_path),
                "body": body,
            })

    index_dir.mkdir(parents=True, exist_ok=True)
    index_count = build_index(
        documents, index_dir, ref_labels, rst_index_dirs, dtags_per_doc,
        rst_title_index_roles,
    )

    (index_dir / "ref_labels.json").write_text(
        json.dumps(ref_labels, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    dtag_index = _build_dtag_index(dtags_per_doc)
    (index_dir / "dtag_index.json").write_text(
        json.dumps(dtag_index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _build_relations_safe(html_dir, documents)
    _build_activity_safe(project_root, html_dir, documents)
    _build_assets_safe(project_root, html_dir, documents)

    overview_data = _build_overview_safe(project_root, html_dir, documents)
    _generate_landing_page(html_dir, documents, ui_plugin, overview_data)
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
    elif fi.ext == ".txt":
        result = render_txt(content, str(fi.rel_path))
    else:
        result = render_md(content, str(fi.rel_path))
    result["title"] = _resolve_page_title(content, fi, result.get("body", ""))
    result["source"] = content
    return result


def _write_html(html_dir, fi, rendered, ref_labels, ui_plugin: UiPluginBundle,
                ref_candidates=None):
    """Resolve references and write a single HTML file."""
    html_rel = fi.rel_path.with_suffix(".html")
    html_path = html_dir / html_rel
    html_path.parent.mkdir(parents=True, exist_ok=True)

    body = resolve_references(
        rendered["body"], str(html_rel), ref_labels, ref_candidates
    )
    full_html = wrap_html(
        body,
        rendered["title"],
        str(html_rel),
        toc=rendered.get("toc", ""),
        source=rendered.get("source", ""),
        warnings=rendered.get("warnings", []),
        source_path=str(fi.rel_path),
        source_abs_path=str(fi.path),
        dtags=rendered.get("dtags", []),
        source_editable=True,
        heading_lines=rendered.get("heading_lines"),
        extra_css_files=ui_plugin.css_files,
        extra_js_files=ui_plugin.js_files,
        project_id=_project_id(html_dir.parent.parent),
        project_name=Path(html_dir.parent.parent).name,
    )
    html_path.write_text(full_html, encoding="utf-8")

    return {
        "title": rendered["title"],
        "path": str(html_rel),
        "source_path": str(fi.rel_path),
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


def _rst_line_is_adornment(line: str) -> str | None:
    """若整行由同一非标点以外的重复符号组成（docutils 节装饰线），返回该符号。"""
    s = line.strip()
    if len(s) < 2:
        return None
    c = s[0]
    if c.isalnum():
        return None
    if all(ch == c for ch in s):
        return c
    return None


def _rst_collect_headings(lines: list[str]) -> list[tuple[str, str, int]]:
    """收集 RST 过渡标题：(标题文本, 装饰字符, 标题行行号)。含上划线+下划线形式。"""
    import re
    headings: list[tuple[str, str, int]] = []
    n = len(lines)
    i = 0
    while i < n:
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith(".."):
            i += 1
            continue
        # Skip RST field lists like ":Authors: Name" but keep role-markup
        # titles like ":index:`bash` 技巧" (no whitespace after second colon).
        if stripped.startswith(":") and re.match(r"^:[A-Za-z][\w-]*:\s+", stripped):
            i += 1
            continue

        adorn_top = _rst_line_is_adornment(lines[i])
        if adorn_top is not None and i + 2 < n:
            mid = lines[i + 1].strip()
            bot = _rst_line_is_adornment(lines[i + 2])
            if (
                mid
                and not mid.startswith("..")
                and _rst_line_is_adornment(lines[i + 1]) is None
                and bot == adorn_top
            ):
                headings.append((mid, adorn_top, i + 1))
                i += 3
                continue

        if _rst_line_is_adornment(lines[i]) is not None:
            i += 1
            continue

        if i + 1 < n:
            adorn_next = _rst_line_is_adornment(lines[i + 1])
            if adorn_next is not None:
                headings.append((stripped, adorn_next, i))
                i += 2
                continue

        i += 1

    return headings


def _rst_pick_document_title(
    headings: list[tuple[str, str, int]], stem: str = ""
) -> str | None:
    """在收集到的节标题中选「文档标题」。

    以前的规则偏好 ``=`` 装饰，但你的笔记里顶层标题可能用 ``*`` 等其它装饰符，
    且标题行可能以 ``:index:`` 开头。

    这里改为：按装饰符的首次出现顺序推断层级，取“最顶层”（装饰符层级最小）的标题。
    同时对 ``:index:`...` `` 等内容做清洗后再返回。
    """
    if not headings:
        return None

    # Determine adornment rank by first appearance in the source.
    ordered = sorted(headings, key=lambda h: h[2])
    distinct: list[str] = []
    seen: set[str] = set()
    for _text, ch, _ln in ordered:
        if ch and ch not in seen:
            distinct.append(ch)
            seen.add(ch)

    rank = {ch: i for i, ch in enumerate(distinct)}  # smaller => higher level
    min_rank = min(rank.get(ch, 10**9) for _text, ch, _ln in ordered)
    picked = min(
        (h for h in ordered if rank.get(h[1], 10**9) == min_rank),
        key=lambda h: h[2],
    )

    cleaned = _rst_clean_title_text(picked[0])
    stem_key = (stem or "").strip().lower()
    if stem_key and cleaned.lower().startswith(stem_key):
        return stem
    return cleaned or picked[0]


def _md_strip_yaml_frontmatter(content: str) -> str:
    """去掉开头的 YAML frontmatter（``---`` … ``---``），避免其中的 ``#``/``---`` 干扰标题解析。"""
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return content
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1 :])
    return content


def _md_pick_document_title(headings: list[tuple[int, int, str]]) -> str | None:
    """取「文档级」标题：全篇最低大纲级别中、最先出现的那一个（故 ``##`` 在前的先导小节
    不会压过后面出现的 ``#`` 一级标题）。
    """
    if not headings:
        return None
    min_lv = min(h[0] for h in headings)
    for level, _ln, text in headings:
        if level == min_lv:
            return text
    return None


def _extract_title(content, ext, stem: str = ""):
    """从源码提取用于侧栏/索引的文档标题（非任意首段正文）。

    ``stem``：源文件名不含扩展名；仅 RST 用于与节名对齐（见 `_rst_pick_document_title`）。
    """
    if ext == ".rst":
        lines = content.split("\n")
        picked = _rst_pick_document_title(_rst_collect_headings(lines), stem)
        return picked if picked else "Untitled"
    if ext == ".md":
        raw = _md_strip_yaml_frontmatter(content)
        lines = raw.split("\n")
        heads = _md_collect_headings(lines)
        picked = _md_pick_document_title(heads)
        return picked if picked else "Untitled"
    return "Untitled"


def _read_source_title_prefix(path: Path, ext: str, max_chars: int = 16000) -> str:
    """Read only the beginning of a source file for title extraction (cheap)."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except OSError:
        return ""
    t = _extract_title(raw, ext, stem=path.stem)
    return "" if t == "Untitled" else t


def _html_pick_document_heading(body: str) -> str | None:
    """在 HTML 正文中按 DOM 顺序取「最低层级」的标题（先 h1 再 h2），避免先出现 ``<h2>`` 时误取二级节名。"""
    import re

    pat = re.compile(r"<h([1-6])[^>]*>(.*?)</h[1-6]>", re.DOTALL | re.IGNORECASE)
    found: list[tuple[int, int, str]] = []
    for m in pat.finditer(body):
        level = int(m.group(1))
        inner = m.group(2)
        text = re.sub(r"<[^>]+>", "", inner).strip()
        text = re.sub(r"^\d+(?:\.\d+)*\.\s*", "", text).strip()
        if text:
            found.append((level, m.start(), text))
    if not found:
        return None
    min_lv = min(f[0] for f in found)
    for level, _pos, text in found:
        if level == min_lv:
            return text
    return None


def _extract_title_from_html(body: str, ext: str = "") -> str | None:
    """Extract document title from rendered HTML (fragment or full page).

    For full ``wrap_html`` pages, only the ``#nm-panel-render`` region is used so
    embedded JSON / sidebars cannot disturb matching.

    对 ``.rst``：docutils 的文档标题为 ``<h1 class="...title...">``；经 postproc 后节标题
    已降为 ``h2+``，故不得再退回「第一个裸 ``<h1>``」，否则会误取节名（如「介绍」）。
    """
    import re

    if 'id="nm-panel-render"' in body:
        body = extract_render_panel_inner_html(body)
    m = re.search(
        r'<h1[^>]*\bclass\s*=\s*"([^"]*\btitle\b[^"]*)"[^>]*>(.*?)</h1>',
        body,
        re.DOTALL | re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r"<h1[^>]*\bclass\s*=\s*'([^']*\btitle\b[^']*)'[^>]*>(.*?)</h1>",
            body,
            re.DOTALL | re.IGNORECASE,
        )
    if m:
        inner = m.group(2)
    elif ext == ".rst":
        return None
    else:
        return _html_pick_document_heading(body)
    text = re.sub(r"<[^>]+>", "", inner).strip()
    text = re.sub(r"^\d+(?:\.\d+)*\.\s*", "", text).strip()
    return text or None


def _resolve_page_title(content: str, fi: FileInfo, rendered_body: str) -> str:
    """Prefer RST/Markdown source-derived title; fall back to HTML / stem."""
    st = _extract_title(content, fi.ext, fi.rel_path.stem)
    if st == "Untitled":
        st = ""
    ht = _extract_title_from_html(rendered_body, fi.ext) or ""
    return st or ht or fi.rel_path.stem


def _document_list_title(fi: FileInfo, full_page_html: str) -> str:
    """Title for search index + landing tree (may read only a source prefix)."""
    stem = fi.rel_path.stem
    st = _read_source_title_prefix(fi.path, fi.ext)
    ht = _extract_title_from_html(full_page_html, fi.ext) or ""
    if st:
        return st
    # 无标准 RST 下划线标题时，docutils 可能把先导段落抬成 <h1>，目录名会过长，回退文件名
    if ht and len(ht) <= max(72, len(stem) + 40):
        return ht
    return stem


def _copy_static_assets(static_dir):
    """Copy CSS and JS assets to the static directory."""
    (static_dir / "notemanager.css").write_text(_DEFAULT_CSS, encoding="utf-8")
    (static_dir / "notemanager.js").write_text(_DEFAULT_JS, encoding="utf-8")

    # Copy bundled third-party assets (KaTeX, Mermaid, Highlight.js)
    _ASSETS_SRC = Path(__file__).parent / "renderer" / "assets"
    if _ASSETS_SRC.is_dir():
        import shutil
        for item in _ASSETS_SRC.iterdir():
            # Do not overwrite the generated sidebar JS/CSS from _DEFAULT_JS/_DEFAULT_CSS.
            # renderer/assets/notemanager.js is a legacy copy.
            if item.is_file() and item.name in {"notemanager.js", "notemanager.css"}:
                continue
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


_RUNTIME_STATIC_TOP_LEVEL = {
    "notemanager.css",
    "notemanager.js",
    "nm-editor.js",
    "nm-external-editor.js",
    "nm-annotations.js",
    "nm-refs.js",
    "nm-table-editor.js",
    "nm-math-editor.js",
    "nm-overview.js",
    "nm-relations.js",
    "nm-reading.js",
    "nm-command.js",
    "nm-filetree.js",
    "preformatted.css",
    "katex.min.css",
    "katex.min.js",
    "katex-auto-render.min.js",
    "mermaid.min.js",
    "highlight.min.js",
    "highlight-atom-one-dark.min.css",
    "hljs-cpp.min.js",
    "hljs-java.min.js",
    "hljs-json.min.js",
    "hljs-kotlin.min.js",
    "hljs-markdown.min.js",
    "hljs-plaintext.min.js",
    "hljs-rust.min.js",
    "hljs-yaml.min.js",
    "codemirror",
    "katex-fonts",
}


def _is_reserved_runtime_static(rel: Path) -> bool:
    """Return True when a project file would overwrite NoteManager runtime static assets."""
    parts = rel.parts
    if len(parts) < 2 or parts[0] != "_static":
        return False
    return parts[1] in _RUNTIME_STATIC_TOP_LEVEL


def _copy_project_assets(project_root, html_dir):
    """Copy non-document assets (images, etc.) to the html output directory."""
    import shutil
    root = Path(project_root).resolve()
    rules = load_ignore_rules(root)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if is_ignored_path(root, path, rules=rules):
            continue
        parts = path.relative_to(root).parts
        if any(part in {".NoteManager", "_build", "_extensions",
                        "__pycache__", ".git", ".svn", ".hg"} for part in parts):
            continue
        if path.suffix.lower() in _ASSET_EXTENSIONS:
            rel = path.relative_to(root)
            if _is_reserved_runtime_static(rel):
                continue
            dest = html_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)


def _project_id(project_root) -> str:
    return hashlib.sha1(str(Path(project_root).resolve()).encode("utf-8")).hexdigest()[:12]


def _build_activity_safe(project_root, html_dir, documents) -> dict | None:
    try:
        return activity_mod.build_and_write(Path(project_root), html_dir, documents)
    except Exception:
        logger.exception("生成文档活动列表失败")
        return None


def _build_assets_safe(project_root, html_dir, documents) -> dict | None:
    try:
        return assets_mod.build_and_write(Path(project_root), html_dir, documents)
    except Exception:
        logger.exception("生成图片资源索引失败")
        return None


def _build_relations_safe(html_dir, documents) -> dict | None:
    try:
        return relations_mod.build_and_write(html_dir, documents)
    except Exception:
        logger.exception("生成文档关系失败")
        return None


def _remove_overview_outputs(html_dir) -> None:
    for path in (html_dir / "topics.html", html_dir / "_index" / "overview.json"):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("无法清理旧的概览文件: %s", path)


def _ensure_overview_assets(html_dir) -> None:
    """Refresh static CSS/JS once for projects built before this feature."""
    static_dir = html_dir / "_static"
    try:
        css = (static_dir / "notemanager.css").read_text(encoding="utf-8")
    except OSError:
        css = ""
    try:
        js = (static_dir / "notemanager.js").read_text(encoding="utf-8")
    except OSError:
        js = ""
    try:
        overview_js = (static_dir / "nm-overview.js").read_text(encoding="utf-8")
    except OSError:
        overview_js = ""
    try:
        relations_js = (static_dir / "nm-relations.js").read_text(encoding="utf-8")
    except OSError:
        relations_js = ""
    try:
        reading_js = (static_dir / "nm-reading.js").read_text(encoding="utf-8")
    except OSError:
        reading_js = ""
    try:
        command_js = (static_dir / "nm-command.js").read_text(encoding="utf-8")
    except OSError:
        command_js = ""
    try:
        filetree_js = (static_dir / "nm-filetree.js").read_text(encoding="utf-8")
    except OSError:
        filetree_js = ""
    try:
        editor_js = (static_dir / "nm-editor.js").read_text(encoding="utf-8")
    except OSError:
        editor_js = ""
    try:
        external_js = (static_dir / "nm-external-editor.js").read_text(encoding="utf-8")
    except OSError:
        external_js = ""
    try:
        annotations_js = (static_dir / "nm-annotations.js").read_text(encoding="utf-8")
    except OSError:
        annotations_js = ""
    try:
        refs_js = (static_dir / "nm-refs.js").read_text(encoding="utf-8")
    except OSError:
        refs_js = ""
    try:
        table_editor_js = (static_dir / "nm-table-editor.js").read_text(encoding="utf-8")
    except OSError:
        table_editor_js = ""
    try:
        math_editor_js = (static_dir / "nm-math-editor.js").read_text(encoding="utf-8")
    except OSError:
        math_editor_js = ""
    if (
        ".nm-overview" not in css
        or "nmRevealSidebarDir" not in js
        or "Radial directory graph" not in overview_js
        or "backlinks / related documents" not in relations_js
        or "reading state" not in reading_js
        or "command palette" not in command_js
        or "nmOpenShortcutsHelp" not in command_js
        or "create_note_in_dir" not in filetree_js
        or "trash_restore" not in filetree_js
        or "open_url" not in filetree_js
        or "trash_orphans" not in filetree_js
        or "__nmSuppressBuildReload" not in filetree_js
        or "__nmSuppressBuildReload" not in editor_js
        or "import_asset" not in editor_js
        or "nm-source-reference" not in editor_js
        or "__nmSuppressBuildReload" not in js
        or "nmExitSplit" not in js
        or "NM_SPLIT_HOTKEY" not in js
        or "NM_FOCUS_EDITOR_ON_SPLIT" not in js
        or "NM_FOCUS_READONLY_ON_SPLIT" not in js
        or "NM_EXTERNAL_HOTKEY" not in external_js
        or "nm-annotate-overlay" not in annotations_js
        or "nm-ref-choice-list" not in refs_js
        or "nm-source-table" not in table_editor_js
        or "nm-source-math" not in math_editor_js
        or "Ctrl/⌘+Alt+S" not in command_js
    ):
        _copy_static_assets(static_dir)


def _build_overview_safe(project_root, html_dir, documents) -> dict | None:
    """Build overview data; never let a summary failure break the build."""
    try:
        _ensure_overview_assets(html_dir)
        data = overview_mod.build_and_write(project_root, html_dir, documents)
        if data is None:
            _remove_overview_outputs(html_dir)
        return data
    except Exception:
        logger.exception("生成主题概览失败")
        _remove_overview_outputs(html_dir)
        return None


def _generate_landing_page(html_dir, documents, ui_plugin=None, overview_data=None):
    """Generate the landing page: project overview + sidebar directory tree."""
    if ui_plugin is None:
        ui_plugin = UiPluginBundle(css_files=[], js_files=[])

    # Build a nested tree from flat document paths
    tree = {}
    for doc in sorted(documents, key=lambda d: d["path"]):
        parts = Path(doc["path"]).parts
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = doc

    tree_html = _render_tree(tree, depth=0)
    body = overview_mod.render_landing_overview(overview_data)
    project_name = ""
    if overview_data:
        project_name = str(overview_data.get("project") or "")
    if not project_name:
        project_name = html_dir.parent.parent.name or "NoteManager"
    full_html = wrap_html(
        body,
        project_name,
        "index.html",
        toc=tree_html,
        extra_css_files=ui_plugin.css_files,
        extra_js_files=list(ui_plugin.js_files) + ["nm-overview.js"],
        project_id=_project_id(html_dir.parent.parent),
        project_name=project_name,
    )
    (html_dir / "index.html").write_text(full_html, encoding="utf-8")


def _render_tree(node, depth, parent_path=""):
    """Recursively render a directory tree as nested HTML.

    Directory <details> elements get stable ids so the overview graph can
    expand the sidebar to the corresponding directory.
    """
    parts = []
    parts.append('<ul class="nm-tree">')
    for name, value in node.items():
        if isinstance(value, dict) and "path" not in value:
            child_path = f"{parent_path}/{name}" if parent_path else name
            dir_id = overview_mod.dir_node_id(child_path)
            # Directory node: collapsed by default.  Opening every
            # first-level directory made the sidebar hard to scan.
            parts.append(
                f'<li class="nm-dir" data-nm-dir="{html_escape(child_path, quote=True)}">'
                f'<details id="{html_escape(dir_id, quote=True)}">'
            )
            parts.append(
                f'<summary><span class="nm-dir-label">{html_escape(name)}</span>'
                f'<button type="button" class="nm-tree-add" '
                f'data-nm-add-dir="{html_escape(child_path, quote=True)}" '
                f'title="在此目录新建笔记" aria-label="在 {html_escape(name, quote=True)} 新建笔记">+</button>'
                f'</summary>'
            )
            parts.append(_render_tree(value, depth + 1, child_path))
            parts.append('</details></li>')
        else:
            # File node
            path = value["path"]
            title = value["title"]
            display_title = title
            source = value.get("source_path") or ""
            source_attr = (
                f' data-nm-source="{html_escape(str(source), quote=True)}"'
                if source else ""
            )
            delete_btn = ""
            if source:
                delete_btn = (
                    f'<button type="button" class="nm-file-del" '
                    f'data-nm-delete-source="{html_escape(str(source), quote=True)}" '
                    f'data-nm-delete-title="{html_escape(title, quote=True)}" '
                    f'title="删除这篇笔记（移到回收站）" aria-label="删除 {html_escape(title, quote=True)}">×</button>'
                )
            parts.append(
                f'<li class="nm-file"{source_attr} data-nm-html="{html_escape(path, quote=True)}">'
                f'<a href="{html_escape(path, quote=True)}" title="{html_escape(title)}">'
                f'{html_escape(display_title)}</a>'
                f'{delete_btn}</li>'
            )
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
.nm-table-shell { display: inline-block; vertical-align: top; max-width: 100%;
                  margin: 18px 0; overflow-x: auto; background: #fff;
                  border: 1px solid #e2e8f0; border-radius: 8px; }
.nm-table-shell table { width: auto; max-width: 100%; margin: 0; border-collapse: separate;
                        border-spacing: 0; background: #fff; }
.nm-table-shell th, .nm-table-shell td { border: 0; border-bottom: 1px solid #e8eef2;
                                         border-right: 1px solid #eef3f6; padding: 8px 12px;
                                         text-align: left; vertical-align: top; }
.nm-table-shell th:last-child, .nm-table-shell td:last-child { border-right: 0; }
.nm-table-shell tbody tr:last-child td { border-bottom: 0; }
.nm-table-shell thead th { background: #f1f5f9; color: #37474f; font-weight: 600; }
.nm-table-shell tbody tr:nth-child(even) { background: #fafcfe; }
.nm-table-shell tbody tr:hover { background: #f1f7fd; }
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
.nm-tree-highlight { background: #fff3cd; border-radius: 3px; }

/* Project overview / topic map */
.nm-overview { max-width: 900px; }
.nm-overview-title { margin: 0 0 4px; font-size: 26px; }
.nm-overview-stats { color: #777; font-size: 13px; margin-bottom: 18px; }
.nm-overview-heading { font-size: 17px; margin: 20px 0 10px; }
.nm-topic-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
                 gap: 14px; }
.nm-topic-card { background: #fff; border: 1px solid #e5e5e5; border-radius: 8px;
                 padding: 12px 14px; box-shadow: 0 1px 2px rgba(0,0,0,.04); }
.nm-topic-card h3 { font-size: 15px; margin: 0 0 3px; }
.nm-topic-card h3 a { color: #1565c0; text-decoration: none; }
.nm-topic-card h3 a:hover { text-decoration: underline; }
.nm-topic-meta { font-size: 11px; color: #888; margin-bottom: 6px; }
.nm-topic-summary { font-size: 12px; color: #666; margin: 6px 0; }
.nm-overview-doc-list { list-style: none; padding: 0; margin: 6px 0 0; }
.nm-overview-doc-list li { margin: 5px 0; font-size: 13px; }
.nm-overview-doc-list a { color: #0066cc; text-decoration: none; }
.nm-overview-doc-list a:hover { text-decoration: underline; }
.nm-overview-snippet { font-size: 11px; color: #888; margin-top: 2px; }
.nm-more-link { font-size: 12px; color: #0066cc; text-decoration: none; display: inline-block; margin-top: 6px; }
pre.nm-plaintext { white-space: pre-wrap; overflow-wrap: anywhere; background: #fafafa;
                   border: 1px solid #eee; border-radius: 4px; padding: 12px 14px;
                   font-family: inherit; line-height: 1.6; }
.nm-overview-more { margin-top: 10px; font-size: 13px; }
.nm-overview-more a { color: #0066cc; text-decoration: none; }
.nm-overview-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-top: 8px; }
.nm-overview-column { min-width: 0; }
.nm-doc-list { list-style: none; padding: 0; margin: 0; }
.nm-doc-list li { font-size: 13px; margin: 6px 0; overflow-wrap: anywhere; }
.nm-doc-list a { color: #0066cc; text-decoration: none; }
.nm-doc-list a:hover { text-decoration: underline; }
.nm-doc-meta { color: #999; font-size: 11px; }
.nm-overview-empty { color: #888; font-size: 13px; }

/* Document backlinks / related documents */
.nm-related-panel { margin-top: 36px; padding-top: 16px; border-top: 1px solid #e5e5e5; }
.nm-related-heading { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.nm-related-heading h2 { font-size: 17px; margin: 0; }
.nm-related-orphan { font-size: 11px; color: #8d6e63; background: #efebe9;
                     border: 1px solid #d7ccc8; border-radius: 8px; padding: 1px 7px; }
.nm-related-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
                   gap: 16px; align-items: start; }
.nm-related-col h3 { font-size: 13px; margin: 0 0 6px; color: #607d8b; }
.nm-related-col h3 a { color: #1565c0; text-decoration: none; }
.nm-related-col h3 a:hover { text-decoration: underline; }
.nm-related-col ul { list-style: none; padding: 0; margin: 0; }
.nm-related-col li { font-size: 13px; margin: 7px 0; overflow-wrap: anywhere; }
.nm-related-col a { color: #0066cc; text-decoration: none; }
.nm-related-col a:hover { text-decoration: underline; }
.nm-related-snippet { font-size: 11px; color: #999; margin-top: 2px; line-height: 1.45; }
.nm-related-empty { font-size: 12px; color: #aaa; margin: 4px 0; }
.nm-table-dialog { max-width: min(1100px, 96vw); width: min(1100px, 96vw); }
.nm-vim-help { max-width: 620px; }
.nm-math-dialog { width: min(760px, calc(100vw - 32px)); }
.nm-math-mode { display: flex; align-items: center; gap: 14px; margin: 8px 0; font-size: 12px; color: #607d8b; }
.nm-math-mode select { margin-left: 4px; padding: 3px 6px; }
.nm-math-palette { margin: 4px 0 8px; padding: 6px 7px; background: #f7fafc;
                   border: 1px solid #e3ebef; border-radius: 5px; }
.nm-math-palette-tabs { display: flex; flex-wrap: wrap; gap: 3px; margin-bottom: 6px; }
.nm-math-palette-tabs button { border: 1px solid transparent; background: transparent; color: #78909c;
                                border-radius: 4px; padding: 2px 7px; cursor: pointer; font-size: 11px; }
.nm-math-palette-tabs button:hover { background: #eef5f9; color: #1565c0; }
.nm-math-palette-tabs button.active { background: #e3f2fd; color: #1565c0; border-color: #bbdefb; }
.nm-math-palette-list { display: flex; flex-wrap: wrap; gap: 4px; max-height: 104px; overflow-y: auto; }
.nm-math-symbol-btn { min-width: 38px; min-height: 34px; border: 1px solid #d5e2e7; background: #fff;
                      color: #37474f; border-radius: 4px; padding: 3px 7px; cursor: pointer;
                      display: inline-flex; align-items: center; justify-content: center; }
.nm-math-symbol-btn:hover { background: #e3f2fd; border-color: #90caf9; }
.nm-math-icon .katex { font-size: 1.05em; }
.nm-math-icon { display: inline-block; line-height: 1.15; }
.nm-math-format { color: #90a4ae; }
#nm-math-input { width: 100%; box-sizing: border-box; resize: vertical; min-height: 120px;
                 font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
                 font-size: 13px; line-height: 1.55; padding: 9px 10px; border: 1px solid #cfd8dc;
                 border-radius: 5px; background: #fbfcfd; }
#nm-math-input:focus { outline: 2px solid #90caf9; border-color: #90caf9; }
.nm-math-preview { min-height: 64px; margin-top: 10px; padding: 14px; border: 1px solid #e3ebef;
                    border-radius: 5px; background: #fff; overflow-x: auto; color: #263238; }
.nm-vim-help-table { width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 13px; }
.nm-vim-help-table th, .nm-vim-help-table td { border-bottom: 1px solid #e8eef2;
                                                padding: 6px 8px; text-align: left; }
.nm-vim-help-table th { color: #607d8b; font-weight: 600; background: #f7fafc; }
.nm-vim-help-table code { white-space: nowrap; font-size: 12px; }
.nm-table-toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
                     margin: 4px 0 8px; color: #607d8b; font-size: 12px; }
.nm-table-toolbar label { display: inline-flex; align-items: center; gap: 4px; }
.nm-table-toolbar button { padding: 3px 8px; border: 1px solid #cfd8dc; border-radius: 4px;
                           background: #fff; color: #1565c0; cursor: pointer; font-size: 12px; }
.nm-table-toolbar button:hover { background: #e3f2fd; }
.nm-table-scroll { overflow: auto; max-height: 60vh; border: 1px solid #e0e6e9;
                   border-radius: 4px; padding: 8px; }
.nm-table-edit-grid { border-collapse: collapse; width: 100%; }
.nm-table-edit-grid td { border: 1px solid #dfe7eb; padding: 3px; vertical-align: middle; }
.nm-table-edit-grid, .nm-table-edit-grid input, .nm-table-edit-grid select {
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace; }
.nm-table-edit-grid input[type=text] { width: 100%; min-width: 110px; box-sizing: border-box;
                                       border: 0; padding: 5px 6px; background: transparent;
                                       font-family: inherit; font-size: 13px; }
.nm-table-edit-grid input[type=text]:focus { outline: 2px solid #90caf9; background: #fff; }
.nm-table-edit-grid .nm-table-header-row input { font-weight: 600; background: #f5f9ff; }
.nm-table-edit-grid .nm-table-gutter { width: 30px; text-align: right; color: #aaa;
                                       font-size: 11px; padding-right: 5px; }
.nm-table-edit-grid .nm-table-row-actions, .nm-table-edit-grid .nm-table-col-actions td:not(.nm-table-gutter) {
    width: 26px; text-align: center; padding: 2px; }
.nm-table-del { border: 0; background: transparent; color: #b71c1c; cursor: pointer;
                font-size: 15px; line-height: 1; padding: 1px 4px; border-radius: 3px; }
.nm-table-del:hover { background: #ffebee; }
.nm-table-align-row select { width: 100%; font-size: 11px; padding: 2px; }

/* Reading state and "since last visit" */
.nm-reading-controls { display: flex; align-items: center; gap: 6px; }
.nm-reading-btn { padding: 3px 8px; border: 1px solid #bbdefb; border-radius: 4px;
                  background: #fff; color: #1565c0; cursor: pointer; font-size: 12px; }
.nm-reading-btn:hover { background: #e3f2fd; }
.nm-reading-btn.active { background: #e3f2fd; border-color: #64b5f6; }
.nm-reading-home { margin: 4px 0 24px; }
.nm-reading-panel { background: #fff; border: 1px solid #e5e5e5; border-radius: 8px;
                    padding: 12px 14px; box-shadow: 0 1px 2px rgba(0,0,0,.04); }
.nm-reading-heading { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
                      margin-bottom: 8px; }
.nm-reading-heading h2 { font-size: 16px; margin: 0; }
.nm-reading-stats { font-size: 11px; color: #888; }
.nm-reading-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.nm-reading-card { min-width: 0; border-left: 2px solid #e3f2fd; padding-left: 9px; }
.nm-reading-card h3 { font-size: 12px; color: #607d8b; margin: 0 0 5px; }
.nm-reading-card ul { list-style: none; padding: 0; margin: 0; }
.nm-reading-card li { font-size: 12px; margin: 5px 0; overflow-wrap: anywhere; }
.nm-reading-card a { color: #0066cc; text-decoration: none; }
.nm-reading-card a:hover { text-decoration: underline; }
.nm-reading-date { color: #aaa; font-size: 10px; margin-left: 5px; white-space: nowrap; }
.nm-reading-empty { color: #aaa; font-size: 12px; margin: 4px 0; }

/* Saved search views + command palette */
.nm-saved-views { margin: 10px 0 22px; }
.nm-saved-views-heading { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.nm-saved-views-heading h2 { font-size: 15px; margin: 0; }
.nm-saved-views-hint { font-size: 11px; color: #999; }
.nm-saved-views-empty { font-size: 12px; color: #aaa; margin: 5px 0 0; }
.nm-saved-view-list { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }
.nm-saved-view { display: inline-flex; align-items: center; border: 1px solid #d6c6e8;
                 border-radius: 12px; background: #fff; overflow: hidden; }
.nm-saved-view a { color: #6a1b9a; text-decoration: none; font-size: 12px; padding: 3px 4px 3px 9px; }
.nm-saved-view a:hover { text-decoration: underline; }
.nm-saved-view-del { border: 0; background: transparent; color: #b39ddb; cursor: pointer;
                     font-size: 13px; line-height: 1; padding: 2px 7px 3px 3px; }
.nm-saved-view-del:hover { color: #8e24aa; }
body.nm-cmd-open { overflow: hidden; }
.nm-cmd-overlay { position: fixed; inset: 0; z-index: 1000; background: rgba(20, 20, 30, .42);
                  display: flex; align-items: flex-start; justify-content: center; padding-top: 12vh; }
.nm-cmd-overlay[hidden] { display: none !important; }
.nm-cmd-dialog { width: min(620px, calc(100vw - 32px)); max-height: 70vh; background: #fff;
                 border-radius: 10px; box-shadow: 0 18px 60px rgba(0,0,0,.28); overflow: hidden; }
#nm-cmd-input { width: 100%; border: 0; border-bottom: 1px solid #eee; padding: 14px 16px;
                font-size: 15px; outline: none; box-sizing: border-box; }
.nm-cmd-list { max-height: 52vh; overflow-y: auto; padding: 4px 0; }
.nm-cmd-item { padding: 8px 16px; cursor: pointer; border-left: 3px solid transparent; }
.nm-cmd-item.selected { background: #f3e5f5; border-left-color: #8e24aa; }
.nm-cmd-title { font-size: 13px; color: #263238; overflow-wrap: anywhere; }
.nm-cmd-detail { font-size: 11px; color: #999; margin-top: 1px; overflow-wrap: anywhere; }
.nm-cmd-empty { padding: 18px 16px; color: #999; font-size: 13px; }
.nm-cmd-hint { border-top: 1px solid #eee; padding: 7px 16px; color: #aaa; font-size: 11px; }
.nm-help-overlay { position: fixed; inset: 0; z-index: 1000; background: rgba(20, 20, 30, .42);
                   display: flex; align-items: flex-start; justify-content: center; padding-top: 16vh; }
.nm-help-overlay[hidden] { display: none !important; }
.nm-help-dialog { width: min(460px, calc(100vw - 32px)); background: #fff; border-radius: 10px;
                  box-shadow: 0 18px 60px rgba(0,0,0,.28); padding: 14px 18px 16px; box-sizing: border-box; }
.nm-help-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.nm-help-head h2 { font-size: 16px; margin: 0; }
.nm-help-close { border: 0; background: transparent; color: #999; font-size: 20px; line-height: 1;
                 cursor: pointer; padding: 0 2px; }
.nm-help-close:hover { color: #333; }
.nm-help-list { margin: 12px 0 10px; }
.nm-help-list > div { display: flex; align-items: baseline; gap: 12px; margin: 7px 0; }
.nm-help-list dt { flex: 0 0 92px; text-align: right; }
.nm-help-list dd { margin: 0; color: #555; font-size: 12px; }
.nm-help-list kbd { border: 1px solid #ccc; border-bottom-width: 2px; border-radius: 3px;
                    background: #fafafa; color: #555; padding: 1px 5px; font-family: monospace;
                    font-size: 11px; white-space: nowrap; }
.nm-help-note { color: #888; font-size: 11px; line-height: 1.6; margin: 10px 0 0; }
body.nm-note-open { overflow: hidden; }
.nm-annotated-media { position: relative; display: inline-block; max-width: 100%; line-height: 0; }
.nm-annotated-media > img { display: block; max-width: 100%; height: auto; }
.nm-annotation-layer { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none;
                       overflow: visible; }
.nm-image-actions { position: absolute; right: 8px; bottom: 8px; z-index: 2; display: flex;
                    gap: 4px; opacity: 0; }
.nm-annotated-media:hover .nm-image-actions, .nm-image-actions:focus-within { opacity: 1; }
.nm-annotate-btn { position: static; z-index: 2;
                   border: 1px solid #bbdefb; background: rgba(255,255,255,.92); color: #1565c0;
                   border-radius: 4px; padding: 3px 8px; font-size: 11px; cursor: pointer; }
@media (hover: none) { .nm-image-actions { opacity: 1; } }
.nm-zoom-overlay { position: fixed; inset: 0; z-index: 1100; background: rgba(20,20,30,.5);
                   display: flex; align-items: center; justify-content: center; padding: 16px;
                   box-sizing: border-box; }
.nm-zoom-dialog { background: #fff; border-radius: 8px; box-shadow: 0 18px 60px rgba(0,0,0,.35);
                  width: min(720px, 96vw); max-height: 94vh; overflow: auto; padding: 12px 14px;
                  box-sizing: border-box; }
.nm-zoom-head { display: flex; align-items: center; justify-content: space-between; }
.nm-zoom-head h2 { font-size: 16px; margin: 0; }
.nm-zoom-close { border: 0; background: transparent; color: #999; font-size: 20px; cursor: pointer; }
.nm-zoom-controls { display: flex; align-items: center; gap: 8px; margin: 10px 0; font-size: 12px; color: #607d8b; }
.nm-zoom-controls input { flex: 1; }
.nm-zoom-value { font-family: monospace; min-width: 55px; text-align: right; }
.nm-zoom-apply { border: 1px solid #2e7d32; background: #2e7d32; color: #fff; border-radius: 4px;
                 padding: 4px 9px; font-size: 12px; cursor: pointer; }
.nm-zoom-apply:disabled { opacity: .55; cursor: wait; }
.nm-zoom-apply-status { min-height: 17px; color: #c62828; font-size: 11px; margin-top: 4px; }
.nm-zoom-stage { background: #f5f5f5; overflow: auto; padding: 10px; text-align: center; }
.nm-zoom-image { display: inline-block; max-width: none; height: auto; }
.nm-zoom-code { display: grid; grid-template-columns: 48px 1fr auto; align-items: center; gap: 6px;
                margin-top: 8px; font-size: 11px; color: #607d8b; }
.nm-zoom-code pre { background: #f5f7f8; border: 1px solid #e0e5e8; border-radius: 4px;
                    padding: 5px 7px; margin: 0; overflow-x: auto; font-size: 11px; color: #37474f; }
.nm-zoom-copy { border: 1px solid #cfd8dc; background: #fff; border-radius: 4px; padding: 3px 7px;
                font-size: 11px; cursor: pointer; }
.nm-zoom-note { color: #90a4ae; font-size: 11px; line-height: 1.5; margin: 8px 0 0; }
.nm-annotate-overlay { position: fixed; inset: 0; z-index: 1100; background: rgba(20,20,30,.5);
                       display: flex; align-items: center; justify-content: center; padding: 16px;
                       box-sizing: border-box; }
.nm-annotate-dialog { background: #fff; border-radius: 8px; box-shadow: 0 18px 60px rgba(0,0,0,.35);
                      width: min(1100px, 96vw); max-height: 94vh; padding: 10px 12px; box-sizing: border-box;
                      display: flex; flex-direction: column; gap: 8px; }
.nm-annotate-head { display: flex; align-items: center; gap: 10px; }
.nm-annotate-head h2 { font-size: 16px; margin: 0; }
.nm-annotate-status { color: #c62828; font-size: 12px; flex: 1; }
.nm-annotate-close { border: 0; background: transparent; color: #999; font-size: 20px; cursor: pointer; }
.nm-annotate-toolbar { display: flex; align-items: center; gap: 6px; flex-wrap: nowrap;
                       min-height: 32px; overflow-x: auto; }
.nm-annotate-tool, .nm-annotate-action { border: 1px solid #cfd8dc; background: #fff; color: #455a64;
                  border-radius: 4px; padding: 3px 8px; font-size: 12px; cursor: pointer; }
.nm-annotate-tool.active { background: #e3f2fd; border-color: #64b5f6; color: #1565c0; }
.nm-annotate-action:hover, .nm-annotate-tool:hover { background: #f5f5f5; }
.nm-annotate-color { width: 28px; height: 24px; padding: 0; border: 1px solid #cfd8dc; background: #fff; }
.nm-annotate-width { border: 1px solid #cfd8dc; border-radius: 4px; padding: 2px 4px; font-size: 12px; }
.nm-annotate-check { display: inline-flex; align-items: center; gap: 2px; font-size: 12px; color: #455a64; }
.nm-annotate-opacity { width: 70px; }
.nm-annotate-opacity-wrap { display: inline-flex; align-items: center; gap: 3px; }
.nm-annotate-opacity-value { font-size: 11px; color: #607d8b; min-width: 34px; display: inline-block; }
.nm-annotate-info { flex: 0 0 170px; margin-left: auto; color: #607d8b; font-size: 12px;
                    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nm-annotate-stage { position: relative; min-height: 120px; overflow: auto; background: #f5f5f5;
                     text-align: center; }
.nm-annotate-canvas { position: relative; display: inline-block; max-width: 100%; line-height: 0; }
.nm-annotate-image { display: block; max-width: 100%; max-height: 68vh; user-select: none;
                     -webkit-user-drag: none; }
.nm-annotate-svg { position: absolute; inset: 0; width: 100%; height: 100%; touch-action: none;
                   cursor: default; overflow: visible; pointer-events: all; }
.nm-annotate-actions { display: flex; justify-content: flex-end; gap: 8px; }
.nm-annotate-save { border: 1px solid #1565c0; background: #1565c0; color: #fff; border-radius: 4px;
                    padding: 5px 12px; cursor: pointer; }
.nm-annotate-cancel { border: 1px solid #cfd8dc; background: #fff; border-radius: 4px; padding: 5px 12px;
                      cursor: pointer; }
.nm-annotate-export-btn { border: 1px solid #2e7d32; background: #2e7d32; color: #fff;
                          border-radius: 4px; padding: 5px 12px; cursor: pointer; }
.nm-annotate-export-btn:disabled { opacity: .55; cursor: wait; }
.nm-annotate-export { z-index: 1300; }
.nm-annotate-export-path { color: #37474f; font-family: monospace; font-size: 12px;
                           overflow-wrap: anywhere; margin: 4px 0; }
.nm-export-desc { color: #455a64; font-size: 13px; line-height: 1.6; margin: 6px 0; }
.nm-export-warning { color: #b26a00; background: #fff8e1; border: 1px solid #ffe0a3;
                     border-radius: 4px; padding: 6px 8px; font-size: 12px; line-height: 1.5; }
.nm-export-actions { display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap; margin-top: 4px; }
.nm-export-delete { border: 1px solid #c62828; background: #c62828; color: #fff;
                    border-radius: 4px; padding: 5px 12px; cursor: pointer; }
.nm-export-delete:disabled, .nm-export-actions button:disabled { opacity: .55; cursor: wait; }
.nm-note-overlay { position: fixed; inset: 0; z-index: 1000; background: rgba(20, 20, 30, .42);
                   display: flex; align-items: flex-start; justify-content: center; padding-top: 14vh; }
.nm-note-overlay[hidden] { display: none !important; }
.nm-note-dialog { width: min(480px, calc(100vw - 32px)); background: #fff; border-radius: 10px;
                  box-shadow: 0 18px 60px rgba(0,0,0,.28); padding: 16px 18px; box-sizing: border-box; }
.nm-note-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.nm-note-head h2 { font-size: 16px; margin: 0; }
.nm-note-close { border: 0; background: transparent; color: #999; font-size: 20px; line-height: 1;
                 cursor: pointer; padding: 0 2px; }
.nm-note-close:hover { color: #333; }
.nm-note-field { margin-top: 12px; }
.nm-note-field label { display: block; color: #607d8b; font-size: 11px; margin-bottom: 4px; }
.nm-note-dir { font-family: monospace; font-size: 12px; color: #455a64; background: #f5f7f8;
               border: 1px solid #e0e5e8; border-radius: 4px; padding: 6px 8px; overflow-wrap: anywhere; }
.nm-note-field input, .nm-note-field select { width: 100%; box-sizing: border-box; padding: 7px 9px;
               border: 1px solid #cfd8dc; border-radius: 4px; font-size: 13px; outline: none; background: #fff; }
.nm-note-field input:focus, .nm-note-field select:focus { border-color: #1565c0; }
.nm-note-error { color: #c62828; background: #ffebee; border-radius: 4px; padding: 5px 8px;
                 font-size: 12px; margin-top: 10px; }
.nm-note-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
.nm-note-actions button { border-radius: 4px; padding: 5px 12px; font-size: 12px; cursor: pointer; }
.nm-note-cancel { border: 1px solid #cfd8dc; background: #fff; color: #555; }
.nm-note-primary { border: 1px solid #1565c0; background: #1565c0; color: #fff; }
.nm-note-primary:disabled { opacity: .55; cursor: default; }
.nm-note-danger { border: 1px solid #c62828; background: #c62828; color: #fff; }
.nm-note-danger:disabled { opacity: .55; cursor: default; }
.nm-note-file { font-size: 13px; color: #263238; margin-top: 8px; overflow-wrap: anywhere; }
.nm-import-hint { color: #78909c; font-size: 11px; line-height: 1.55; margin: 8px 0 0; }
.nm-reference-dialog { width: min(720px, calc(100vw - 32px)); max-height: 88vh; overflow-y: auto; }
.nm-reference-dialog .nm-note-actions { position: sticky; bottom: 0; background: #fff;
                                        padding-top: 8px; padding-bottom: 2px; z-index: 1; }
.nm-reference-filters { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
.nm-reference-filters select, .nm-reference-filters input { border: 1px solid #cfd8dc; border-radius: 4px;
                                                          padding: 5px 7px; font-size: 12px; }
.nm-reference-filters select { min-width: 150px; }
.nm-reference-filters input { flex: 1; min-width: 160px; }
.nm-reference-list { max-height: 330px; overflow-y: auto; border: 1px solid #eceff1; border-radius: 5px;
                     margin-top: 8px; }
.nm-import-hint code { background: #eceff1; padding: 0 3px; border-radius: 2px; }
.nm-ref-choice-list { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.nm-ref-choice-btn { text-align: left; border: 1px solid #cfd8dc; background: #fafafa; color: #37474f;
                     border-radius: 4px; padding: 7px 9px; font-family: monospace; font-size: 12px;
                     cursor: pointer; overflow-wrap: anywhere; }
.nm-ref-choice-btn:hover { background: #e3f2fd; border-color: #90caf9; }
#nm-import-file { padding: 4px 0; border: 0; background: transparent; font-size: 12px; }
.nm-note-impact { font-size: 12px; color: #555; background: #f7f9fa; border: 1px solid #eceff1;
                  border-radius: 5px; padding: 8px 10px; margin-top: 10px; line-height: 1.6; }
.nm-note-impact-main { margin-top: 3px; color: #37474f; }
.nm-note-impact-list { color: #78909c; font-size: 11px; padding-left: 8px; margin-top: 2px;
                       overflow-wrap: anywhere; }
.nm-note-warning { font-size: 11px; color: #8d6e63; background: #fff8e1; border-radius: 4px;
                   padding: 6px 8px; margin-top: 10px; line-height: 1.55; }
.nm-note-warning code { background: #fff3cd; padding: 0 3px; border-radius: 2px; }
.nm-note-asset-choice { display: block; font-size: 12px; color: #37474f; background: #f5f9ff;
                        border: 1px solid #e1ecf8; border-radius: 4px; padding: 6px 8px;
                        margin-top: 9px; cursor: pointer; }
.nm-note-asset-choice input { vertical-align: middle; margin: 0 4px 0 0; }
.nm-trash-list { margin-top: 10px; max-height: 330px; overflow-y: auto; border: 1px solid #eceff1;
                 border-radius: 5px; }
.nm-trash-empty { padding: 18px 12px; color: #999; font-size: 12px; text-align: center; }
.nm-trash-item { display: flex; align-items: center; gap: 10px; padding: 8px 10px;
                 border-bottom: 1px solid #f0f0f0; }
.nm-trash-item:last-child { border-bottom: 0; }
.nm-trash-main { min-width: 0; flex: 1; }
.nm-trash-title { font-size: 13px; color: #263238; overflow-wrap: anywhere; }
.nm-trash-path { font-size: 10px; color: #90a4ae; font-family: monospace; overflow-wrap: anywhere; }
.nm-trash-meta { font-size: 10px; color: #aaa; margin-top: 2px; }
.nm-trash-restore { border: 1px solid #a5d6a7; background: #e8f5e9; color: #2e7d32;
                    border-radius: 4px; padding: 3px 9px; font-size: 11px; cursor: pointer; }
.nm-trash-restore:hover { background: #c8e6c9; }
.nm-trash-restore:disabled { opacity: .55; cursor: default; }
.nm-assets-dialog { width: min(620px, calc(100vw - 32px)); }
.nm-assets-summary { font-size: 12px; color: #607d8b; margin-top: 8px; }
.nm-assets-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px;
                      margin: 9px 0; flex-wrap: wrap; }
.nm-assets-all { font-size: 12px; color: #455a64; cursor: pointer; }
.nm-assets-all input { vertical-align: middle; margin: 0 4px 0 0; }
#nm-assets-trash, .nm-assets-insert { padding: 4px 9px; border-radius: 4px; font-size: 11px; }
.nm-assets-insert { border: 1px solid #90caf9; background: #e3f2fd; color: #1565c0; cursor: pointer; }
.nm-assets-insert:disabled { opacity: .5; cursor: default; }
.nm-assets-status { min-height: 17px; margin-top: 7px; color: #2e7d32; font-size: 11px; }
.nm-assets-list { max-height: 300px; overflow-y: auto; border: 1px solid #eceff1; border-radius: 5px; }
.nm-assets-empty { padding: 16px 12px; color: #999; font-size: 12px; text-align: center; }
.nm-asset-item { display: flex; align-items: center; gap: 9px; padding: 7px 9px;
                 border-bottom: 1px solid #f2f2f2; cursor: pointer; }
.nm-asset-item:last-child { border-bottom: 0; }
.nm-asset-item:hover { background: #fafafa; }
.nm-asset-item input { flex: 0 0 auto; }
.nm-asset-item img { width: 38px; height: 38px; object-fit: cover; border-radius: 3px;
                     border: 1px solid #eee; background: #fafafa; flex: 0 0 auto; }
.nm-asset-main { min-width: 0; flex: 1; }
.nm-asset-path { font-size: 11px; color: #455a64; font-family: monospace; overflow-wrap: anywhere; }
.nm-asset-size { font-size: 10px; color: #aaa; margin-top: 1px; }
.nm-assets-missing { margin-top: 8px; }
.nm-assets-missing-title { font-size: 11px; color: #b71c1c; margin-bottom: 2px; }
.nm-asset-missing { font-size: 10px; color: #c62828; font-family: monospace; overflow-wrap: anywhere; }
@media (max-width: 600px) {
    .nm-note-overlay { padding-top: 7vh; }
}
@media (max-width: 600px) {
    .nm-cmd-overlay { padding-top: 6vh; }
    .nm-cmd-dialog { max-height: 80vh; }
}
@media (max-width: 900px) {
    .nm-reading-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 600px) {
    .nm-reading-grid { grid-template-columns: 1fr; }
}
@media (max-width: 1100px) {
    .nm-related-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 800px) {
    .nm-related-grid { grid-template-columns: 1fr; }
}

/* Directory graph overview */
.nm-dir-hint { color: #888; font-size: 12px; margin: -4px 0 10px; }
.nm-dir-graph-wrap { overflow: auto; max-height: 80vh; background: #fff;
                     border: 1px solid #e5e5e5; border-radius: 8px; padding: 8px; }
.nm-dir-graph { display: block; width: 100%; height: auto; margin: 0 auto; }
.nm-dir-nav { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
              margin: 4px 0; color: #777; font-size: 12px; }
.nm-dir-nav button { padding: 2px 8px; border: 1px solid #bbdefb; border-radius: 4px;
                     background: #fff; color: #1565c0; cursor: pointer; font-size: 12px; }
.nm-dir-nav button:hover:not(:disabled) { background: #e3f2fd; }
.nm-dir-nav button:disabled { opacity: .45; cursor: default; }
#nm-dir-breadcrumb { margin-left: 4px; }
.nm-crumb { border: 0; background: none; color: #1565c0; cursor: pointer;
            font: inherit; padding: 0; }
button.nm-crumb:hover { text-decoration: underline; }
.nm-crumb-sep { color: #bbb; }
.nm-dir-graph-tools { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
                      margin: 4px 0 8px; color: #777; font-size: 12px; }
.nm-dir-graph-tools input[type=range] { width: 130px; }
.nm-dir-graph-tools button { padding: 2px 8px; border: 1px solid #bbdefb; border-radius: 4px;
                             background: #fff; color: #1565c0; cursor: pointer; font-size: 12px; }
.nm-dir-graph-tools button:hover { background: #e3f2fd; }
.nm-g-edge { fill: none; stroke: #cfd8dc; stroke-width: 1.4; }
.nm-g-node circle { transition: filter .12s; }
.nm-g-node:hover circle { filter: brightness(.95); }
a.nm-g-node { cursor: pointer; }
@media (max-width: 700px) {
    .nm-overview-columns { grid-template-columns: 1fr; }
}
"""

_DEFAULT_JS = """\
/* Reveal a directory node in the sidebar tree from the overview graph. */
window.nmRevealSidebarDir = function(id) {
    var el = document.getElementById(id);
    if (!el) return false;
    var node = el;
    while (node && node !== document.body) {
        if (node.tagName === 'DETAILS') node.open = true;
        node = node.parentElement;
    }
    var tocBtn = document.getElementById('nm-sidebar-view-toc');
    if (tocBtn) tocBtn.click();
    var summary = el.querySelector('summary');
    if (summary) {
        summary.classList.remove('nm-tree-highlight');
        void summary.offsetWidth;
        summary.classList.add('nm-tree-highlight');
        setTimeout(function() { summary.classList.remove('nm-tree-highlight'); }, 1800);
        summary.scrollIntoView({block: 'center'});
    } else {
        el.scrollIntoView({block: 'center'});
    }
    return false;
};

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
    var sidebarViewTocBtn = document.getElementById('nm-sidebar-view-toc');
    var sidebarViewResultsBtn = document.getElementById('nm-sidebar-view-results');

    var searchData = null;
    var searchDataPrefix = null;
    var debounceTimer = null;

    var prefix = window.NM_PREFIX || '';

    var TYPE_LABELS = {
        chapter: '章节',
        label: '标签',
        index: '索引',
        dtag: '标记',
        grep: '渲染块',
        fname: '文件名'
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
        setSidebarView('results');
        if (sidebarResults) {
            sidebarResults.innerHTML = '<div class="nm-no-results">无法加载搜索索引（请确认已构建并执行 nmr rebuild）</div>';
        }
    }

    function setSidebarView(view) {
        if (view === 'results') {
            if (sidebarToc) sidebarToc.style.display = 'none';
            if (sidebarResults) sidebarResults.style.display = 'block';
            if (sidebarViewResultsBtn) sidebarViewResultsBtn.classList.add('active');
            if (sidebarViewTocBtn) sidebarViewTocBtn.classList.remove('active');
        } else {
            if (sidebarToc) sidebarToc.style.display = '';
            if (sidebarResults) sidebarResults.style.display = 'none';
            if (sidebarViewResultsBtn) sidebarViewResultsBtn.classList.remove('active');
            if (sidebarViewTocBtn) sidebarViewTocBtn.classList.add('active');
        }
    }

    // Keep q in URL so that when user opens a search result and comes back,
    // the sidebar can re-show the same search results.
    function upsertQParam(url, q) {
        if (!q) return url;
        var u = String(url || '');
        var hash = '';
        var hi = u.indexOf('#');
        if (hi >= 0) {
            hash = u.slice(hi);
            u = u.slice(0, hi);
        }
        if (/[?&]q=/.test(u)) {
            u = u.replace(/([?&])q=[^&#]*/g, '$1q=' + encodeURIComponent(q));
            return u + hash;
        }
        var sep = u.indexOf('?') >= 0 ? '&' : '?';
        return u + sep + 'q=' + encodeURIComponent(q) + hash;
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
        var match = raw.match(/^(chapter|label|index|dtag|fname|grep):\\s*(.*)$/i);
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
            // Default (mapped) search: do NOT include grep/render blocks unless user
            // explicitly uses `grep:`. This matches docs/搜索策略说明.md.
            if (!parsed.type && doc.type === 'grep') continue;
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
            var href = docHref(doc.path);
            href = upsertQParam(href, query);
            if (doc.type === 'grep' && doc.source_path && doc.line_start) {
                var sp = encodeURIComponent(String(doc.source_path));
                var lsep = href.indexOf('?') >= 0 ? '&' : '?';
                href = href + lsep + 'src=' + sp + '&line=' + encodeURIComponent(String(doc.line_start));
            }
            var dp = escAttr(doc.path || '');
            html += '<a class="nm-result-item" data-doc-path="' + dp + '" href="' + escAttr(href) + '">' +
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
            // Avoid auto-navigation to prevent "q=..." reload loops.
            // Keep showing the single result instead.
            // window.location.href = upsertQParam(docHref(results[0].path), q);
            return;
        }
        var html = renderResults(q, results);
        if (sidebarResults) sidebarResults.innerHTML = html;
        setSidebarView('results');
        restoreSearchScrollAndSelection(q);
    }

    function hideSearchResults() {
        setSidebarView('toc');
    }

    function searchKey(q) {
        try { return encodeURIComponent(String(q || '')); } catch(e) { return String(q || ''); }
    }
    function selKey(q) { return 'nm_search_sel_' + searchKey(q); }
    function scrollKey(q) { return 'nm_search_scroll_' + searchKey(q); }
    function hoverYKey(q) { return 'nm_search_hover_y_' + searchKey(q); }
    function selTopKey(q) { return 'nm_search_sel_top_' + searchKey(q); }

    function restoreSearchScrollAndSelection(q) {
        if (!sidebarResults) return;
        // Ensure we restore after layout.
        try {
            var sel = sessionStorage.getItem(selKey(q));
            var st = parseInt(sessionStorage.getItem(scrollKey(q)) || '', 10);
            var hoverY = parseFloat(sessionStorage.getItem(hoverYKey(q)) || 'NaN');
            var selTop = parseFloat(sessionStorage.getItem(selTopKey(q)) || 'NaN');
            if (isNaN(st)) st = null;
            if (isNaN(hoverY)) hoverY = null;
            if (isNaN(selTop)) selTop = null;
            if (sel) {
                var nodes = sidebarResults.querySelectorAll('.nm-result-item');
                for (var i = 0; i < nodes.length; i++) {
                    if (nodes[i].getAttribute('data-doc-path') === sel) {
                        if (hoverY !== null && selTop !== null) {
                            // itemTop is relative to scroll content; to make item stay under cursor
                            // with the same relative offset (hoverY), we set scrollTop so that
                            // itemTop - scrollTop == hoverY.
                            var desired = selTop - hoverY;
                            if (desired < 0) desired = 0;
                            var max = sidebarResults.scrollHeight - sidebarResults.clientHeight;
                            if (desired > max) desired = max;
                            sidebarResults.scrollTop = desired;
                        } else if (hoverY !== null) {
                            // Fallback to approximate delta alignment.
                            nodes[i].scrollIntoView({ block: 'nearest' });
                        } else {
                            nodes[i].scrollIntoView({ block: 'nearest' });
                        }
                        return;
                    }
                }
            }
            if (st !== null) sidebarResults.scrollTop = st;
        } catch (err) {}
    }

    if (sidebarResults) {
        sidebarResults.addEventListener('click', function(e) {
            var a = e.target.closest('.nm-result-item');
            if (!a) return;
            var q = '';
            // Derive current query from the input (preferred).
            if (searchInput) q = String(searchInput.value || '').trim();
            if (!q) {
                // Fallback: attempt from URL param.
                var params = new URLSearchParams(window.location.search);
                q = params.get('q') || '';
            }
            var dp = a.getAttribute('data-doc-path') || '';
            try {
                sessionStorage.setItem(selKey(q), dp);
                sessionStorage.setItem(scrollKey(q), String(sidebarResults.scrollTop || 0));
                // Save the pointer Y offset relative to results container,
                // and the selected item top relative to scroll content.
                var cRect = sidebarResults.getBoundingClientRect();
                var y = (typeof e.clientY === 'number' ? e.clientY : cRect.top) - cRect.top;
                var nRect = a.getBoundingClientRect();
                var itemTop = (nRect.top - cRect.top) + (sidebarResults.scrollTop || 0);
                sessionStorage.setItem(hoverYKey(q), String(y));
                sessionStorage.setItem(selTopKey(q), String(itemTop));
            } catch (err) {}
        });
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
            showSearchResults(q, false);
        }).catch(showIndexLoadError);
    });

    (function() {
        var params = new URLSearchParams(window.location.search);
        var q = params.get('q');
        if (q) {
            searchInput.value = q;
            loadIndex().then(function() {
                showSearchResults(q, false);
            }).catch(showIndexLoadError);
        }
    })();

    // Sidebar view switches: keep search results and TOC independent.
    if (sidebarViewTocBtn) {
        sidebarViewTocBtn.addEventListener('click', function() { setSidebarView('toc'); });
    }
    if (sidebarViewResultsBtn) {
        sidebarViewResultsBtn.addEventListener('click', function() { setSidebarView('results'); });
    }
    setSidebarView('toc');
    window.nmRunSearch = function(query) {
        if (!searchInput) return false;
        var q = String(query || '').trim();
        if (!q) return false;
        searchInput.value = q;
        loadIndex().then(function() {
            showSearchResults(q, false);
        }).catch(showIndexLoadError);
        return true;
    };
    document.querySelectorAll('.nm-dtag-badge').forEach(function(badge) {
        badge.addEventListener('click', function() {
            var tag = this.getAttribute('data-dtag');
            if (tag) {
                searchInput.value = 'dtag:' + tag;
                loadIndex().then(function() {
                    showSearchResults('dtag:' + tag, false);
                }).catch(showIndexLoadError);
            }
        });
    });
    __NM_CLIENT_BOOTSTRAP__
    (function() {
        var evtSource = window.NM_EVENT_SOURCE;
        if (!evtSource) return;
        evtSource.addEventListener('cli_search', function(e) {
            var data = JSON.parse(e.data);
            if (data.open_url) {
                window.location.href = data.open_url;
                return;
            }
            var query = data.query || '';
            if (!query) return;
            var parts = window.location.pathname.split('/').filter(function(s) { return s; });
            var curProject = (parts.length >= 2 && parts[0] === 'p')
                ? parseInt(parts[1], 10) : null;
            if (data.project !== undefined && data.project !== null
                    && curProject !== data.project) {
                window.location.href = '/p/' + data.project + '/?q=' + encodeURIComponent(query);
                return;
            }
            searchInput.value = query;
            clearTimeout(debounceTimer);
            loadIndex().then(function() {
                showSearchResults(query, false);
            }).catch(showIndexLoadError);
            window.focus();
        });
        evtSource.addEventListener('cli_view', function(e) {
            var data = JSON.parse(e.data);
            if (!data || !data.url) return;
            window.location.href = data.url;
        });
        evtSource.addEventListener('build_complete', function(e) {
            // A page that is creating/deleting a note is about to navigate to a
            // different URL; don't let this event reload the current page first.
            if (window.__nmSuppressBuildReload) return;
            var data;
            try { data = JSON.parse(e.data); } catch (err) { return; }
            if (data.project === undefined || data.project === null) return;
            var parts = window.location.pathname.split('/').filter(function(s) { return s; });
            if (parts.length < 2 || parts[0] !== 'p') return;
            var cur = parseInt(parts[1], 10);
            if (isNaN(cur) || cur !== data.project) return;
            // Break potential reload loops: if build_complete is emitted on
            // reconnect or too frequently, we only reload once per time window.
            try {
                var key = 'nm_build_reload_ts';
                var last = parseInt(sessionStorage.getItem(key) || '0', 10) || 0;
                var now = Date.now();
                if (now - last < 10000) return;
                sessionStorage.setItem(key, String(now));
            } catch (err) {
                // Ignore sessionStorage errors; fall back to a plain reload.
            }
            window.location.reload();
        });
    })();
})();

/* Layout features: split view, back-to-top, TOC -> source line jump, readonly line jump */
(function () {
    function ready(fn) {
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn);
        else fn();
    }
    ready(function () {
        var body = document.body;
        var splitBtn = document.getElementById('nm-split-btn');
        var backtop = document.getElementById('nm-backtop');
        var renderPanel = document.getElementById('nm-panel-render');
        var sourcePanel = document.getElementById('nm-panel-source');
        var warningsPanel = document.getElementById('nm-panel-warnings');
        function layoutChanged() {
            try { document.dispatchEvent(new CustomEvent('nm-layout-change')); } catch (e) {}
            updateBacktop();
        }
        function sourceVisible() {
            return !!sourcePanel && (sourcePanel.classList.contains('active') || body.classList.contains('nm-split'));
        }
        function panelVisible(el) {
            return !!el && !!(el.offsetWidth || el.offsetHeight);
        }
        function setSplit(on) {
            body.classList.toggle('nm-split', !!on);
            if (splitBtn) {
                splitBtn.classList.toggle('active', !!on);
                splitBtn.textContent = on ? '\u9000\u51fa\u5206\u5c4f' : '\u5206\u5c4f';
            }
            if (on) {
                var tabs = document.querySelectorAll('.nm-tab');
                for (var i = 0; i < tabs.length; i++) tabs[i].classList.remove('active');
                var panels = document.querySelectorAll('.nm-tab-panel');
                for (var j = 0; j < panels.length; j++) panels[j].classList.remove('active');
                var rt = document.querySelector('.nm-tab[data-tab="render"]');
                if (rt) rt.classList.add('active');
                if (renderPanel) renderPanel.classList.add('active');
                hookCM();
            }
            layoutChanged();
            if (on) {
                setTimeout(function () {
                    if (window.__nmCM && typeof window.__nmCM.focus === 'function') {
                        window.__nmCM.focus();
                    } else if (sourcePanel && typeof sourcePanel.focus === 'function') {
                        sourcePanel.focus();
                    }
                }, 30);
            }
        }
        window.nmSetSplit = setSplit;
        window.nmExitSplit = function () { setSplit(false); };
        var NM_SPLIT_HOTKEY = 'Ctrl/⌘+Alt+S';
        var NM_FOCUS_EDITOR_ON_SPLIT = true;
        var NM_FOCUS_READONLY_ON_SPLIT = true;
        document.addEventListener('keydown', function (event) {
            if (event.repeat) return;
            if (!(event.ctrlKey || event.metaKey) || !event.altKey) return;
            if (String(event.key || '').toLowerCase() !== 's') return;
            if (!splitBtn) return;
            event.preventDefault();
            event.stopPropagation();
            setSplit(!body.classList.contains('nm-split'));
        }, true);
        if (splitBtn) {
            splitBtn.addEventListener('click', function () {
                setSplit(!body.classList.contains('nm-split'));
            });
            document.addEventListener('click', function (e) {
                var tab = e.target.closest && e.target.closest('.nm-tab');
                if (!tab) return;
                if (!body.classList.contains('nm-split')) return;
                body.classList.remove('nm-split');
                splitBtn.classList.remove('active');
                splitBtn.textContent = '\u5206\u5c4f';
                layoutChanged();
            }, true);
        }
        function scrollablePanels() {
            var out = [];
            [renderPanel, sourcePanel, warningsPanel].forEach(function (el) {
                if (panelVisible(el)) out.push(el);
            });
            return out;
        }
        function updateBacktop() {
            if (!backtop) return;
            var top = 0;
            scrollablePanels().forEach(function (el) { top = Math.max(top, el.scrollTop); });
            backtop.classList.toggle('show', top > 400);
        }
        if (backtop) {
            [renderPanel, sourcePanel, warningsPanel].forEach(function (el) {
                if (el) el.addEventListener('scroll', updateBacktop, { passive: true });
            });
            backtop.addEventListener('click', function () {
                scrollablePanels().forEach(function (el) {
                    try { el.scrollTo({ top: 0, behavior: 'smooth' }); }
                    catch (err) { el.scrollTop = 0; }
                });
            });
        }

        /* ---- split scroll sync: render <-> source, anchored on heading map ---- */
        var syncBtn = document.getElementById('nm-sync-btn');
        var syncEnabled = true;
        try { syncEnabled = localStorage.getItem('nm-sync-scroll') !== '0'; } catch (e) {}
        var syncAnchors = [];
        var syncAnchorsDirty = true;
        var syncLockUntil = 0;
        var syncCmHooked = false;

        function syncReady() {
            return body.classList.contains('nm-split') && syncEnabled && window.__nmCM;
        }
        function markAnchorsDirty() { syncAnchorsDirty = true; }
        // 源码侧真正滚动的容器是 #nm-panel-source 面板（CodeMirror 为
        // height:auto 全高展开，自身不产生滚动），所有同步以面板像素为准。
        var sourcePanel = document.getElementById('nm-panel-source');
        function buildSyncAnchors() {
            syncAnchorsDirty = false;
            syncAnchors = [];
            var cm = window.__nmCM;
            if (!cm || !renderPanel || !sourcePanel) return;
            var map = getHeadingMap();
            var keys = Object.keys(map);
            if (keys.length < 2) return;
            var rTop = renderPanel.getBoundingClientRect().top;
            /* src = 源码面板内容坐标系里标题行的像素位置；dst = 渲染面板滚动像素。
               两端钉住 0/max，滚动条首尾精确对齐，标题锚点精确、其间线性插值。 */
            var cmBase = cm.getWrapperElement().getBoundingClientRect().top
                - sourcePanel.getBoundingClientRect().top + sourcePanel.scrollTop;
            var pts = [{ src: 0, dst: 0 }];
            var lastLine = Math.max(0, cm.lineCount() - 1);
            keys.forEach(function (id) {
                var el = document.getElementById(id);
                if (!el) return;
                var y = el.getBoundingClientRect().top - rTop + renderPanel.scrollTop;
                if (y < 0) y = 0;
                var px = cmBase + cm.heightAtLine(Math.max(0, Math.min(map[id] - 1, lastLine)), 'local');
                pts.push({ src: px, dst: y });
            });
            var maxSrc = Math.max(0, sourcePanel.scrollHeight - sourcePanel.clientHeight);
            var maxDst = Math.max(0, renderPanel.scrollHeight - renderPanel.clientHeight);
            pts.push({ src: maxSrc, dst: maxDst });
            pts.sort(function (a, b) { return a.src - b.src; });
            var out = [];
            pts.forEach(function (pt) {
                if (out.length && out[out.length - 1].src === pt.src) {
                    if (pt.dst < out[out.length - 1].dst) out[out.length - 1].dst = pt.dst;
                    return;
                }
                out.push({ src: pt.src, dst: pt.dst });
            });
            /* 保持 dst 单调不减，反向插值才有定义 */
            for (var i = 1; i < out.length; i++) {
                if (out[i].dst < out[i - 1].dst) out[i].dst = out[i - 1].dst;
            }
            syncAnchors = out;
        }
        function ensureSyncAnchors() {
            if (syncAnchorsDirty || !syncAnchors.length) buildSyncAnchors();
        }
        function interpSrcToDst(srcPx) {
            var n = syncAnchors.length;
            if (!n) return 0;
            if (srcPx <= syncAnchors[0].src) return syncAnchors[0].dst;
            for (var i = 1; i < n; i++) {
                var a = syncAnchors[i];
                if (srcPx <= a.src) {
                    var pv = syncAnchors[i - 1];
                    var d = a.src - pv.src;
                    return pv.dst + (d > 0 ? (a.dst - pv.dst) * (srcPx - pv.src) / d : 0);
                }
            }
            return syncAnchors[n - 1].dst;
        }
        function interpDstToSrc(dstPx) {
            var n = syncAnchors.length;
            if (!n) return 0;
            if (dstPx <= syncAnchors[0].dst) return syncAnchors[0].src;
            for (var j = 1; j < n; j++) {
                var b = syncAnchors[j];
                if (dstPx <= b.dst) {
                    var q = syncAnchors[j - 1];
                    var dy = b.dst - q.dst;
                    return q.src + (dy > 0 ? (b.src - q.src) * (dstPx - q.dst) / dy : 0);
                }
            }
            return syncAnchors[n - 1].src;
        }
        function syncSourceFromRender() {
            if (!sourcePanel) return;
            ensureSyncAnchors();
            if (syncAnchors.length < 2) return;
            var target = interpDstToSrc(renderPanel.scrollTop);
            syncLockUntil = Date.now() + 120;
            sourcePanel.scrollTop = Math.max(0, target);
        }
        function syncRenderFromSource() {
            if (!sourcePanel) return;
            ensureSyncAnchors();
            if (syncAnchors.length < 2) return;
            var target = interpSrcToDst(sourcePanel.scrollTop);
            syncLockUntil = Date.now() + 120;
            renderPanel.scrollTop = Math.max(0, target);
        }
        function hookCM() {
            if (syncCmHooked || !window.__nmCM) return;
            syncCmHooked = true;
            var cm = window.__nmCM;
            /* CM 是全高展开的，滚动发生在其外层面板；同时监听 CM 自身滚动
               以防未来改成受限高度模式。 */
            sourcePanel.addEventListener('scroll', function () {
                if (Date.now() < syncLockUntil) return;
                if (!syncReady()) return;
                syncRenderFromSource();
            }, { passive: true });
            var scroller = cm.getScrollerElement && cm.getScrollerElement();
            if (scroller) scroller.addEventListener('scroll', function () {
                if (Date.now() < syncLockUntil) return;
                if (!syncReady()) return;
                syncRenderFromSource();
            }, { passive: true });
            cm.on('change', markAnchorsDirty);
            cm.on('swapDoc', markAnchorsDirty);
            renderPanel.addEventListener('scroll', function () {
                if (Date.now() < syncLockUntil) return;
                if (!syncReady()) return;
                syncSourceFromRender();
            }, { passive: true });
        }
        function refreshSyncBtn() {
            if (!syncBtn) return;
            var show = body.classList.contains('nm-split') && !!window.__nmCM;
            syncBtn.style.display = show ? '' : 'none';
            syncBtn.classList.toggle('active', syncEnabled);
            syncBtn.textContent = syncEnabled ? '\u540c\u6b65\u5f00' : '\u540c\u6b65\u5173';
        }
        if (syncBtn) {
            syncBtn.addEventListener('click', function () {
                syncEnabled = !syncEnabled;
                try { localStorage.setItem('nm-sync-scroll', syncEnabled ? '1' : '0'); } catch (e) {}
                refreshSyncBtn();
                if (syncEnabled && syncReady()) syncSourceFromRender();
            });
        }
        var syncCmPollCount = 0;
        var syncCmPoll = setInterval(function () {
            if (window.__nmCM && window.__nmCM.getScrollerElement) {
                hookCM();
                markAnchorsDirty();
                refreshSyncBtn();
                clearInterval(syncCmPoll);
            } else if (++syncCmPollCount > 40) {
                clearInterval(syncCmPoll);
            }
        }, 500);
        window.addEventListener('resize', markAnchorsDirty);
        window.addEventListener('load', markAnchorsDirty, true);
        document.addEventListener('nm-layout-change', function () {
            markAnchorsDirty();
            refreshSyncBtn();
        });
        var headingMap = null;
        function getHeadingMap() {
            if (headingMap !== null) return headingMap;
            headingMap = {};
            var el = document.getElementById('nm-heading-map');
            if (el) {
                try { headingMap = JSON.parse(el.textContent || '{}') || {}; } catch (e) { headingMap = {}; }
            }
            return headingMap;
        }
        function flashRect(rect) {
            if (!rect) return;
            var d = document.createElement('div');
            d.className = 'nm-src-flash';
            d.style.left = rect.left + 'px';
            d.style.top = rect.top + 'px';
            d.style.width = Math.max(rect.width || 0, 80) + 'px';
            d.style.height = Math.max(rect.height || 0, 16) + 'px';
            document.body.appendChild(d);
            setTimeout(function () { d.className = 'nm-src-flash fade'; }, 900);
            setTimeout(function () { if (d.parentNode) d.parentNode.removeChild(d); }, 1600);
        }
        function gotoReadonlyLine(line) {
            var pre = sourcePanel ? sourcePanel.querySelector('pre') : null;
            if (!pre) return;
            var walker = document.createTreeWalker(pre, NodeFilter.SHOW_TEXT, null, false);
            var node, target = null, remaining = line - 1;
            outerLoop:
            while ((node = walker.nextNode())) {
                var text = node.data;
                for (var i = 0; i < text.length; i++) {
                    if (text.charAt(i) === '\\n') {
                        remaining--;
                        if (remaining <= 0) { target = { node: node, offset: i + 1 }; break outerLoop; }
                    }
                }
            }
            if (!target) { sourcePanel.scrollTop = sourcePanel.scrollHeight; return; }
            var range = document.createRange();
            range.setStart(target.node, target.offset);
            range.setEnd(target.node, Math.min(target.node.length, target.offset + 40));
            var rect = range.getBoundingClientRect();
            var pRect = sourcePanel.getBoundingClientRect();
            sourcePanel.scrollTop += rect.top - pRect.top - 12;
            requestAnimationFrame(function () { flashRect(range.getBoundingClientRect()); });
        }
        function gotoSourceLine(line) {
            var ln = parseInt(line, 10);
            if (!ln || ln < 1) return;
            if (window.__nmCM) {
                var cm = window.__nmCM;
                var l = Math.min(ln - 1, Math.max(0, cm.lineCount() - 1));
                if (!sourceVisible()) {
                    var srcTab = document.querySelector('.nm-tab[data-tab="source"]');
                    if (srcTab) srcTab.click();
                }
                cm.setCursor({ line: l, ch: 0 });
                cm.scrollIntoView({ line: l, ch: 0 }, 80);
                try {
                    cm.addLineClass(l, 'background', 'nm-flash-line');
                    setTimeout(function () { cm.removeLineClass(l, 'background', 'nm-flash-line'); }, 1500);
                } catch (e) {}
                return;
            }
            if (!sourcePanel) return;
            if (!sourceVisible()) {
                var tab = document.querySelector('.nm-tab[data-tab="source"]');
                if (tab) tab.click();
            }
            setTimeout(function () { gotoReadonlyLine(ln); }, 30);
        }
        window.__nmGotoSourceLine = gotoSourceLine;
        document.addEventListener('click', function (e) {
            var a = e.target.closest && e.target.closest('.nm-sidebar-toc a');
            if (!a) return;
            var href = a.getAttribute('href') || '';
            if (href.charAt(0) !== '#') return;
            if (!sourceVisible()) return;
            var line = getHeadingMap()[href.slice(1)];
            if (!line) return;
            e.preventDefault();
            e.stopImmediatePropagation();
            gotoSourceLine(line);
        }, true);
        (function initJumpParams() {
            try {
                var u = new URL(window.location.href);
                var src = (u.searchParams.get('src') || '').trim().replace(/\\\\/g, '/');
                var ln = parseInt(u.searchParams.get('line') || '0', 10);
                if (!src || !ln) return;
                if (window.__nmCM) return;
                var expected = '';
                var metaEl = document.getElementById('nm-temp-meta') || document.getElementById('nm-page-meta');
                if (metaEl) {
                    try { expected = String((JSON.parse(metaEl.textContent) || {}).source_path || ''); } catch (err) {}
                }
                if (expected && expected.replace(/\\\\/g, '/') !== src) return;
                gotoSourceLine(ln);
                u.searchParams.delete('src');
                u.searchParams.delete('line');
                window.history.replaceState({}, '', u.pathname + (u.search || '') + u.hash);
            } catch (e) {}
        })();
    });
})();

/* Document tab shortcuts: Ctrl/⌘+Alt+1/2/3, with C as a source fallback. */
(function () {
    var TAB_KEYS = {
        Digit1: 'render', Digit2: 'source', Digit3: 'warnings',
        KeyC: 'source'
    };
    document.addEventListener('keydown', function (event) {
        if (!(event.ctrlKey || event.metaKey) || !event.altKey || event.shiftKey) return;
        var name = TAB_KEYS[String(event.code || '')];
        if (!name) return;
        var tab = document.querySelector('.nm-tab[data-tab="' + name + '"]');
        if (!tab) return;
        event.preventDefault();
        tab.click();
        if (name === 'source' && window.__nmCM) {
            setTimeout(function () { window.__nmCM.focus(); }, 0);
        }
    });
})();

"""

# 共用页面 id/EventSource 引导脚本：维护 window.name 中的 tab id，并上报活跃时间。
_DEFAULT_JS = _DEFAULT_JS.replace("__NM_CLIENT_BOOTSTRAP__", CLIENT_BOOTSTRAP_JS)
