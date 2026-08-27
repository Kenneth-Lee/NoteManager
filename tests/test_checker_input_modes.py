from pathlib import Path

from notemanager.checker import check_file_with_options, check_text


def test_check_text_math_mode_reports_unclosed_block() -> None:
    diags = check_text('$$\\frac{1}{2}\n', input_format='math', source_name='<stdin>')
    assert diags
    assert any('LaTeX' in d.message for d in diags)


def test_check_text_mermaid_mode_reports_parse_error() -> None:
    diags = check_text('flowchart TD\nA[foo\n', input_format='mermaid', source_name='<stdin>')
    assert diags
    assert any('Mermaid' in d.kind or 'Mermaid' in d.message for d in diags)


def test_check_text_markdown_only_math_ignores_mermaid() -> None:
    content = '```mermaid\nflowchart TD\nA[foo\n```\n'
    diags = check_text(content, input_format='md', source_name='<stdin>', scope='math')
    assert not diags


def test_check_file_with_format_override_math(tmp_path: Path) -> None:
    p = tmp_path / 'snippet.txt'
    p.write_text('$$x\n', encoding='utf-8')
    diags = check_file_with_options(p, input_format='math', scope='all')
    assert diags
    assert any('LaTeX' in d.message for d in diags)
