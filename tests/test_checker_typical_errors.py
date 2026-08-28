import pytest

from notemanager.checker import check_text


@pytest.mark.parametrize(
    ("name", "content", "expected_hint"),
    [
        (
            "unclosed_display_math",
            "$$\\frac{1}{2}\n",
            "缺少结束的 $$",
        ),
        (
            "env_mismatch",
            "\\begin{align}\na+b=c\n\\end{equation}\n",
            "LaTeX 环境不匹配",
        ),
        (
            "missing_begin",
            "\\end{aligned}\n",
            "缺少匹配的 \\begin",
        ),
        (
            "missing_inline_close",
            "Text with \\(a+b\n",
            "行内公式 未闭合",
        ),
        (
            "missing_block_open",
            "\\] orphan close\\n",
            "块级公式 缺少对应的开启符号",
        ),
    ],
)
def test_typical_math_errors_reported(name: str, content: str, expected_hint: str) -> None:
    diags = check_text(content, input_format="math", source_name=f"<math:{name}>")
    assert diags, f"{name} should report diagnostics"
    assert any(expected_hint in d.message for d in diags), f"{name} diagnostics mismatch: {diags}"


@pytest.mark.parametrize(
    "content",
    [
        "$$\\frac{1}{2}$$\n",
        "\\(a+b\\) and \\[x^2+y^2=z^2\\]\n",
        "\\begin{aligned}\na+b=c\n\\end{aligned}\n",
    ],
)
def test_typical_math_valid_samples_pass(content: str) -> None:
    diags = check_text(content, input_format="math", source_name="<math:valid>")
    assert not diags


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("unclosed_bracket", "flowchart TD\nA[foo\n"),
        ("dangling_edge", "flowchart TD\nA -->\n"),
        ("broken_link", "flowchart TD\nsubgraph S\nA -->\n"),
        ("unclosed_subgraph", "flowchart TD\nsubgraph S\nA --> B\n"),
    ],
)
def test_typical_mermaid_errors_reported(name: str, content: str) -> None:
    diags = check_text(content, input_format="mermaid", source_name=f"<mermaid:{name}>")
    assert diags, f"{name} should report diagnostics"
    assert any("Parse error" in d.message or "语法错误" in d.message for d in diags), diags


@pytest.mark.parametrize(
    "content",
    [
        "flowchart TD\nA[Start] --> B[Next]\n",
        "sequenceDiagram\nparticipant A\nparticipant B\nA->>B: ping\n",
        "classDiagram\nclass A\nclass B\nA --> B\n",
    ],
)
def test_typical_mermaid_valid_samples_no_parse_error(content: str) -> None:
    diags = check_text(content, input_format="mermaid", source_name="<mermaid:valid>")
    assert not any("Parse error" in d.message for d in diags)


def test_mermaid_runtime_error_is_not_reported_as_syntax_error() -> None:
    source = (
        'graph LR\n'
        'USR["用户代码两种形态"] -->|"gRPC 同步形态 原样"| BA["阻塞适配模型"]\n'
        'USR -->|"加 co_await"| NA["原生模型"]\n'
    )
    diags = check_text(source, input_format="mermaid", source_name="<mermaid:runtime>")
    assert not diags
