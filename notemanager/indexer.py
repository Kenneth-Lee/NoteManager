"""Search index builder for NoteManager."""

import json
import re
from pathlib import Path


def build_index(documents: list[dict], output_dir: Path) -> None:
    """Build a JSON search index from rendered HTML documents.

    Args:
        documents: List of dicts with 'title', 'path', 'body' keys.
        output_dir: Directory to write search_index.json to.
    """
    indexed = []
    for i, doc in enumerate(documents):
        text = _strip_html(doc["body"])
        snippet = text[:200] + "..." if len(text) > 200 else text
        indexed.append({
            "id": i,
            "title": doc["title"],
            "path": doc["path"],
            "text": text,
            "snippet": snippet,
        })

    data = {"documents": indexed}
    output_path = output_dir / "search_index.json"
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _strip_html(html: str) -> str:
    """Remove HTML tags and normalize whitespace."""
    # Remove script and style content
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode common HTML entities
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                          ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
        text = text.replace(entity, char)
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text
