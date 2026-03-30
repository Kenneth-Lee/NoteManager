"""Cross-reference resolver for NoteManager.

Extracts label definitions from RST files and resolves :ref: and :doc:
links in rendered HTML.
"""

import re
from pathlib import Path

from notemanager.scanner import FileInfo

# Pattern for RST label definitions: .. _label:
LABEL_PATTERN = re.compile(r"^\.\.\s+_`?([^`:\s]+)`?:\s*$", re.MULTILINE)


def extract_labels(project_root: str | Path, files: list[FileInfo]) -> dict[str, str]:
    """Extract all label definitions from RST files.

    Args:
        project_root: Absolute path to the project root.
        files: List of FileInfo objects from the scanner.

    Returns:
        Dictionary mapping label names to their HTML file paths
        (relative to .NoteManager/html/).
    """
    labels = {}
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
            labels[label] = html_rel

    return labels


def resolve_references(
    html: str,
    current_html_path: str,
    ref_labels: dict[str, str],
) -> str:
    """Resolve :ref: and :doc: cross-references in rendered HTML.

    Args:
        html: The rendered HTML body.
        current_html_path: Relative path of the current HTML file.
        ref_labels: Label-to-path mapping from extract_labels().

    Returns:
        HTML with resolved cross-references.
    """
    html = _resolve_ref_links(html, current_html_path, ref_labels)
    html = _resolve_doc_links(html, current_html_path)
    return html


def _resolve_ref_links(
    html: str,
    current_html_path: str,
    ref_labels: dict[str, str],
) -> str:
    """Resolve #ref-label placeholders to actual href links."""
    def replace_ref(match):
        full_tag = match.group(0)
        label = match.group(1)

        target = ref_labels.get(label)
        if not target:
            # Label not found, remove the placeholder link, keep text
            return match.group(2)  # just the link text

        rel_path = _relative_path(current_html_path, target)
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
