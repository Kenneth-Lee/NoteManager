"""Plain text to simple paragraph HTML renderer."""

import html
import re


def render_txt(content: str, source_path: str = "<string>") -> dict:
    """Render plain text into simple paragraph HTML.

    Blank lines split paragraphs; single newlines inside a paragraph become ``<br>``.
    """
    _ = source_path
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]

    parts: list[str] = []
    for b in blocks:
        esc = html.escape(b.strip())
        esc = esc.replace("\n", "<br>\n")
        parts.append(f"<p>{esc}</p>")

    body = "\n".join(parts) if parts else "<p></p>"
    return {"body": body, "toc": "", "warnings": [], "dtags": []}

