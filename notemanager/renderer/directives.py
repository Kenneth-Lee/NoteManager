"""Custom docutils directives for NoteManager.

Registers Sphinx-compatible directives so that docutils can render them
without a full Sphinx build environment.
"""

from docutils import nodes
from docutils.parsers.rst import Directive, directives


class ToctreeDirective(Directive):
    """Simplified toctree - just generates a placeholder list."""
    has_content = True
    required_arguments = 0
    optional_arguments = 0
    option_spec = {
        "maxdepth": directives.nonnegative_int,
        "caption": directives.unchanged,
        "numbered": directives.flag,
        "glob": directives.flag,
    }

    def run(self):
        container = nodes.container(classes=["toctree"])
        if self.content:
            for line in self.content:
                line = line.strip()
                if line:
                    item = nodes.list_item()
                    ref = nodes.reference("", line, refuri=f"#doc-{line}")
                    ref["data-doc-path"] = line
                    para = nodes.paragraph("", "", ref)
                    item += para
                    container += item
        return [container]


class CnoteDirective(Directive):
    """Custom note directive with optional title.

    Ported from the Sphinx version, using docutils Directive base class.

    Usage::

        .. cnote:: Custom Title

           Content here.
    """
    has_content = True
    required_arguments = 0
    optional_arguments = 1
    option_spec = {"caption": directives.unchanged}

    def run(self):
        node = nodes.admonition(classes=["cnote"])
        if self.arguments:
            node += nodes.title(text=self.arguments[0])
        elif "caption" in self.options:
            node += nodes.title(text=self.options["caption"])
        else:
            node += nodes.title(text="说明")

        self.state.nested_parse(self.content, self.content_offset, node)
        return [node]


class CenteredDirective(Directive):
    """Centered text directive."""
    has_content = True
    required_arguments = 0
    optional_arguments = 0

    def run(self):
        container = nodes.container(classes=["centered"])
        self.state.nested_parse(self.content, self.content_offset, container)
        return [container]


def register_directives():
    """Register all custom directives with docutils."""
    directives.register_directive("toctree", ToctreeDirective)
    directives.register_directive("cnote", CnoteDirective)
    directives.register_directive("centered", CenteredDirective)
