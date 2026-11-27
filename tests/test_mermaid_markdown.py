from notemanager.renderer.md_renderer import render_md


def test_markdown_gantt_fence_becomes_mermaid_pre():
    source = """\
# Gantt

```mermaid
gantt
    title 项目计划
    dateFormat YYYY-MM-DD
    section 设计
    需求 :a1, 2026-01-01, 7d
    实现 :after a1, 14d
```
"""
    body = render_md(source, "test.md")["body"]
    assert '<pre class="mermaid">' in body
    assert "gantt\n" in body
    assert "<div class=\"highlight\">" not in body


def test_markdown_other_mermaid_types_without_space_are_recognized():
    for kind in ("stateDiagram-v2", "pie", "journey", "mindmap"):
        source = f"# T\n\n```mermaid\n{kind}\n```\n"
        body = render_md(source, "test.md")["body"]
        assert '<pre class="mermaid">' in body, kind
