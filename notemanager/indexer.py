"""Search index builder for NoteManager.

Extracts structured entries from rendered HTML:
- chapter: section headings with anchors
- dtag: document tags
- index: inline index terms (:index: role)
- label: cross-reference anchors (.. _label:)
- index-directive: .. index:: entries from RST source
"""

import json
import re
from pathlib import Path

# <section id="xxx">...<hN>title</hN>
_SECTION_HEADING_RE = re.compile(
    r'<section[^>]*\bid="([^"]+)"[^>]*>.*?<h(\d+)>([^<]+)</h\2>',
    re.DOTALL,
)

# <span class="dtag">tag1, tag2</span>
_DTAG_RE = re.compile(r'<span class="dtag">([^<]+)</span>')

# <span class="index-term">term</span>
_INDEX_TERM_RE = re.compile(r'<span class="index-term">([^<]+)</span>')

# .. index:: term
_RST_INDEX_DIRECTIVE_RE = re.compile(r'^\.\.\s+index::\s+(.+)$', re.MULTILINE)


def build_index(
    documents: list[dict],
    output_dir: Path,
    ref_labels: dict[str, str] | None = None,
    rst_index_directives: dict[str, list[str]] | None = None,
) -> None:
    """Build a categorized search index from rendered HTML documents.

    Args:
        documents: List of dicts with 'title', 'path', 'body' keys.
        output_dir: Directory to write search_index.json to.
        ref_labels: Mapping of label names to HTML file paths.
        rst_index_directives: Mapping of path -> list of index terms
            extracted from RST ``.. index::`` directives.
    """
    entries = []

    for doc in documents:
        path = doc["path"]
        body = doc["body"]
        _extract_chapters(entries, path, body)
        _extract_dtags(entries, path, body)
        _extract_index_terms(entries, path, body)

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

    data = {"documents": entries}
    output_path = output_dir / "search_index.json"
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _extract_chapters(entries: list, path: str, html: str) -> None:
    """Extract section headings from rendered HTML."""
    for match in _SECTION_HEADING_RE.finditer(html):
        section_id = match.group(1)
        level = int(match.group(2))
        title = _strip_tags(match.group(3))
        if not title:
            continue
        entries.append({
            "type": "chapter",
            "title": title,
            "path": f"{path}#{section_id}",
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
