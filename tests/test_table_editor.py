import pytest

from notemanager.renderer.rst_renderer import render_rst
from notemanager.table_editor import TableError, parse_table, serialize_table


def test_markdown_table_roundtrip_and_alignment():
    source = (
        "text\n"
        "| A | B |\n"
        "| :--- | ---: |\n"
        "| 1 | 2 |\n"
        "end\n"
    )
    table = parse_table(source, 2, ".md")
    assert table["start_line"] == 1
    assert table["end_line"] == 3
    assert table["header"] is True
    assert table["align"] == ["left", "right"]
    text = serialize_table(table)
    assert "| A | B |" in text
    assert "| :--- | ---: |" in text
    assert "| 1 | 2 |" in text


def test_rst_grid_table_roundtrip():
    source = (
        "text\n"
        "+---+---+\n"
        "| A | B |\n"
        "+===+===+\n"
        "| 1 | 2 |\n"
        "+---+---+\n"
        "end\n"
    )
    table = parse_table(source, 3, ".rst")
    assert table["format"] == "rst-grid"
    assert table["header"] is True
    assert table["rows"] == [["A", "B"], ["1", "2"]]
    text = serialize_table(table)
    assert "+=====+=====+" in text
    assert "| A " in text and "| 1 " in text


def test_rst_simple_table_is_normalized_to_grid():
    source = (
        "text\n"
        "=== ===\n"
        " A   B\n"
        "=== ===\n"
        " 1   2\n"
        "=== ===\n"
        "end\n"
    )
    table = parse_table(source, 3, ".rst")
    assert table["format"] == "rst-grid"
    assert table["rows"] == [["A", "B"], ["1", "2"]]
    text = serialize_table(table)
    assert text.startswith("+-----+-----+")
    assert "+=====+=====+" in text


def test_cursor_outside_table_fails():
    with pytest.raises(TableError):
        parse_table("just text\n", 0, ".md")


def test_rst_grid_without_header_is_readable():
    source = (
        "+---+---+\n"
        "| a | b |\n"
        "+---+---+\n"
        "| c | d |\n"
        "+---+---+\n"
    )
    table = parse_table(source, 1, ".rst")
    assert table["header"] is False
    assert table["rows"] == [["a", "b"], ["c", "d"]]


def test_markdown_loose_pipe_block_is_migrated():
    source = "| a | b |\n| c | d |\n"
    table = parse_table(source, 1, ".md")
    assert table["header"] is True
    assert table["rows"] == [["a", "b"], ["c", "d"]]
    assert "| --- | --- |" in serialize_table(table)


def test_grid_serialization_uses_cjk_display_width():
    from notemanager.renderer.rst_renderer import render_rst

    table = {
        "format": "rst-grid",
        "header": True,
        "align": ["default", "default"],
        "rows": [["列 1", "列 2"], ["asdfasdf", "asdfasdf"]],
    }
    text = serialize_table(table)
    result = render_rst(text)
    assert "Malformed table" not in "\n".join(result["warnings"])
    assert "<table>" in result["body"]


def test_cjk_grid_table_roundtrip_is_docutils_valid():
    source = (
        "+--------------------+----------+------------+\n"
        "| asdad              | sdf      | 现在可以了 |\n"
        "+====================+==========+============+\n"
        "| asdf               | asdf     |            |\n"
        "+--------------------+----------+------------+\n"
        "| 重要的是中文行不行 | 我不知道 |            |\n"
        "+--------------------+----------+------------+\n"
    )
    table = parse_table(source, 1, ".rst")
    text = serialize_table(table)
    result = render_rst(text)
    assert "Malformed table" not in "\n".join(result["warnings"])
    assert "重要的是中文行不行" in text


def test_misaligned_grid_table_can_be_recovered_for_editing():
    source = (
        "+----------+----------+\n"
        "| 列 1      | 列 2      |\n"
        "+==========+==========+\n"
        "| asdfasdf | asdfasdf |\n"
        "+----------+----------+\n"
    )
    table = parse_table(source, 1, ".rst")
    assert table["rows"] == [["列 1", "列 2"], ["asdfasdf", "asdfasdf"]]
    text = serialize_table(table)
    result = render_rst(text)
    assert "Malformed table" not in "\n".join(result["warnings"])
