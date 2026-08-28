from pathlib import Path

from notemanager.checker import check_file


def test_check_matches_rendered_mermaid_failures_for_overall_design() -> None:
    source = Path('test_projects/overall-design.md')
    diags = check_file(source)

    mermaid_diags = [d for d in diags if 'Mermaid' in d.kind]
    assert mermaid_diags, 'overall-design should contain Mermaid render failures'

    # Real rendered-page parser failure for this document is in the resource
    # allocation block; comment-heavy classDiagram blocks should not be flagged.
    lines = sorted({d.line for d in mermaid_diags})
    assert lines == [549]


def test_check_does_not_flag_comment_only_mermaid_block() -> None:
    content = (
        '# Demo\n\n'
        '```mermaid\n'
        'classDiagram\n'
        'class A {\n'
        '  +x: int // inline comment kept in source\n'
        '}\n'
        '```\n'
    )
    p = Path('/tmp/nmr_comment_only_mermaid.md')
    p.write_text(content, encoding='utf-8')
    try:
        diags = check_file(p)
        assert not any('可疑写法' in d.message for d in diags)
    finally:
        p.unlink(missing_ok=True)
