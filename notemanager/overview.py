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
import math
import posixpath
import re
import time
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

from notemanager.index_body import extract_render_panel_inner_html

MAX_TOPICS = 20
MAX_TOPIC_DOCS = 12
MAX_CORE_DOCS = 8
MAX_RECENT_DOCS = 6
RECENT_DAYS = 30

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


def _format_mtime(mtime: float) -> str:
    if not mtime:
        return ""
    try:
        return datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
    except (ValueError, OSError):
        return ""


def _new_dir_node(name: str, path: str) -> dict:
    return {
        "name": name,
        "path": path,
        "dirs": {},
        "files": [],
        "doc_count": 0,
        "section_count": 0,
        "inlinks": 0,
        "last_mtime": 0.0,
        "recent_count": 0,
        "score": 0.0,
        "heat": "",
        "bar_width": 0,
    }


def _build_directory_tree(meta: dict, recent_cutoff: float) -> dict:
    """Build a directory tree with counts and recency, independent of topics."""
    root = _new_dir_node("根目录", "")

    for path, m in meta.items():
        parts = path.split("/")
        node = root
        ancestors = [root]
        for idx, part in enumerate(parts[:-1]):
            child = node["dirs"].get(part)
            if child is None:
                child = _new_dir_node(part, "/".join(parts[: idx + 1]))
                node["dirs"][part] = child
            node = child
            ancestors.append(node)

        mtime = float(m.get("mtime") or 0.0)
        recent = 1 if mtime >= recent_cutoff else 0
        file_entry = {
            "title": m["title"],
            "path": path,
            "chapter_count": m["chapter_count"],
            "inlinks": m["inlinks"],
            "mtime": mtime,
            "date": _format_mtime(mtime),
        }
        node["files"].append(file_entry)
        for ancestor in ancestors:
            ancestor["doc_count"] += 1
            ancestor["section_count"] += m["chapter_count"]
            ancestor["inlinks"] += m["inlinks"]
            ancestor["last_mtime"] = max(ancestor["last_mtime"], mtime)
            ancestor["recent_count"] += recent

    def finalize(node: dict) -> None:
        node["dirs"] = dict(sorted(node["dirs"].items()))
        node["files"].sort(key=lambda f: (f["title"].lower(), f["path"]))
        node["score"] = (
            node["doc_count"] * 3.0
            + node["section_count"] * 0.5
            + node["inlinks"] * 2.0
            + node["recent_count"] * 5.0
        )
        node["date"] = _format_mtime(node["last_mtime"])
        for child in node["dirs"].values():
            finalize(child)

    finalize(root)

    top_dirs = list(root["dirs"].values())
    max_docs = max((n["doc_count"] for n in top_dirs), default=0)
    # Highlight the most important top-level directories by rank rather than
    # an absolute score ratio: a huge directory would otherwise leave every
    # sibling unhighlighted.
    ranked = sorted(
        top_dirs,
        key=lambda n: (n["score"], n["doc_count"]),
        reverse=True,
    )
    for rank, node in enumerate(ranked):
        if rank == 0:
            node["heat"] = "hot"
        elif rank < 3:
            node["heat"] = "warm"
        if max_docs > 0:
            node["bar_width"] = int(15 + 85 * ((node["doc_count"] / max_docs) ** 0.5))
    return root


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
    # Directory structure is the primary navigation on the landing page.
    directory_tree = _build_directory_tree(
        meta, time.time() - RECENT_DAYS * 86400
    )

    # Themes are tag/index/chapter/document topics; directory topics are
    # already represented by the tree above.
    themes = [t for t in built_topics if t["kind"] != "directory"]
    top_themes = themes[:MAX_TOPICS]
    more_topics = [
        {
            "id": t["id"],
            "name": t["name"],
            "kind": t["kind"],
            "kind_label": t["kind_label"],
            "doc_count": t["doc_count"],
        }
        for t in themes[MAX_TOPICS:]
    ]
    top_topics = built_topics[:MAX_TOPICS]

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
        "themes": top_themes,
        "more_topics": more_topics,
        "directory_tree": directory_tree,
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


def dir_node_id(path: str) -> str:
    """Stable id shared by the radial graph and the sidebar directory tree."""
    return "nm-dir-" + hashlib.sha1((path or ".").encode("utf-8")).hexdigest()[:10]


def _node_radius(node: dict, depth: int, max_docs: int) -> float:
    if depth == 0:
        return 28.0
    docs = max(1, int(node.get("doc_count") or 0))
    return 8.0 + 22.0 * math.sqrt(docs / max(1, max_docs))


def _leaf_counts(tree: dict) -> dict[str, int]:
    counts: dict[str, int] = {}

    def walk(node: dict) -> int:
        children = list((node.get("dirs") or {}).values())
        if children:
            n = sum(walk(child) for child in children)
        else:
            n = 1
        counts[str(node.get("path") or "")] = n
        return n

    walk(tree)
    return counts


def _radial_layout(tree: dict) -> tuple[dict, float]:
    leaf_counts = _leaf_counts(tree)
    max_docs = max(
        (int(n.get("doc_count") or 0) for n in _walk_dirs(tree)),
        default=1,
    )
    max_node_r = max(
        (_node_radius(n, d, max_docs) for n, d in _walk_dirs_with_depth(tree)),
        default=12.0,
    )
    depth_counts: dict[int, int] = {}
    for _, depth in _walk_dirs_with_depth(tree):
        depth_counts[depth] = depth_counts.get(depth, 0) + 1
    max_depth = max(depth_counts, default=0)
    rings = [0.0]
    for depth in range(1, max_depth + 1):
        required = depth_counts.get(depth, 0) * (2 * max_node_r + 12) / (2 * math.pi)
        if depth == 1:
            rings.append(max(140.0, required))
        else:
            rings.append(max(rings[-1] + 95.0, required))
    max_ring = rings[-1]
    size = 2 * (max_ring + max_node_r + 80.0)
    cx = cy = size / 2
    positions: dict[str, tuple[float, float, dict, int, float, float]] = {}

    def place(node: dict, angle: float, span: float, depth: int) -> None:
        radius = rings[depth]
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        node_r = _node_radius(node, depth, max_docs)
        positions[str(node.get("path") or "")] = (x, y, node, depth, node_r, angle)
        children = list((node.get("dirs") or {}).values())
        if not children:
            return
        n = len(children)
        gap = min(0.05, span * 0.15 / (n - 1)) if n > 1 else 0.0
        usable = max(span - gap * (n - 1), 0.001)
        start = -math.pi / 2 if depth == 0 else angle - span / 2
        total = sum(leaf_counts[str(c.get("path") or "")] for c in children) or 1
        cursor = start
        for child in children:
            child_path = str(child.get("path") or "")
            child_span = usable * leaf_counts[child_path] / total
            child_angle = cursor + child_span / 2
            place(child, child_angle, child_span, depth + 1)
            cursor += child_span + gap

    place(tree, -math.pi / 2, 2 * math.pi, 0)
    return positions, size


def _walk_dirs(tree: dict):
    stack = [tree]
    while stack:
        node = stack.pop()
        yield node
        stack.extend((node.get("dirs") or {}).values())


def _walk_dirs_with_depth(tree: dict):
    stack = [(tree, 0)]
    while stack:
        node, depth = stack.pop()
        yield node, depth
        stack.extend((child, depth + 1) for child in (node.get("dirs") or {}).values())


def _graph_node_colors(node: dict, depth: int) -> tuple[str, str]:
    if depth == 0:
        return "#eceff1", "#90a4ae"
    heat = str(node.get("heat") or "")
    if heat == "hot":
        return "#e3f2fd", "#42a5f5"
    if heat == "warm":
        return "#f4f9ff", "#90caf9"
    return "#ffffff", "#cfd8dc"


def _node_title(node: dict, depth: int, project: str) -> str:
    name = project if depth == 0 else str(node.get("name") or "")
    bits = [
        f"{name}：{node.get('doc_count', 0)} 篇，"
        f"{node.get('section_count', 0)} 节"
    ]
    if node.get("recent_count"):
        bits.append(f"近 {RECENT_DAYS} 天 {node['recent_count']} 篇")
    if node.get("date"):
        bits.append(f"最近 {node['date']}")
    if node.get("inlinks"):
        bits.append(f"被引 {node['inlinks']}")
    return "；".join(bits)


def _render_radial_node(
    x: float,
    y: float,
    node: dict,
    depth: int,
    node_r: float,
    angle: float,
    project: str,
) -> str:
    name = project if depth == 0 else str(node.get("name") or "")
    fill, stroke = _graph_node_colors(node, depth)
    node_id = dir_node_id(str(node.get("path") or ""))
    title = _node_title(node, depth, project)
    count = str(node.get("doc_count", 0))
    parts = []
    if depth == 0:
        parts.append('<g class="nm-g-node nm-g-root">')
    else:
        parts.append(
            f'<a class="nm-g-node" href="#{node_id}" '
            f'onclick="return nmRevealSidebarDir(\'{node_id}\')">'
        )
    parts.append(f"<title>{_escape(title)}</title>")
    parts.append(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{node_r:.1f}" '
        f'fill="{fill}" stroke="{stroke}"/>'
    )
    if node_r >= 12:
        parts.append(
            f'<text x="{x:.1f}" y="{y + 3:.1f}" font-size="9" text-anchor="middle" '
            f'font-family="sans-serif" fill="#546e7a">{_escape(count)}</text>'
        )
    if node.get("recent_count"):
        parts.append(
            f'<circle cx="{x + node_r * 0.7:.1f}" cy="{y - node_r * 0.7:.1f}" '
            'r="3" fill="#43a047"/>'
        )
    # Labels sit outside the circle so the diameter remains readable.
    if depth == 0:
        lx, ly = x, y + node_r + 15
        anchor = "middle"
    else:
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        lx = x + cos_a * (node_r + 6)
        ly = y + sin_a * (node_r + 6)
        if cos_a > 0.25:
            anchor = "start"
        elif cos_a < -0.25:
            anchor = "end"
        else:
            anchor = "middle"
    label = name
    if len(label) > 13:
        label = label[:12] + "…"
    parts.append(
        f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="11" text-anchor="{anchor}" '
        'dominant-baseline="middle" font-family="sans-serif" fill="#37474f">'
        f"{_escape(label)}</text>"
    )
    parts.append("</a>" if depth else "</g>")
    return "".join(parts)


def _graph_bounds(positions: dict, project: str) -> tuple[float, float, float, float]:
    """Tight bounding box for circles + outside labels, including padding."""
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    for _, (x, y, node, depth, node_r, angle) in positions.items():
        min_x = min(min_x, x - node_r)
        max_x = max(max_x, x + node_r)
        min_y = min(min_y, y - node_r)
        max_y = max(max_y, y + node_r)

        name = project if depth == 0 else str(node.get("name") or "")
        label = name if len(name) <= 13 else name[:12] + "…"
        text_w = len(label) * 11.0
        text_h = 12.0
        if depth == 0:
            lx, ly = x, y + node_r + 15
            left, right = lx - text_w / 2, lx + text_w / 2
        else:
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            lx, ly = x + cos_a * (node_r + 6), y + sin_a * (node_r + 6)
            if cos_a > 0.25:
                left, right = lx, lx + text_w
            elif cos_a < -0.25:
                left, right = lx - text_w, lx
            else:
                left, right = lx - text_w / 2, lx + text_w / 2
        min_x = min(min_x, left)
        max_x = max(max_x, right)
        min_y = min(min_y, ly - text_h / 2)
        max_y = max(max_y, ly + text_h / 2)
    pad = 16.0
    return min_x - pad, min_y - pad, (max_x - min_x) + 2 * pad, (max_y - min_y) + 2 * pad


def _render_directory_graph(tree: dict, project: str) -> str:
    positions, _size = _radial_layout(tree)
    vx, vy, vw, vh = _graph_bounds(positions, project)
    parts = [
        '<div class="nm-dir-graph-wrap">',
        f'<svg class="nm-dir-graph" viewBox="{vx:.0f} {vy:.0f} {vw:.0f} {vh:.0f}" '
        'role="img" aria-label="目录关系图">',
    ]
    # Edges first, clipped to the circle boundaries.
    for path, (x, y, node, depth, node_r, angle) in positions.items():
        for child in (node.get("dirs") or {}).values():
            child_pos = positions.get(str(child.get("path") or ""))
            if not child_pos:
                continue
            cx, cy, _, _, child_r, _ = child_pos
            dx, dy = cx - x, cy - y
            dist = math.hypot(dx, dy)
            if dist <= 0:
                continue
            ux, uy = dx / dist, dy / dist
            x1, y1 = x + ux * (node_r + 1), y + uy * (node_r + 1)
            x2, y2 = cx - ux * (child_r + 1), cy - uy * (child_r + 1)
            parts.append(
                f'<line class="nm-g-edge" x1="{x1:.1f}" y1="{y1:.1f}" '
                f'x2="{x2:.1f}" y2="{y2:.1f}"/>'
            )
    for path, (x, y, node, depth, node_r, angle) in positions.items():
        parts.append(
            _render_radial_node(x, y, node, depth, node_r, angle, project)
        )
    parts.append("</svg></div>")
    return "".join(parts)


def _compact_dir_node(node: dict, name: str) -> dict:
    return {
        "id": dir_node_id(str(node.get("path") or "")),
        "name": name,
        "path": str(node.get("path") or ""),
        "doc_count": int(node.get("doc_count") or 0),
        "section_count": int(node.get("section_count") or 0),
        "recent_count": int(node.get("recent_count") or 0),
        "date": str(node.get("date") or ""),
        "inlinks": int(node.get("inlinks") or 0),
        "heat": str(node.get("heat") or ""),
        "children": [
            _compact_dir_node(child, str(child.get("name") or ""))
            for child in (node.get("dirs") or {}).values()
        ],
    }


def render_directory_overview(overview: dict | None) -> str:
    if not overview:
        return ""
    tree = overview.get("directory_tree")
    if not tree:
        return ""
    dirs = tree.get("dirs") or {}
    if not dirs:
        # A flat project has no directory structure to draw; the sidebar still
        # lists its root files.
        return ""
    project = str(overview.get("project") or "")
    graph_data = _compact_dir_node(tree, project)
    graph_json = json.dumps(graph_data, ensure_ascii=False, separators=(",", ":")).replace(
        "<", "\\u003c"
    )
    return (
        '<section class="nm-dir-overview">'
        '<h2 class="nm-overview-heading">目录概览</h2>'
        '<p class="nm-dir-hint">从中心向外展示目录层级；圆直径表示文档数，'
        "蓝色表示重要目录，绿色圆点表示近期修改。点击有子目录的节点可将它设为新的根；"
        "叶子节点打开侧边栏，返回按钮回到上一视图。</p>"
        '<div class="nm-dir-nav">'
        '<button id="nm-dir-back" type="button" disabled>← 返回</button>'
        '<button id="nm-dir-root" type="button" disabled>⌂ 根</button>'
        '<span id="nm-dir-breadcrumb"></span>'
        '<button id="nm-dir-open-sidebar" type="button">侧栏定位</button>'
        "</div>"
        '<div class="nm-dir-graph-tools">'
        '<label for="nm-dir-graph-scale">比例</label>'
        '<input id="nm-dir-graph-scale" type="range" min="10" max="300" step="5" value="100">'
        '<span id="nm-dir-graph-scale-label">100%</span>'
        '<button id="nm-dir-graph-scale-fit" type="button">适应</button>'
        '<button id="nm-dir-graph-scale-reset" type="button">重置</button>'
        "</div>"
        + _render_directory_graph(tree, project)
        + f'<script type="application/json" id="nm-dir-graph-data">{graph_json}</script>'
        + "</section>"
    )


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
        '<section id="nm-reading-home" class="nm-reading-home" '
        'data-nm-activity-src="_index/activity.json"></section>',
        '<section id="nm-saved-views" class="nm-saved-views"></section>',
    ]
    directory_html = render_directory_overview(overview)
    if directory_html:
        parts.append(directory_html)

    themes = overview.get("themes") or []
    if themes:
        parts.append('<h2 class="nm-overview-heading">主题</h2>')
        parts.append('<div class="nm-topic-grid">')
        for topic in themes[:6]:
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
    topics = overview.get("themes") or []
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
        f'{len(topics)} 个主题',
        "</div>",
    ]
    if not topics:
        parts.append(
            '<p class="nm-overview-empty">当前工程没有独立的标签/章节主题；'
            '请使用首页的目录概览定位内容。</p>'
        )
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
