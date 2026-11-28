from notemanager.renderer.rst_renderer import render_rst


def test_rst_mermaid_code_block_renders_without_pygments_warning():
    source = (
        "Mermaid\n"
        "=======\n\n"
        ".. code-block:: mermaid\n\n"
        "   flowchart TD\n"
        "       A[开始] --> B{判断}\n"
        "       B -->|是| C[结束]\n"
    )
    result = render_rst(source, "test/rst_test.rst")
    assert not any("Pygments" in warning for warning in result["warnings"])
    assert '<pre class="mermaid">' in result["body"]
    assert "flowchart TD" in result["body"]


def test_rst_literal_block_is_not_parsed_as_mermaid_directive():
    source = """\
Lit
===

Example::

    .. code-block:: mermaid

       flowchart TD
           A --> B
"""
    result = render_rst(source, "literal.rst")
    assert not result["warnings"]
    assert '<pre class="mermaid">' not in result["body"]
    assert ".. code-block:: mermaid" in result["body"]
    assert '<pre class="literal-block">' in result["body"]


def test_rst_mermaid_block_followed_by_paragraph_keeps_blank_separator():
    source = """\
Title
=====

Before.

.. code-block:: mermaid

   flowchart TD
       A --> B

After paragraph.
"""
    result = render_rst(source, "separator.rst")
    assert not result["warnings"]
    assert '<pre class="mermaid">' in result["body"]
    assert "After paragraph." in result["body"]
