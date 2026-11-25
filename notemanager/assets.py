"""Build a lightweight image-asset reference index.

The index powers safe note deletion (only exclusively-referenced images can be
removed with the note) and a future orphan-asset view.  It is intentionally
deterministic and only looks at image references in source documents.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from notemanager.scanner import is_ignored_path, load_ignore_rules

_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"})
_SKIP_DIRS = frozenset({".NoteManager", ".git", ".svn", ".hg", "_build", "_extensions", "__pycache__"})
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)")
_HTML_IMAGE_RE = re.compile(r"<img\b[^>]*\bsrc\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
_RST_IMAGE_RE = re.compile(r"^\s*\.\.\s+(?:image|figure)::\s*(\S+)", re.MULTILINE)


def _is_annotation_sidecar(path: str | Path) -> bool:
    return str(path).lower().endswith(".ann.svg")


def _clean_ref(raw: str) -> str | None:
    value = unquote(str(raw or "").strip()).strip("<>\"'")
    if not value or value.startswith(("http://", "https://", "data:", "mailto:", "#")):
        return None
    parsed = urlsplit(value)
    path = parsed.path.replace("\\", "/")
    if not path or path.startswith("/"):
        return None
    if _is_annotation_sidecar(path):
        return None
    if Path(path).suffix.lower() not in _IMAGE_EXTS:
        return None
    return path


def _doc_refs(source_rel: str, text: str) -> set[str]:
    refs: set[str] = set()
    for pattern in (_MD_IMAGE_RE, _HTML_IMAGE_RE, _RST_IMAGE_RE):
        for match in pattern.finditer(text):
            raw = _clean_ref(match.group(1))
            if not raw:
                continue
            resolved = posixpath.normpath(
                posixpath.join(posixpath.dirname(source_rel), raw)
            )
            if resolved.startswith("../") or resolved.startswith("/"):
                continue
            refs.add(resolved)
    return refs


def build_assets(project_root: Path, documents: list[dict]) -> dict:
    project_root = Path(project_root)
    doc_by_source = {}
    for doc in documents:
        source = str(doc.get("source_path") or "").replace("\\", "/")
        if not source:
            continue
        doc_by_source[source] = {
            "source_path": source,
            "html_path": str(doc.get("path") or ""),
            "title": str(doc.get("title") or Path(source).stem),
        }

    ignore_rules = load_ignore_rules(project_root)
    assets: dict[str, dict] = {}
    for source, meta in sorted(doc_by_source.items()):
        try:
            text = (project_root / source).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ref in _doc_refs(source, text):
            if is_ignored_path(
                project_root, project_root / ref, is_dir=False, rules=ignore_rules
            ):
                continue
            entry = assets.setdefault(ref, {
                "path": ref,
                "referenced_by": [],
                "exists": False,
                "size": 0,
            })
            if meta["source_path"] not in {r["source_path"] for r in entry["referenced_by"]}:
                entry["referenced_by"].append(meta)

    # Include image files that exist in the project, even if no document
    # currently references them; those are the cleanup candidates.
    for dirpath, dirnames, filenames in os.walk(project_root):
        base = Path(dirpath)
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS
            and not is_ignored_path(
                project_root, base / d, is_dir=True, rules=ignore_rules
            )
        ]
        for name in filenames:
            path = base / name
            if _is_annotation_sidecar(path):
                continue
            if path.suffix.lower() not in _IMAGE_EXTS:
                continue
            if is_ignored_path(
                project_root, path, is_dir=False, rules=ignore_rules
            ):
                continue
            rel = path.relative_to(project_root).as_posix()
            entry = assets.setdefault(rel, {
                "path": rel,
                "referenced_by": [],
                "exists": True,
                "size": 0,
            })
            try:
                entry["size"] = path.stat().st_size
            except OSError:
                entry["size"] = 0

    for rel, entry in assets.items():
        try:
            abs_path = project_root / rel
            entry["exists"] = abs_path.is_file()
            if entry["exists"]:
                entry["size"] = abs_path.stat().st_size
        except OSError:
            entry["exists"] = False
        entry["referenced_by"].sort(key=lambda r: r["source_path"])

    orphans = sorted(
        rel for rel, entry in assets.items()
        if entry["exists"] and not entry["referenced_by"]
    )
    missing = sorted(
        rel for rel, entry in assets.items()
        if not entry["exists"] and entry["referenced_by"]
    )
    return {
        "version": 1,
        "assets": dict(sorted(assets.items())),
        "orphans": orphans,
        "missing": missing,
    }


def build_and_write(project_root: Path, html_dir: Path, documents: list[dict]) -> dict:
    data = build_assets(project_root, documents)
    (html_dir / "_index" / "assets.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return data
