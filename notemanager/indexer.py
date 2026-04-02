"""Search index builder for NoteManager.

Extracts structured entries from rendered HTML:
- chapter: section headings with anchors
- dtag: document tags
- index: inline index terms (:index: role)
- label: cross-reference anchors (.. _label:)
- index-directive: .. index:: entries from RST source
- grep: text blocks from rendered main content (not raw source)
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

from notemanager.index_body import extract_render_panel_inner_html

# <section id="xxx">...<hN>title</hN>
_SECTION_HEADING_RE = re.compile(
    r'<section[^>]*\bid="([^"]+)"[^>]*>.*?<h(\d+)>(.*?)</h\2>',
    re.DOTALL,
)

# <hN id="xxx">title</hN>  (Markdown-style, no section wrapper)
_DIRECT_HEADING_RE = re.compile(
    r'<h(\d+)\s[^>]*\bid="([^"]+)"[^>]*>(.*?)</h\1>',
    re.DOTALL,
)

# <span class="dtag">tag1, tag2</span>
_DTAG_RE = re.compile(r'<span class="dtag">([^<]+)</span>')

# <span class="index-term">term</span>
_INDEX_TERM_RE = re.compile(r'<span class="index-term">([^<]+)</span>')

# .. index:: term
_RST_INDEX_DIRECTIVE_RE = re.compile(r'^\.\.\s+index::\s+(.+)$', re.MULTILINE)

_GREP_BLOCK_TAGS = frozenset({
    "p", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "td", "th", "blockquote", "pre", "dt", "dd", "caption",
    # Sphinx / HTML5 常见容器，避免正文落在 section/main 的直接文本节点里被漏抽
    "section", "main", "article",
})
_GREP_SKIP_TAGS = frozenset({"script", "style", "noscript", "template"})
_MAX_GREP_NODES_PER_DOC = 600
_MAX_GREP_TEXT_LEN = 4000


def parse_search_query(query: str) -> tuple[str | None, str]:
    """解析 ``chapter:`` / ``grep:`` 等前缀，返回 (类型或 None, 核心查询串)。"""
    raw = query.strip()
    m = re.match(r"^(chapter|label|index|dtag|grep):\s*(.*)$", raw, re.IGNORECASE)
    if m:
        core = (m.group(2) or "").strip()
        return m.group(1).lower(), core
    return None, raw


def filter_search_documents(
    entries: list,
    query: str,
    *,
    limit: int = 500,
) -> list:
    """按与 Web 侧一致的规则过滤索引条目（正则，默认忽略大小写）。"""
    typ, core = parse_search_query(query)
    if not core:
        return []
    try:
        rex = re.compile(core, re.IGNORECASE)
    except re.error:
        rex = re.compile(re.escape(core), re.IGNORECASE)
    results = []
    for e in entries:
        if typ and e.get("type") != typ:
            continue
        title = e.get("title", "") or ""
        text = e.get("text", "") or ""
        snippet = e.get("snippet", "") or ""
        if rex.search(title) or rex.search(text) or rex.search(snippet):
            results.append(e)
            if len(results) >= limit:
                break
    return results


class _GrepBlockExtractor(HTMLParser):
    """从渲染正文中按块级元素抽出纯文本节点（不含 script/style）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nodes: list[str] = []
        self._skip = 0
        self._buffers: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in _GREP_SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if t in _GREP_BLOCK_TAGS:
            self._buffers.append([])

    def handle_endtag(self, tag):
        t = tag.lower()
        if self._skip:
            if t in _GREP_SKIP_TAGS:
                self._skip -= 1
            return
        if t in _GREP_BLOCK_TAGS and self._buffers:
            buf = self._buffers.pop()
            raw = "".join(buf)
            text = _grep_normalize_text(raw)
            if len(text) >= 2:
                self.nodes.append(text[:_MAX_GREP_TEXT_LEN])

    def handle_data(self, data):
        if self._skip or not self._buffers:
            return
        self._buffers[-1].append(data)

    def close(self) -> None:
        super().close()
        while self._buffers:
            buf = self._buffers.pop()
            text = _grep_normalize_text("".join(buf))
            if len(text) >= 2:
                self.nodes.append(text[:_MAX_GREP_TEXT_LEN])


def _grep_normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _extract_grep_nodes(entries: list, path: str, body_html: str, doc_title: str) -> None:
    """从单页渲染正文中追加 type=grep 的索引条目。"""
    try:
        parser = _GrepBlockExtractor()
        parser.feed(body_html)
        parser.close()
    except Exception:
        return
    nodes = parser.nodes[:_MAX_GREP_NODES_PER_DOC]
    base_title = (doc_title or Path(path).stem or "文档")[:120]
    for node_text in nodes:
        snip = node_text[:160] + ("…" if len(node_text) > 160 else "")
        entries.append({
            "type": "grep",
            "title": base_title,
            "path": path,
            "text": node_text,
            "snippet": snip,
        })


def build_index(
    documents: list[dict],
    output_dir: Path,
    ref_labels: dict[str, str] | None = None,
    rst_index_directives: dict[str, list[str]] | None = None,
    dtags_per_doc: dict[str, list[str]] | None = None,
) -> None:
    """Build a categorized search index from rendered HTML documents.

    Args:
        documents: List of dicts with 'title', 'path', 'body' keys.
        output_dir: Directory to write search_index.json to.
        ref_labels: Mapping of label names to HTML file paths.
        rst_index_directives: Mapping of path -> list of index terms
            extracted from RST ``.. index::`` directives.
        dtags_per_doc: Mapping of rel_path -> list of dtag strings
            extracted from source before stripping.
    """
    entries = []

    for doc in documents:
        path = doc["path"]
        body = doc["body"]
        if 'id="nm-panel-render"' in body:
            body = extract_render_panel_inner_html(body)
        title = doc.get("title") or ""
        _extract_chapters(entries, path, body)
        _extract_dtags(entries, path, body)
        _extract_index_terms(entries, path, body)
        _extract_grep_nodes(entries, path, body, title)

    # Add label entries
    if ref_labels:
        for label, target_path in ref_labels.items():
            entries.append({
                "type": "label",
                "title": label,
                "path": f"{target_path}#{label}",
                "text": label,
                "snippet": f"标签锚点: {label}",
            })

    # Add .. index:: directive entries
    if rst_index_directives:
        for path, terms in rst_index_directives.items():
            html_path = str(Path(path).with_suffix(".html"))
            for term in terms:
                entries.append({
                    "type": "index",
                    "title": term.strip(),
                    "path": html_path,
                    "text": term.strip(),
                    "snippet": f"索引条目: {term.strip()}",
                })

    # Add pre-extracted dtag entries
    if dtags_per_doc:
        seen = set()
        for rel_path, tags in dtags_per_doc.items():
            html_path = str(Path(rel_path).with_suffix(".html"))
            for tag in tags:
                tag = tag.strip()
                if tag and (html_path, tag) not in seen:
                    seen.add((html_path, tag))
                    entries.append({
                        "type": "dtag",
                        "title": tag,
                        "path": html_path,
                        "text": tag,
                        "snippet": f"文档标签: {tag}",
                    })

    data = {"documents": entries}
    output_path = output_dir / "search_index.json"
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return len(entries)


def _extract_chapters(entries: list, path: str, html: str) -> None:
    """Extract section headings from rendered HTML."""
    seen_ids = set()
    # RST style: <section id="xxx">...<hN>title</hN>
    for match in _SECTION_HEADING_RE.finditer(html):
        section_id = match.group(1)
        level = int(match.group(2))
        title = _strip_tags(match.group(3))
        if not title:
            continue
        seen_ids.add(section_id)
        entries.append({
            "type": "chapter",
            "title": title,
            "path": f"{path}#{section_id}",
            "text": title,
            "snippet": f"H{level}: {title}",
        })
    # Markdown style: <hN id="xxx">title</hN>
    for match in _DIRECT_HEADING_RE.finditer(html):
        level = int(match.group(1))
        heading_id = match.group(2)
        title = _strip_tags(match.group(3))
        if not title or heading_id in seen_ids:
            continue
        seen_ids.add(heading_id)
        entries.append({
            "type": "chapter",
            "title": title,
            "path": f"{path}#{heading_id}",
            "text": title,
            "snippet": f"H{level}: {title}",
        })


def _extract_dtags(entries: list, path: str, html: str) -> None:
    """Extract document tags from rendered HTML."""
    seen = set()
    for match in _DTAG_RE.finditer(html):
        raw = match.group(1)
        for tag in raw.split(","):
            tag = tag.strip()
            if tag and (path, tag) not in seen:
                seen.add((path, tag))
                entries.append({
                    "type": "dtag",
                    "title": tag,
                    "path": path,
                    "text": tag,
                    "snippet": f"文档标签: {tag}",
                })


def _extract_index_terms(entries: list, path: str, html: str) -> None:
    """Extract inline index terms from rendered HTML."""
    seen = set()
    for match in _INDEX_TERM_RE.finditer(html):
        term = match.group(1).strip()
        if term and (path, term) not in seen:
            seen.add((path, term))
            entries.append({
                "type": "index",
                "title": term,
                "path": path,
                "text": term,
                "snippet": f"索引条目: {term}",
            })


def extract_md_hashtags(content: str) -> list[str]:
    """从 Markdown 源码中抽取行内 ``#标签``（社交/笔记式标签）。

    ``#`` 须出现在行首、空白或常见中英文标点后，避免把 ``[](#锚点)``、ATX 标题
    ``# 标题``（``#`` 后为空格）等误判为标签。
    """
    content = content.lstrip("\ufeff")
    _MD_HASHTAG_RE = re.compile(
        r"(?:^|[\s:：，。、；！？「」])#([\w\u4e00-\u9fff]+(?:[/+_.-][\w\u4e00-\u9fff]+)*)"
    )
    seen: set[str] = set()
    out: list[str] = []
    for m in _MD_HASHTAG_RE.finditer(content):
        tag = m.group(1).strip()
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


def extract_dtags_from_sources(files) -> dict[str, list[str]]:
    """Extract :dtag: entries from source files.

    Args:
        files: List of FileInfo objects from the scanner.

    Returns:
        Dict mapping relative source path -> list of dtag strings.
    """
    _DTAG_ROLE_RE = re.compile(r":dtag:`([^`]+)`")
    result: dict[str, list[str]] = {}
    for fi in files:
        try:
            content = fi.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        tags: list[str] = []
        if fi.ext == ".rst":
            for m in _DTAG_ROLE_RE.finditer(content):
                for tag in m.group(1).split(","):
                    tag = tag.strip()
                    if tag and tag not in tags:
                        tags.append(tag)
        elif fi.ext == ".md":
            tags = extract_md_hashtags(content)
        if tags:
            result[str(fi.rel_path)] = tags
    return result


def extract_rst_index_directives(files) -> dict[str, list[str]]:
    """Extract ``.. index::`` directives from RST source files.

    Args:
        files: List of FileInfo objects from the scanner.

    Returns:
        Dict mapping relative RST path -> list of index terms.
    """
    result = {}
    for fi in files:
        if fi.ext != ".rst":
            continue
        try:
            content = fi.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        terms = _RST_INDEX_DIRECTIVE_RE.findall(content)
        if terms:
            result[str(fi.rel_path)] = terms
    return result


def _strip_tags(html: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", "", html).strip()
