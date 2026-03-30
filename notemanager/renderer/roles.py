"""Custom docutils roles for NoteManager.

Registers Sphinx-compatible roles so that docutils can render them
without a full Sphinx build environment.
"""

from docutils import nodes
from docutils.parsers.rst import roles


def _ref_role(role, rawtext, text, lineno, inliner, options=None, content=None):
    """Handle :ref:`label` and :ref:`Title <label>` syntax."""
    options = options or {}
    content = content or []

    # Parse "Title <label>" or just "label"
    if "<" in text and text.endswith(">"):
        title = text[:text.index("<")].strip()
        label = text[text.index("<") + 1:text.index(">")].strip()
    else:
        title = text
        label = text

    node = nodes.reference(rawtext, title, refuri=f"#ref-{label}")
    node["data-ref-label"] = label
    return [node], []


def _doc_role(role, rawtext, text, lineno, inliner, options=None, content=None):
    """Handle :doc:`path/to/document` syntax."""
    options = options or {}
    content = content or []

    node = nodes.reference(rawtext, text, refuri=f"#doc-{text.strip()}")
    node["data-doc-path"] = text.strip()
    return [node], []


def _dtag_role(role, rawtext, text, lineno, inliner, options=None, content=None):
    """Handle :dtag:`tag1, tag2` syntax."""
    options = options or {}
    content = content or []

    node = nodes.inline(rawtext, text, classes=["dtag"], **{"data-tags": text.strip()})
    return [node], []


def _dtaglist_role(role, rawtext, text, lineno, inliner, options=None, content=None):
    """Handle :dtaglist:`tag` or :dtaglist:`@all` syntax."""
    options = options or {}
    content = content or []

    node = nodes.container(rawtext, classes=["dtaglist"], **{"data-tags": text.strip()})
    node += nodes.Text(f"[标签列表: {text}]")
    return [node], []


def _index_role(role, rawtext, text, lineno, inliner, options=None, content=None):
    """Handle :index:`term` syntax - just render as inline text."""
    options = options or {}
    content = content or []

    node = nodes.inline(rawtext, text, classes=["index-term"])
    return [node], []


def _math_role(role, rawtext, text, lineno, inliner, options=None, content=None):
    """Handle :math:`expression` - render as code-like span."""
    options = options or {}
    content = content or []

    node = nodes.math(rawtext, text)
    return [node], []


def register_roles():
    """Register all custom roles with docutils."""
    roles.register_local_role("ref", _ref_role)
    roles.register_local_role("doc", _doc_role)
    roles.register_local_role("dtag", _dtag_role)
    roles.register_local_role("dtaglist", _dtaglist_role)
    roles.register_local_role("index", _index_role)
