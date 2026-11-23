"""Build a structural project overview for the generated landing page.

The overview is deterministic and static: it uses the search index (sections,
dtags, index terms), directory names, and internal links.  It deliberately
does not call an LLM; snippets are extracted from the existing index.
"""

from __future__ import annotations

import datetime
import hashlib
import html
import json
import posixpath
import re
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

from notemanager.index_body import extract_render_panel_inner_html

MAX_TOPICS = 20
TREEMAP_TOPICS = 12
MAX_TOPIC_DOCS = 12
MAX_CORE_DOCS = 8
MAX_RECENT_DOCS = 6

_KIND_LABELS = {
    "directory": "目录",
    "tag": "标签",
    "index": "索引",
    "chapter": "章节",
    "document": "文档",
    "other": "其他",
}

_KIND_PRIORITY = ("tag", "index", "directory", "document", "chapter")

_GENERIC_CHAPTERS = {
    "其他", "参考", "参考文档", "总结", "简介", "前言", "目录", "附录", "索引",
    "说明", "概述", "背景", "目标", "示例", "结论", "致谢", "参考文献",
    "相关阅读", "更新", "变更", "介绍", "小结", "综述", "引言", "序言",
    "结语", "后记", "备注", "补充", "其他有趣的东西", "有趣的东西",
    "overview", "introduction", "summary", "references", "contents",
    "appendix", "background", "conclusion", "notes",
}

_COLORS = {
    "directory": ("#e3f2fd", "#90caf9"),
    "tag": ("#e8f5e9", "#a5d6a7"),
    "index": ("#fff3e0", "#ffcc80"),
    "chapter": ("#f3e5f5", "#ce93d8"),
    "document": ("#eceff1", "#b0bec5"),
    "other": ("#f5f5f5", "#cfcfcf"),
}


def _escape(value) -> str:
    return html.escape(str(value or ""), quote=True)


def _topic_id(name: str) -> str:
    return "topic-" + hashlib.sha1(name.strip().lower().encode("utf-8")).hexdigest()[:10]


def _kind_label(kind: str) -> str:
    return _KIND_LABELS.get(kind, kind)


def _pick_kind(kinds: set[str]) -> str:
    for kind in _KIND_PRIORITY:
        if kind in kinds:
            return kind
    return "document"


def _clean_title(title: str) -> str:
    text = re.sub(r"<[^>]+>", "", str(title or ""))
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _chapter_topic_name(title: str) -> str:
    text = _clean_title(title)
    text = re.sub(r"^\s*\d+(?:[.．]\d+)*[、.．:：\-]\s*", "", text)
    return text.strip(" -—:：")


def _document_topic_name(title: str, path: str) -> str:
    name = _chapter_topic_name(title)
    return name or Path(path).stem


def _is_generic_chapter(name: str) -> bool:
    low = name.strip().lower()
    if not low or low in _GENERIC_CHAPTERS:
        return True
    if re.fullmatch(r"[\d.\s]+", low):
        return True
    return len(name.strip()) < 2


class _LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.hrefs.append(value)


def _internal_link_target(href: str, source_path: str, doc_paths: set[str]) -> str | None:
    try:
        parsed = urlparse(href)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc:
        return None
    target = unquote(parsed.path or "").replace("\\", "/")
    if not target or target.startswith("#") or target.startswith("/"):
        return None
    resolved = posixpath.normpath(
        posixpath.join(posixpath.dirname(source_path), target)
    )
    if resolved.startswith("../") or resolved not in doc_paths:
        return None
    return resolved


def _mtime_for(project_root: Path, source_path: str) -> float:
    if not source_path:
        return 0.0
    try:
        p = Path(source_path)
        if not p.is_absolute():
            p = project_root / p
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _snippet_from_grep(entries: list[dict]) -> str:
    for entry in entries:
        text = re.sub(r"\s+", " ", str(entry.get("text") or "")).strip()
        if len(text) >= 20:
            return text[:170] + ("…" if len(text) > 170 else "")
    return ""


def build_overview(project_root: Path, html_dir: Path, documents: list[dict]) -> dict | None:
    """Build overview data; return None when the search index is unavailable."""
    index_dir = html_dir / "_index"
    index_file = index_dir / "search_index.json"
    if not index_file.is_file():
        return None
    try:
        index_data = json.loads(index_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    entries = index_data.get("documents") or []
    if not isinstance(entries, list):
        return None

    project_root = Path(project_root)
    meta: dict[str, dict] = {}
    for doc in documents:
        path = str(doc.get("path") or "").replace("\\", "/")
        if not path:
            continue
        body = str(doc.get("body") or "")
        if 'id="nm-panel-render"' in body:
            body = extract_render_panel_inner_html(body)
        source_path = str(doc.get("source_path") or "")
        meta[path] = {
            "path": path,
            "title": _clean_title(doc.get("title") or Path(path).stem),
            "source_path": source_path,
            "body": body,
            "chapters": [],
            "dtags": [],
            "index_terms": [],
            "grep": [],
            "inlinks": 0,
            "mtime": _mtime_for(project_root, source_path),
        }
    if not meta:
        return None

    doc_paths = set(meta)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_path = str(entry.get("path") or "")
        path = raw_path.split("#", 1)[0]
        if path not in meta:
            continue
        typ = entry.get("type")
        if typ == "chapter":
            meta[path]["chapters"].append(entry)
        elif typ == "dtag":
            meta[path]["dtags"].append(entry)
        elif typ == "index":
            meta[path]["index_terms"].append(entry)
        elif typ == "grep":
            meta[path]["grep"].append(entry)

    link_count = 0
    for path, m in meta.items():
        parser = _LinkExtractor()
        try:
            parser.feed(m["body"])
            parser.close()
        except Exception:
            pass
        targets = set()
        for href in parser.hrefs:
            target = _internal_link_target(href, path, doc_paths)
            if target and target != path:
                targets.add(target)
        for target in targets:
            meta[target]["inlinks"] += 1
        link_count += len(targets)

    for m in meta.values():
        m["snippet"] = _snippet_from_grep(m["grep"])
        m["chapter_count"] = len(m["chapters"])
        m["doc_score"] = (
            m["inlinks"] * 5
            + m["chapter_count"] * 1.5
            + len(m["dtags"]) * 2
            + min(6.0, len(m["grep"]) * 0.2)
        )

    topics: dict[str, dict] = {}

    def add_topic(kind: str, name: str, paths: set[str]) -> None:
        clean = _clean_title(name)
        key = clean.lower()
        if not key or not paths:
            return
        topic = topics.get(key)
        if topic is None:
            topic = {
                "name": clean,
                "kinds": set(),
                "paths": set(),
            }
            topics[key] = topic
        topic["kinds"].add(kind)
        topic["paths"].update(p for p in paths if p in meta)

    # 1. Top-level directory groups.
    for path in meta:
        parts = path.split("/")
        if len(parts) > 1:
            add_topic("directory", parts[0], {path})

    # 2. Explicit document tags.
    for path, m in meta.items():
        for entry in m["dtags"]:
            add_topic("tag", str(entry.get("title") or ""), {path})

    # 3. RST index terms (only terms shared by at least two documents; a
    # single-document index term is usually too narrow for an overview).
    index_groups: dict[str, set[str]] = defaultdict(set)
    index_display: dict[str, str] = {}
    for path, m in meta.items():
        for entry in m["index_terms"]:
            name = _clean_title(str(entry.get("title") or ""))
            key = name.lower()
            if not key:
                continue
            index_display.setdefault(key, name)
            index_groups[key].add(path)
    for key, paths in index_groups.items():
        if len(paths) >= 2:
            add_topic("index", index_display[key], paths)

    # 3.5 Root-level documents.  In a small project (e.g. one chapter per
    # document) the documents themselves are the useful topics; in a large
    # project group many root documents under one fallback topic.
    root_paths = [path for path in meta if "/" not in path]
    if root_paths:
        if len(meta) <= 80:
            for path in root_paths:
                add_topic("document", _document_topic_name(meta[path]["title"], path), {path})
        elif len(root_paths) >= 5:
            add_topic("directory", "根目录", set(root_paths))

    # 4. Repeated chapter titles (sub-topics), but only when directory/tag/
    # index topics do not already provide enough structure.  This keeps
    # generic repeated headings ("介绍", "小结") from crowding out real topics.
    structural_count = sum(
        1 for t in topics.values() if t["kinds"] & {"directory", "tag", "index"}
    )
    chapter_groups: dict[str, set[str]] = defaultdict(set)
    chapter_display: dict[str, str] = {}
    for path, m in meta.items():
        for entry in m["chapters"]:
            name = _chapter_topic_name(str(entry.get("title") or ""))
            if _is_generic_chapter(name):
                continue
            key = name.lower()
            chapter_display.setdefault(key, name)
            chapter_groups[key].add(path)
    if structural_count < 8:
        for key, paths in chapter_groups.items():
            if len(paths) >= 2:
                add_topic("chapter", chapter_display[key], paths)

    # 5. Small/flat projects without directory/tag/index signals: documents
    # themselves are useful entry points.  Do not add every document when a
    # meaningful directory/tag structure already exists.
    has_structural_topics = any(
        t["kinds"] & {"directory", "tag", "index"} for t in topics.values()
    )
    if (
        len(topics) < 8
        and len(meta) <= 80
        and (not has_structural_topics or structural_count <= 1)
    ):
        for path, m in meta.items():
            add_topic("document", _document_topic_name(m["title"], path), {path})

    # 6. Last resort for a flat project with no directory/tag/index signals.
    if not topics:
        for path, m in sorted(meta.items(), key=lambda kv: kv[1]["doc_score"], reverse=True)[:12]:
            add_topic("document", _document_topic_name(m["title"], path), {path})

    built_topics = []
    for topic in topics.values():
        paths = list(topic["paths"])
        if not paths:
            continue
        kind = _pick_kind(topic["kinds"])
        section_count = sum(meta[p]["chapter_count"] for p in paths)
        score = (
            sum(meta[p]["doc_score"] for p in paths)
            + len(paths) * 4
            + section_count * 0.5
        )
        docs = []
        for path in sorted(paths, key=lambda p: meta[p]["doc_score"], reverse=True):
            m = meta[path]
            docs.append({
                "title": m["title"],
                "path": path,
                "source_path": m["source_path"],
                "snippet": m["snippet"],
                "score": round(m["doc_score"], 2),
                "chapters": [
                    {
                        "title": _clean_title(ch.get("title") or ""),
                        "path": str(ch.get("path") or ""),
                    }
                    for ch in m["chapters"][:3]
                ],
            })
        docs = docs[:MAX_TOPIC_DOCS]
        summary = next((d["snippet"] for d in docs if d["snippet"]), "")
        built_topics.append({
            "id": _topic_id(topic["name"]),
            "name": topic["name"],
            "kind": kind,
            "kind_label": _kind_label(kind),
            "doc_count": len(paths),
            "section_count": section_count,
            "score": round(score, 2),
            "summary": summary,
            "documents": docs,
            "has_more_docs": len(paths) > len(docs),
        })

    built_topics.sort(key=lambda t: (t["score"], t["doc_count"]), reverse=True)
    top_topics = built_topics[:MAX_TOPICS]
    more_topics = [
        {
            "id": t["id"],
            "name": t["name"],
            "kind": t["kind"],
            "kind_label": t["kind_label"],
            "doc_count": t["doc_count"],
        }
        for t in built_topics[MAX_TOPICS:]
    ]

    # A treemap needs a handful of comparable structural topics.  For a
    # flat project where the only entries are one-document topics, a treemap
    # degenerates into a field of tiny unreadable tiles; use the topic cards
    # (and the full topics page) instead.
    structural = [
        t for t in built_topics if t["kind"] in ("directory", "tag", "index")
    ]
    chapter_topics = [
        t for t in built_topics if t["kind"] == "chapter" and t["doc_count"] >= 2
    ]
    treemap_source = structural if len(structural) >= 3 else structural + chapter_topics
    treemap = []
    if len(treemap_source) >= 3:
        for t in treemap_source[:TREEMAP_TOPICS]:
            treemap.append({
                "id": t["id"],
                "name": t["name"],
                "kind": t["kind"],
                "kind_label": t["kind_label"],
                "doc_count": t["doc_count"],
                "weight": max(float(t["score"]), 1.0),
                "summary": t["summary"],
            })
        if len(treemap_source) > TREEMAP_TOPICS:
            treemap.append({
                "id": "",
                "name": "其他主题",
                "kind": "other",
                "kind_label": _kind_label("other"),
                "doc_count": sum(t["doc_count"] for t in treemap_source[TREEMAP_TOPICS:]),
                "weight": sum(max(float(t["score"]), 1.0) for t in treemap_source[TREEMAP_TOPICS:]),
            })

    core_docs = []
    for path, m in sorted(meta.items(), key=lambda kv: kv[1]["doc_score"], reverse=True)[:MAX_CORE_DOCS]:
        core_docs.append({
            "title": m["title"],
            "path": path,
            "snippet": m["snippet"],
            "chapter_count": m["chapter_count"],
            "inlinks": m["inlinks"],
            "score": round(m["doc_score"], 2),
        })

    recent_docs = []
    for path, m in sorted(meta.items(), key=lambda kv: kv[1]["mtime"], reverse=True)[:MAX_RECENT_DOCS]:
        date = ""
        if m["mtime"]:
            try:
                date = datetime.datetime.fromtimestamp(m["mtime"]).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                date = ""
        recent_docs.append({
            "title": m["title"],
            "path": path,
            "date": date,
        })

    tags = {
        str(e.get("title") or "").strip()
        for m in meta.values()
        for e in m["dtags"]
        if str(e.get("title") or "").strip()
    }
    index_terms = {
        str(e.get("title") or "").strip()
        for m in meta.values()
        for e in m["index_terms"]
        if str(e.get("title") or "").strip()
    }
    chapter_total = sum(m["chapter_count"] for m in meta.values())

    return {
        "version": 1,
        "project": project_root.name,
        "stats": {
            "documents": len(meta),
            "chapters": chapter_total,
            "tags": len(tags),
            "index_terms": len(index_terms),
            "links": link_count,
            "topics": len(built_topics),
        },
        "topics": top_topics,
        "more_topics": more_topics,
        "treemap": treemap,
        "core_docs": core_docs,
        "recent_docs": recent_docs,
    }


def build_and_write(project_root: Path, html_dir: Path, documents: list[dict]) -> dict | None:
    """Build overview data, write overview.json and topics.html."""
    overview = build_overview(project_root, html_dir, documents)
    if overview is None:
        return None
    index_dir = html_dir / "_index"
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "overview.json").write_text(
        json.dumps(overview, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (html_dir / "topics.html").write_text(
        render_topics_page(overview),
        encoding="utf-8",
    )
    return overview


def _binary_layout(items: list[dict], x: float, y: float, w: float, h: float) -> list[tuple[dict, float, float, float, float]]:
    if not items:
        return []
    if len(items) == 1:
        return [(items[0], x, y, w, h)]
    total = sum(float(i.get("weight") or 1.0) for i in items)
    if total <= 0:
        return []
    acc = 0.0
    split = 1
    for i, item in enumerate(items[:-1], start=1):
        acc += float(item.get("weight") or 1.0)
        split = i
        if acc >= total / 2:
            break
    left = items[:split]
    right = items[split:]
    left_weight = sum(float(i.get("weight") or 1.0) for i in left)
    ratio = left_weight / total if total else 0.5
    if w >= h:
        lw = w * ratio
        return (
            _binary_layout(left, x, y, lw, h)
            + _binary_layout(right, x + lw, y, w - lw, h)
        )
    lh = h * ratio
    return (
        _binary_layout(left, x, y, w, lh)
        + _binary_layout(right, x, y + lh, w, h - lh)
    )


def _treemap_svg(overview: dict) -> str:
    items = overview.get("treemap") or []
    if not items:
        return ""
    items = sorted(items, key=lambda i: float(i.get("weight") or 1.0), reverse=True)
    height = 220.0 if len(items) <= 4 else (280.0 if len(items) <= 8 else 320.0)
    tiles = _binary_layout(items, 0.0, 0.0, 800.0, height)
    parts = [
        f'<svg class="nm-treemap" viewBox="0 0 800 {height:.0f}" role="img" '
        'aria-label="主题地图" preserveAspectRatio="xMidYMid meet">'
    ]
    for item, x, y, w, h in tiles:
        if w < 4 or h < 4:
            continue
        kind = str(item.get("kind") or "document")
        fill, stroke = _COLORS.get(kind, _COLORS["document"])
        if not item.get("id"):
            href = "topics.html#more" if overview.get("more_topics") else "topics.html"
        else:
            href = f"topics.html#{_escape(item['id'])}"
        title = f"{item.get('name') or ''}（{item.get('doc_count') or 0} 篇）"
        parts.append(f'<a href="{href}" class="nm-tm-tile">')
        parts.append(
            f'<rect x="{x + 1:.1f}" y="{y + 1:.1f}" width="{max(w - 2, 0):.1f}" '
            f'height="{max(h - 2, 0):.1f}" rx="4" fill="{fill}" stroke="{stroke}"/>'
        )
        parts.append(f"<title>{_escape(title)}</title>")
        if w >= 72 and h >= 42:
            name = str(item.get("name") or "")
            max_chars = max(4, int((w - 16) / 12))
            if len(name) > max_chars:
                name = name[:max_chars - 1] + "…"
            parts.append(
                f'<text x="{x + 8:.1f}" y="{y + 20:.1f}" font-size="13" '
                f'font-family="sans-serif" fill="#263238">{_escape(name)}</text>'
            )
        if w >= 72 and h >= 60:
            count = f"{item.get('doc_count') or 0} 篇 · {item.get('kind_label') or ''}"
            parts.append(
                f'<text x="{x + 8:.1f}" y="{y + 38:.1f}" font-size="11" '
                f'font-family="sans-serif" fill="#607d8b">{_escape(count)}</text>'
            )
        if w >= 200 and h >= 100:
            summary = re.sub(r"\s+", " ", str(item.get("summary") or "")).strip()
            if summary:
                max_chars = max(8, int((w - 16) / 11))
                if len(summary) > max_chars:
                    summary = summary[:max_chars - 1] + "…"
                parts.append(
                    f'<text x="{x + 8:.1f}" y="{y + 58:.1f}" font-size="11" '
                    f'font-family="sans-serif" fill="#546e7a">{_escape(summary)}</text>'
                )
        parts.append("</a>")
    parts.append("</svg>")
    return "".join(parts)


def _doc_list(items: list[dict], limit: int | None = None) -> str:
    out = ['<ul class="nm-overview-doc-list">']
    for doc in items[:limit] if limit else items:
        out.append(
            '<li><a href="{href}">{title}</a>'.format(
                href=_escape(doc.get("path")),
                title=_escape(doc.get("title")),
            )
        )
        snippet = str(doc.get("snippet") or "").strip()
        if snippet:
            out.append(f'<div class="nm-overview-snippet">{_escape(snippet)}</div>')
        out.append("</li>")
    out.append("</ul>")
    return "".join(out)


def render_landing_overview(overview: dict | None) -> str:
    if not overview:
        return "<h1>NoteManager</h1>"
    stats = overview.get("stats") or {}
    project = str(overview.get("project") or "")
    parts = [
        '<div class="nm-overview">',
        f'<h1 class="nm-overview-title">{_escape(project or "项目概览")}</h1>',
        '<div class="nm-overview-stats">',
        f'{stats.get("documents", 0)} 篇文档 · '
        f'{stats.get("chapters", 0)} 个章节 · '
        f'{stats.get("tags", 0)} 个标签 · '
        f'{stats.get("index_terms", 0)} 个索引词 · '
        f'{stats.get("links", 0)} 条内部链接',
        "</div>",
    ]
    treemap = _treemap_svg(overview)
    if treemap:
        parts.append('<h2 class="nm-overview-heading">主题地图</h2>')
        parts.append(treemap)
    topics = overview.get("topics") or []
    if topics:
        parts.append('<h2 class="nm-overview-heading">重点主题</h2>')
        parts.append('<div class="nm-topic-grid">')
        for topic in topics[:6]:
            parts.append('<div class="nm-topic-card">')
            parts.append(
                f'<h3><a href="topics.html#{_escape(topic.get("id"))}">'
                f'{_escape(topic.get("name"))}</a></h3>'
            )
            parts.append(
                '<div class="nm-topic-meta">'
                f'{_escape(topic.get("kind_label"))} · '
                f'{topic.get("doc_count", 0)} 篇 · '
                f'{topic.get("section_count", 0)} 节</div>'
            )
            summary = str(topic.get("summary") or "").strip()
            if summary:
                parts.append(f'<p class="nm-topic-summary">{_escape(summary)}</p>')
            parts.append(_doc_list(topic.get("documents") or [], limit=3))
            if topic.get("has_more_docs"):
                parts.append(
                    f'<a class="nm-more-link" href="topics.html#{_escape(topic.get("id"))}">'
                    "查看该主题全部内容 →</a>"
                )
            parts.append("</div>")
        parts.append("</div>")
        parts.append('<p class="nm-overview-more"><a href="topics.html">查看全部主题 →</a></p>')
    else:
        parts.append(
            '<p class="nm-overview-empty">尚未提取到主题；可以继续使用左侧目录树或搜索。</p>'
        )

    core = overview.get("core_docs") or []
    recent = overview.get("recent_docs") or []
    if core or recent:
        parts.append('<div class="nm-overview-columns">')
        if core:
            parts.append('<div class="nm-overview-column"><h2 class="nm-overview-heading">核心文档</h2>')
            parts.append('<ul class="nm-doc-list">')
            for doc in core:
                parts.append(
                    '<li><a href="{href}">{title}</a>'
                    '<span class="nm-doc-meta"> {chapters} 节 · 被引 {inlinks} 次</span></li>'.format(
                        href=_escape(doc.get("path")),
                        title=_escape(doc.get("title")),
                        chapters=doc.get("chapter_count", 0),
                        inlinks=doc.get("inlinks", 0),
                    )
                )
            parts.append("</ul></div>")
        if recent:
            parts.append('<div class="nm-overview-column"><h2 class="nm-overview-heading">最近更新</h2>')
            parts.append('<ul class="nm-doc-list">')
            for doc in recent:
                date = str(doc.get("date") or "")
                parts.append(
                    '<li><a href="{href}">{title}</a>'
                    '<span class="nm-doc-meta"> {date}</span></li>'.format(
                        href=_escape(doc.get("path")),
                        title=_escape(doc.get("title")),
                        date=_escape(date),
                    )
                )
            parts.append("</ul></div>")
        parts.append("</div>")
    parts.append("</div>")
    return "".join(parts)


def render_topics_page(overview: dict) -> str:
    project = str(overview.get("project") or "NoteManager")
    stats = overview.get("stats") or {}
    topics = overview.get("topics") or []
    more = overview.get("more_topics") or []
    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f"<title>主题概览 - {_escape(project)}</title>",
        '<link rel="stylesheet" href="_static/notemanager.css">',
        "<style>",
        "body { background: #f7f7f7; }",
        ".nm-topics-page { max-width: 980px; margin: 0 auto; padding: 20px 20px 60px; }",
        ".nm-topics-header { margin-bottom: 18px; }",
        ".nm-topics-header h1 { margin: 0 0 4px; font-size: 24px; }",
        ".nm-topics-header a { color: #0066cc; text-decoration: none; font-size: 13px; }",
        ".nm-topics-stats { color: #777; font-size: 13px; margin-bottom: 18px; }",
        ".nm-topic-section { background: #fff; border-radius: 8px; padding: 16px 18px;",
        "  margin-bottom: 14px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }",
        ".nm-topic-section h2 { margin: 0 0 4px; font-size: 18px; }",
        ".nm-topic-kind { font-size: 11px; color: #1565c0; background: #e3f2fd;",
        "  border-radius: 8px; padding: 1px 6px; margin-left: 6px; vertical-align: middle; }",
        ".nm-topic-count { font-size: 12px; color: #888; margin-left: 6px; font-weight: normal; }",
        ".nm-topic-summary { color: #555; font-size: 13px; margin: 6px 0 10px; }",
        ".nm-topic-doc-list { list-style: none; padding: 0; margin: 0; }",
        ".nm-topic-doc-list li { border-top: 1px solid #eee; padding: 8px 0; }",
        ".nm-topic-doc-list a { color: #0066cc; text-decoration: none; font-weight: 500; }",
        ".nm-topic-snippet { color: #777; font-size: 12px; margin-top: 3px; }",
        ".nm-topic-chapters { font-size: 12px; color: #888; margin-top: 4px; }",
        ".nm-topic-chapters a { font-weight: normal; margin-right: 8px; }",
        ".nm-more-topics { background: #fff; border-radius: 8px; padding: 14px 18px; }",
        ".nm-more-topics ul { margin: 8px 0 0; padding-left: 20px; }",
        "</style></head><body>",
        '<div class="nm-topics-page">',
        '<div class="nm-topics-header">',
        f"<h1>主题概览 - {_escape(project)}</h1>",
        '<a href="index.html">← 返回项目首页</a>',
        "</div>",
        '<div class="nm-topics-stats">',
        f'{stats.get("documents", 0)} 篇文档 · '
        f'{stats.get("chapters", 0)} 个章节 · '
        f'{stats.get("topics", 0)} 个主题',
        "</div>",
    ]
    if not topics:
        parts.append('<p class="nm-overview-empty">尚未提取到主题。</p>')
    for topic in topics:
        parts.append(
            f'<section class="nm-topic-section" id="{_escape(topic.get("id"))}">'
        )
        parts.append(
            f'<h2>{_escape(topic.get("name"))}'
            f'<span class="nm-topic-kind">{_escape(topic.get("kind_label"))}</span>'
            f'<span class="nm-topic-count">{topic.get("doc_count", 0)} 篇 · '
            f'{topic.get("section_count", 0)} 节</span></h2>'
        )
        summary = str(topic.get("summary") or "").strip()
        if summary:
            parts.append(f'<p class="nm-topic-summary">{_escape(summary)}</p>')
        parts.append('<ul class="nm-topic-doc-list">')
        for doc in topic.get("documents") or []:
            parts.append("<li>")
            parts.append(
                f'<a href="{_escape(doc.get("path"))}">{_escape(doc.get("title"))}</a>'
            )
            snippet = str(doc.get("snippet") or "").strip()
            if snippet:
                parts.append(f'<div class="nm-topic-snippet">{_escape(snippet)}</div>')
            chapters = doc.get("chapters") or []
            if chapters:
                parts.append('<div class="nm-topic-chapters">')
                for chapter in chapters:
                    parts.append(
                        f'<a href="{_escape(chapter.get("path"))}">'
                        f'{_escape(chapter.get("title"))}</a>'
                    )
                parts.append("</div>")
            parts.append("</li>")
        parts.append("</ul>")
        if topic.get("has_more_docs"):
            parts.append(
                '<p class="nm-topic-snippet">该主题还有更多文档；'
                "请使用首页目录树或搜索继续查看。</p>"
            )
        parts.append("</section>")
    if more:
        parts.append('<section class="nm-more-topics" id="more">')
        parts.append("<h2>更多主题</h2>")
        parts.append("<ul>")
        for topic in more:
            parts.append(
                f'<li>{_escape(topic.get("name"))} '
                f'<span class="nm-topic-count">{topic.get("doc_count", 0)} 篇 · '
                f'{_escape(topic.get("kind_label"))}</span></li>'
            )
        parts.append("</ul></section>")
    parts.append("</div></body></html>")
    return "".join(parts)
