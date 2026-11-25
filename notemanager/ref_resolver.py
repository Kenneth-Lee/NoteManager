"""Cross-reference resolver for NoteManager.

Extracts label definitions from RST files and resolves :ref: and :doc:
links in rendered HTML.
"""

import json
from html import escape as html_escape
import re
from pathlib import Path

from notemanager.scanner import FileInfo

# Pattern for RST label definitions: .. _label:
LABEL_PATTERN = re.compile(r"^\.\.\s+_`?([^`:\s]+)`?:\s*$", re.MULTILINE)


def extract_labels(
    project_root: str | Path, files: list[FileInfo], *, with_candidates: bool = False
):
    """Extract all label definitions from RST files.

    Args:
        project_root: Absolute path to the project root.
        files: List of FileInfo objects from the scanner.

    Returns:
        Dictionary mapping label names to their HTML file paths
        (relative to .NoteManager/html/), or
        ``(labels, candidates)`` when ``with_candidates`` is true.
    """
    labels = {}
    candidates: dict[str, list[str]] = {}
    for fi in files:
        if fi.ext != ".rst":
            continue
        try:
            content = fi.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        html_rel = str(fi.rel_path.with_suffix(".html"))
        for match in LABEL_PATTERN.finditer(content):
            label = match.group(1).strip()
            if label in labels:
                import warnings
                warnings.warn(
                    f"Duplicate label '{label}' in {fi.rel_path} "
                    f"(previously defined for {labels[label]})",
                    stacklevel=2,
                )
            target = f"{html_rel}#{label}"
            labels[label] = target
            candidates.setdefault(label, []).append(target)

    if with_candidates:
        return labels, candidates
    return labels


def resolve_references(
    html: str,
    current_html_path: str,
    ref_labels: dict[str, str],
    ref_candidates: dict[str, list[str]] | None = None,
) -> str:
    """Resolve :ref: and :doc: cross-references in rendered HTML.

    Args:
        html: The rendered HTML body.
        current_html_path: Relative path of the current HTML file.
        ref_labels: Label-to-path mapping from extract_labels().

    Returns:
        HTML with resolved cross-references.
    """
    html = _resolve_ref_links(html, current_html_path, ref_labels, ref_candidates)
    html = _resolve_doc_links(html, current_html_path)
    return html


def _resolve_ref_links(
    html: str,
    current_html_path: str,
    ref_labels: dict[str, str],
    ref_candidates: dict[str, list[str]] | None = None,
) -> str:
    """Resolve #ref-label placeholders to actual href links."""
    def replace_ref(match):
        full_tag = match.group(0)
        label = match.group(1)

        choices = list((ref_candidates or {}).get(label) or [])
        target = (choices[-1] if choices else ref_labels.get(label))
        if not target:
            # Label not found, remove the placeholder link, keep text
            return match.group(2)  # just the link text

        def rel_target(value: str) -> str:
            target_path, _, anchor = value.partition("#")
            rel = _relative_path(current_html_path, target_path)
            return rel + (("#" + anchor) if anchor else "")

        rel_path = rel_target(target)
        if len(choices) > 1:
            payload = []
            for choice in choices:
                path, _, anchor = choice.partition("#")
                payload.append({
                    "path": rel_target(choice),
                    "label": Path(path).with_suffix("").name,
                })
            attr = html_escape(json.dumps(payload, ensure_ascii=False), quote=True)
            return full_tag.replace(
                f'href="#ref-{label}"',
                f'href="{rel_path}" data-nm-ref-candidates="{attr}"',
            )
        return full_tag.replace(f'href="#ref-{label}"', f'href="{rel_path}"')

    # Match <a ... href="#ref-LABEL">TEXT</a>
    return re.sub(
        r'<a[^>]*href="#ref-([^"]*)"[^>]*>([^<]*)</a>',
        replace_ref,
        html,
    )


def _resolve_doc_links(html: str, current_html_path: str) -> str:
    """Resolve #doc-path placeholders to actual href links."""
    def replace_doc(match):
        full_tag = match.group(0)
        doc_path = match.group(1)

        # Convert doc path to html path (add .html if needed)
        if not doc_path.endswith(".html"):
            html_target = doc_path + ".html"
        else:
            html_target = doc_path

        # If target has no directory component, resolve relative to current
        # file's directory (e.g. toctree entry "工具链" in "ocaml/index.html"
        # should become "ocaml/工具链.html")
        if not Path(html_target).parent.parts:
            current_dir = str(Path(current_html_path).parent)
            if current_dir and current_dir != ".":
                html_target = current_dir + "/" + html_target

        rel_path = _relative_path(current_html_path, html_target)
        return full_tag.replace(f'href="#doc-{doc_path}"', f'href="{rel_path}"')

    return re.sub(
        r'<a[^>]*href="#doc-([^"]*)"[^>]*>[^<]*</a>',
        replace_doc,
        html,
    )


def _relative_path(from_path: str, to_path: str) -> str:
    """Compute relative path from one HTML file to another.

    Args:
        from_path: e.g. "学习笔记/vim/index.html"
        to_path: e.g. "学习笔记/futex.html"

    Returns:
        Relative path, e.g. "../futex.html"
    """
    from_dir = Path(from_path).parent
    to = Path(to_path)

    try:
        rel = to.relative_to(from_dir)
        return str(rel)
    except ValueError:
        pass

    # Walk up from from_dir and down to to_path
    from_parts = list(from_dir.parts)
    to_parts = list(to.parent.parts) if str(to.parent) != "." else []

    # Find common prefix length
    common = 0
    for a, b in zip(from_parts, to_parts):
        if a == b:
            common += 1
        else:
            break

    ups = len(from_parts) - common
    downs = to_parts[common:]

    result = "/".join([".."] * ups + downs + [to.name])
    return result
