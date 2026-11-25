"""Minimal YAML-style front matter for NoteManager notes.

This is deliberately not a full YAML implementation.  It extracts only a
small, predictable subset: a ``tags`` list (inline or block form) and records
other top-level fields as diagnostics metadata.  The body is rendered exactly
as before; any unsupported YAML structure is left in the diagnostics text.
"""

from __future__ import annotations

import re


def _clean_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _split_inline_list(value: str) -> list[str]:
    value = value.strip()
    if not value:
        return []
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return [_clean_scalar(part) for part in value.split(",") if part.strip()]


def extract_frontmatter(content: str) -> tuple[str, list[str], list[str], list[str]]:
    """Return ``(body, tags, metadata_lines, warnings)``.

    Front matter is recognized only at the very beginning of a file, enclosed
    by ``---`` lines.  A malformed/unclosed block is left untouched and a
    warning is returned.
    """
    text = content.lstrip("\ufeff")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return content, [], [], []

    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            end = i
            break
    if end is None:
        return content, [], [], ["YAML 头未找到结束行，已按普通正文处理"]

    block = lines[1:end]
    body = "".join(lines[end + 1:])
    tags: list[str] = []
    metadata: list[str] = []
    warnings: list[str] = []

    i = 0
    while i < len(block):
        raw = block[i].rstrip("\r\n")
        stripped = raw.strip()
        i += 1
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            warnings.append(f"YAML 头中的孤立列表项: {stripped}")
            continue
        if ":" not in raw:
            warnings.append(f"YAML 头无法解析的行: {stripped}")
            continue
        key, value = raw.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key == "tags":
            # Inline form: tags: [a, b] or tags: a, b
            if value:
                tags.extend(_split_inline_list(value))
                continue
            # Block form:
            # tags:
            #   - a
            #   - b
            while i < len(block) and block[i].lstrip().startswith("-"):
                item = block[i].strip()[1:].strip()
                if item:
                    tags.append(_clean_scalar(item))
                i += 1
            continue
        if not key:
            warnings.append(f"YAML 头无法解析的行: {stripped}")
            continue
        metadata.append(f"{key}: {_clean_scalar(value)}" if value else key)

    # Deduplicate tags while preserving order.
    seen: set[str] = set()
    out_tags: list[str] = []
    for tag in tags:
        tag = tag.strip()
        if tag and tag not in seen:
            seen.add(tag)
            out_tags.append(tag)
    if metadata:
        warnings.append("YAML 元数据: " + ", ".join(metadata))
    return body, out_tags, metadata, warnings
