"""Build document backlinks, outgoing links and related-document data.

This is deliberately deterministic and independent of any LLM.  The generated
``_index/relations.json`` is fetched by the per-page related-documents panel.
"""

from __future__ import annotations

import html
import json
import math
import posixpath
import re
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

from notemanager.index_body import extract_render_panel_inner_html

_MAX_ITEMS = 8
_MAX_RELATED = 5
_MAX_SNIPPET = 160
_MAX_TERM_TEXT = 3000

_GENERIC_HEADINGS = {
    "其他", "参考", "参考文档", "总结", "简介", "前言", "目录", "附录", "索引",
    "说明", "概述", "背景", "目标", "示例", "结论", "致谢", "参考文献",
    "相关阅读", "更新", "变更", "介绍", "小结", "综述", "引言", "序言",
    "结语", "后记", "备注", "补充",
    "overview", "introduction", "summary", "references", "contents",
    "appendix", "background", "conclusion", "notes",
}


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.hrefs.append(value)


def _clean(value) -> str:
    text = re.sub(r"<[^>]+>", "", str(value or ""))
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _snippet(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > _MAX_SNIPPET:
        text = text[: _MAX_SNIPPET - 1] + "…"
    return text


def _heading_set(values) -> set[str]:
    out = set()
    for value in values:
        text = _clean(value).strip(" -—:：")
        low = text.lower()
        if not text or low in _GENERIC_HEADINGS or len(text) < 2:
            continue
        out.add(low)
    return out


def _terms(text: str) -> set[str]:
    text = str(text or "")[:_MAX_TERM_TEXT]
    terms: set[str] = set()
    for word in re.findall(r"[A-Za-z][A-Za-z0-9_+\-]{2,}", text.lower()):
        terms.add(word)
    for cjk in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(cjk) < 2:
            continue
        for i in range(len(cjk) - 1):
            terms.add(cjk[i : i + 2])
    return terms


def _target(href: str, source_path: str, doc_paths: set[str]) -> str | None:
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


def build_relations(documents: list[dict], entries: list[dict]) -> dict:
    doc_paths = {str(d.get("path") or "").replace("\\", "/") for d in documents}
    doc_paths.discard("")
    meta: dict[str, dict] = {}
    for doc in documents:
        path = str(doc.get("path") or "").replace("\\", "/")
        if not path:
            continue
        body = str(doc.get("body") or "")
        if 'id="nm-panel-render"' in body:
            body = extract_render_panel_inner_html(body)
        meta[path] = {
            "title": _clean(doc.get("title") or Path(path).stem),
            "body": body,
            "tags": set(),
            "headings": set(),
            "index_terms": set(),
            "grep": [],
            "snippet": "",
            "outgoing": set(),
            "incoming": set(),
        }

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_path = str(entry.get("path") or "")
        path = raw_path.split("#", 1)[0]
        if path not in meta:
            continue
        typ = entry.get("type")
        if typ == "dtag":
            name = _clean(entry.get("title"))
            if name:
                meta[path]["tags"].add(name.lower())
        elif typ == "chapter":
            meta[path]["headings"].add(_clean(entry.get("title")))
        elif typ == "index":
            name = _clean(entry.get("title"))
            if name:
                meta[path]["index_terms"].add(name.lower())
        elif typ == "grep":
            meta[path]["grep"].append(str(entry.get("text") or ""))

    for path, m in meta.items():
        m["headings"] = _heading_set(m["headings"])
        m["terms"] = _terms(
            " ".join([m["title"], *list(m["headings"]), *m["grep"][:3]])
        )
        for text in m["grep"]:
            if len(re.sub(r"\s+", " ", text).strip()) >= 20:
                m["snippet"] = _snippet(text)
                break

    for path, m in meta.items():
        parser = _LinkParser()
        try:
            parser.feed(m["body"])
            parser.close()
        except Exception:
            pass
        for href in parser.hrefs:
            target = _target(href, path, doc_paths)
            if not target or target == path:
                continue
            m["outgoing"].add(target)
            meta[target]["incoming"].add(path)

    def ref_entry(path: str) -> dict:
        m = meta[path]
        return {
            "path": path,
            "title": m["title"],
            "snippet": m["snippet"],
        }

    related: dict[str, list[dict]] = defaultdict(list)
    paths = sorted(meta)
    for i, left in enumerate(paths):
        lm = meta[left]
        for right in paths[i + 1 :]:
            rm = meta[right]
            shared_links = int(right in lm["outgoing"] or left in rm["outgoing"])
            shared_tags = len(lm["tags"] & rm["tags"])
            shared_headings = len(lm["headings"] & rm["headings"])
            shared_index = len(lm["index_terms"] & rm["index_terms"])
            denom = math.sqrt(len(lm["terms"]) * len(rm["terms"])) or 1.0
            overlap = len(lm["terms"] & rm["terms"]) / denom
            score = (
                shared_tags * 5.0
                + shared_headings * 2.0
                + shared_index * 3.0
                + shared_links * 3.0
                + overlap * 8.0
            )
            if score < 1.0:
                continue
            related[left].append((score, right))
            related[right].append((score, left))

    docs: dict[str, dict] = {}
    for path, m in sorted(meta.items()):
        rel = [
            ref_entry(target)
            for _, target in sorted(
                related.get(path, []), key=lambda item: (-item[0], meta[item[1]]["title"])
            )[:_MAX_RELATED]
        ]
        backlinks = [ref_entry(p) for p in sorted(m["incoming"])[:_MAX_ITEMS]]
        outgoing = [ref_entry(p) for p in sorted(m["outgoing"])[:_MAX_ITEMS]]
        docs[path] = {
            "title": m["title"],
            "backlinks": backlinks,
            "outgoing": outgoing,
            "related": rel,
            "backlink_count": len(m["incoming"]),
            "outgoing_count": len(m["outgoing"]),
            "related_count": len(related.get(path, [])),
            "orphan": not backlinks and not outgoing,
        }

    orphans = [
        ref_entry(path)
        for path, data in sorted(docs.items())
        if data["orphan"]
    ]
    return {
        "version": 1,
        "docs": docs,
        "orphans": orphans,
    }


def build_and_write(html_dir: Path, documents: list[dict]) -> dict | None:
    index_file = html_dir / "_index" / "search_index.json"
    if not index_file.is_file():
        return None
    try:
        index_data = json.loads(index_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    entries = index_data.get("documents") or []
    data = build_relations(documents, entries)
    (html_dir / "_index" / "relations.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return data
