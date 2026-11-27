"""Parse and serialize simple Markdown/reST tables for the source editor.

The editor model is deliberately small: a header flag, a list of rows and
column alignments.  Cells are stored as raw inline source text; this module
does not interpret Markdown/reST markup.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

_ALIGN_RE = re.compile(r"^:?-{3,}:?$")
_GRID_BORDER_RE = re.compile(r"^\+(?:[-=]+\+)+$")
_MD_SPLIT_RE = re.compile(r"(?<!\\)\|")
_SIMPLE_BORDER_RE = re.compile(r"^(?:=+ +)+=+$")


class TableError(ValueError):
    """The cursor is not in a supported table, or the table is malformed."""


def _lines(content: str) -> list[str]:
    return str(content or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()


def _md_has_pipe(line: str) -> bool:
    return "|" in line


def _md_cells(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    return [part.strip().replace("\\|", "|") for part in _MD_SPLIT_RE.split(text)]


def _md_delimiter(line: str) -> list[str] | None:
    cells = _md_cells(line)
    if len(cells) < 2 or not all(_ALIGN_RE.match(c) for c in cells):
        return None
    return cells


def _md_alignment(spec: str) -> str:
    left = spec.startswith(":")
    right = spec.endswith(":")
    if left and right:
        return "center"
    if right:
        return "right"
    if left:
        return "left"
    return "default"


def _md_block(lines: list[str], cursor: int) -> tuple[int, int] | None:
    n = len(lines)
    for start in range(0, max(0, n - 1)):
        if not _md_has_pipe(lines[start]):
            continue
        delim = _md_delimiter(lines[start + 1]) if start + 1 < n else None
        if not delim:
            continue
        end = start + 2
        while end < n and _md_has_pipe(lines[end]) and not _md_delimiter(lines[end]):
            end += 1
        # Trailing lines without pipes mark the end of the table.
        final = end - 1
        if start <= cursor <= final:
            return start, final
    return None


def _md_loose_block(lines: list[str], cursor: int) -> tuple[int, int] | None:
    """Find a pipe block even when the delimiter row is missing.

    Standard Markdown requires a delimiter row; the editor accepts a loose
    block as a migration aid and writes a proper table when applied.
    """
    n = len(lines)
    if not (0 <= cursor < n):
        return None
    start = cursor
    while start > 0 and _md_has_pipe(lines[start - 1]):
        start -= 1
    end = cursor
    while end + 1 < n and _md_has_pipe(lines[end + 1]):
        end += 1
    if start == end:
        return None
    for line in lines[start:end + 1]:
        if _md_delimiter(line):
            return None
        if line.count("|") < 2 and not (line.strip().startswith("|") or line.strip().endswith("|")):
            return None
    return start, end


def _parse_markdown(lines: list[str], cursor: int) -> dict:
    block = _md_block(lines, cursor)
    loose = False
    if not block:
        block = _md_loose_block(lines, cursor)
        loose = block is not None
    if not block:
        raise TableError("光标不在 Markdown 管道表格内")
    start, end = block
    if loose:
        header = _md_cells(lines[start])
        align = ["default"] * len(header)
        rows = [header]
        for line in lines[start + 1:end + 1]:
            cells = _md_cells(line)
            if len(cells) != len(header):
                raise TableError("Markdown 表格各行列数不一致")
            rows.append(cells)
    else:
        header = _md_cells(lines[start])
        delim = _md_delimiter(lines[start + 1])
        if not delim:
            raise TableError("Markdown 表格缺少分隔行")
        if len(header) != len(delim):
            raise TableError("Markdown 表格表头和分隔行列数不一致")
        align = [_md_alignment(c) for c in delim]
        rows = [header]
        for line in lines[start + 2:end + 1]:
            cells = _md_cells(line)
            if len(cells) != len(header):
                raise TableError("Markdown 表格各行列数不一致")
            rows.append(cells)
    if not rows:
        raise TableError("Markdown 表格没有内容")
    return {
        "start_line": start,
        "end_line": end,
        "format": "markdown",
        "header": True,
        "align": align,
        "rows": rows,
    }


def _grid_is_border(line: str) -> bool:
    return bool(_GRID_BORDER_RE.match(line.strip()))


def _grid_border_with_equals(line: str) -> bool:
    return _grid_is_border(line) and "=" in line


def _grid_block(lines: list[str], cursor: int) -> tuple[int, int] | None:
    n = len(lines)
    for start in range(n):
        if not _grid_is_border(lines[start]):
            continue
        end = start
        # A table is a sequence of border/row lines.  A border followed by a
        # non-row line is the final border.
        while end + 1 < n:
            nxt = lines[end + 1]
            if _grid_is_border(nxt) or nxt.strip().startswith("|"):
                end += 1
                continue
            break
        if start <= cursor <= end:
            return start, end
    return None


def _grid_parse(lines: list[str], cursor: int) -> dict:
    block = _grid_block(lines, cursor)
    if not block:
        raise TableError("光标不在 reST grid table 内")
    start, end = block
    first = lines[start].strip()
    boundaries = [i for i, ch in enumerate(first) if ch == "+"]
    if len(boundaries) < 2:
        raise TableError("reST 表格边框不完整")
    width = len(boundaries) - 1
    align = ["default"] * width
    before_sep: list[list[str]] = []
    after_sep: list[list[str]] = []
    seen_header_sep = False

    for raw in lines[start + 1:end + 1]:
        line = raw.strip()
        if _grid_is_border(line):
            if _grid_border_with_equals(line):
                seen_header_sep = True
            continue
        if not line.startswith("|") or not line.endswith("|"):
            raise TableError("reST grid table 行格式不正确")
        cells = _grid_cells_by_display(line, boundaries)
        if cells is None:
            # A user may have edited a grid table by hand and moved the
            # vertical bars.  Still recover the cells so the table editor
            # can rewrite the table into a valid, aligned shape.
            stripped = line.strip()
            if not stripped.startswith("|") or not stripped.endswith("|"):
                raise TableError("reST grid table 行格式不正确")
            cells = [cell.strip() for cell in stripped[1:-1].split("|")]
            if len(cells) != width:
                raise TableError("暂不支持合并单元格或跨列单元格")
        if seen_header_sep:
            after_sep.append(cells)
        else:
            before_sep.append(cells)

    if seen_header_sep:
        header_lines = before_sep
        body_lines = after_sep
    else:
        header_lines = []
        body_lines = before_sep
    if len(header_lines) > 1:
        raise TableError("暂不支持多行表头")
    rows = header_lines + body_lines
    if not rows:
        raise TableError("reST 表格没有内容")
    return {
        "start_line": start,
        "end_line": end,
        "format": "rst-grid",
        "header": bool(header_lines),
        "align": align,
        "rows": rows,
    }


def _simple_spans(line: str) -> list[tuple[int, int]] | None:
    text = line.rstrip()
    if not _SIMPLE_BORDER_RE.match(text.strip()):
        return None
    spans = []
    for m in re.finditer(r"=+", text):
        spans.append((m.start(), m.end()))
    return spans if len(spans) >= 2 else None


def _simple_block(lines: list[str], cursor: int) -> tuple[int, int] | None:
    # Simple tables are separated from surrounding prose by blank lines in
    # normal reST.  Use the first and last border in that paragraph.
    n = len(lines)
    if not (0 <= cursor < n):
        return None
    top = cursor
    while top > 0 and lines[top - 1].strip():
        top -= 1
    bottom = cursor
    while bottom + 1 < n and lines[bottom + 1].strip():
        bottom += 1
    borders = [i for i in range(top, bottom + 1) if _simple_spans(lines[i])]
    if len(borders) < 2 or not (borders[0] <= cursor <= borders[-1]):
        return None
    return borders[0], borders[-1]


def _simple_parse(lines: list[str], cursor: int) -> dict:
    block = _simple_block(lines, cursor)
    if not block:
        raise TableError("光标不在 reST simple table 内")
    start, end = block
    spans = _simple_spans(lines[start])
    if not spans:
        raise TableError("无法确定 simple table 列宽")
    borders = [i for i in range(start, end + 1) if _simple_spans(lines[i])]
    if not borders:
        raise TableError("simple table 边框缺失")
    # With three borders the middle one separates header and body.  With two
    # borders the table has no header.
    header = len(borders) >= 3
    body_start = borders[1] + 1 if header else start + 1
    body_end = borders[-1] - 1
    row_lines = [lines[i] for i in range(body_start, body_end + 1)
                 if _simple_spans(lines[i]) is None]
    if header:
        header_lines = [lines[i] for i in range(start + 1, borders[1])
                        if _simple_spans(lines[i]) is None]
        if len(header_lines) != 1:
            raise TableError("暂不支持多行 simple table 表头")
        header_cells = _simple_split(header_lines[0], spans)
    else:
        header_cells = None
    rows: list[list[str]] = []
    for line in row_lines:
        if not line.strip():
            continue
        rows.append(_simple_split(line, spans))
    if header_cells:
        rows.insert(0, header_cells)
    if not rows:
        raise TableError("simple table 没有内容")
    return {
        "start_line": start,
        "end_line": end,
        "format": "rst-grid",
        "header": bool(header_cells),
        "align": ["default"] * len(spans),
        "rows": rows,
    }


def _simple_split(line: str, spans: list[tuple[int, int]]) -> list[str]:
    target = spans[-1][1]
    missing = target - _display_width(line)
    if missing > 0:
        line += " " * missing
    indexes = [_char_index_at_display(line, start) for start, _ in spans]
    indexes.append(_char_index_at_display(line, spans[-1][1]))
    if any(index is None for index in indexes):
        raise TableError("simple table 中的中文/全角单元格无法对齐")
    return [line[indexes[i]:indexes[i + 1]].strip() for i in range(len(spans))]


def parse_table(content: str, line: int, ext: str) -> dict:
    """Parse the table containing ``line`` from editor content."""
    lines = _lines(content)
    if not lines:
        raise TableError("源文件为空")
    try:
        cursor = int(line)
    except (TypeError, ValueError):
        raise TableError("光标行号无效")
    if cursor < 0 or cursor >= len(lines):
        raise TableError("光标行号超出文件范围")
    suffix = (ext or "").lower()
    if suffix in (".md", ".markdown", ".mdown"):
        return _parse_markdown(lines, cursor)
    if suffix in (".rst", ".txt"):
        try:
            return _grid_parse(lines, cursor)
        except TableError:
            return _simple_parse(lines, cursor)
    raise TableError("仅支持 .md/.rst 表格")


def _display_width(text: str) -> int:
    """Terminal/monospace display width, counting CJK as double width."""
    width = 0
    for ch in str(text):
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def _pad_display(text: str, width: int) -> str:
    text = str(text)
    return text + " " * max(0, width - _display_width(text))


def _char_index_at_display(line: str, target: int) -> int | None:
    width = 0
    for index, ch in enumerate(line):
        if width == target:
            return index
        width += _display_width(ch)
    return len(line) if width == target else None


def _grid_cells_by_display(line: str, boundaries: list[int]) -> list[str] | None:
    indexes = [_char_index_at_display(line, pos) for pos in boundaries]
    if any(index is None for index in indexes):
        return None
    chars = list(line)
    if any(index >= len(chars) or chars[index] != "|" for index in indexes):
        return None
    return [
        line[indexes[i] + 1:indexes[i + 1]].strip()
        for i in range(len(indexes) - 1)
    ]


def _widths(rows: Iterable[Iterable[str]], columns: int) -> list[int]:
    widths = [3] * columns
    for row in rows:
        for i, value in enumerate(row):
            if i < columns:
                widths[i] = max(widths[i], _display_width(str(value)))
    return widths


def _normalize_rows(rows, columns: int) -> list[list[str]]:
    out = []
    for row in rows:
        values = [str(v).replace("\n", " ") for v in (row or [])]
        values = (values + [""] * columns)[:columns]
        out.append(values)
    return out


def serialize_table(data: dict) -> str:
    """Serialize a model into standard Markdown or reST grid source."""
    fmt = str(data.get("format") or "").lower()
    rows = _normalize_rows(data.get("rows") or [], max(1, len((data.get("rows") or [[]])[0])))
    if not rows:
        raise TableError("表格没有内容")
    columns = len(rows[0])
    align = list(data.get("align") or [])
    align = (align + ["default"] * columns)[:columns]
    header = bool(data.get("header", True))

    if fmt == "markdown":
        if not header:
            raise TableError("Markdown 表格必须有表头")
        widths = _widths(rows, columns)
        def md_row(row, head=False):
            vals = []
            for i, value in enumerate(row):
                value = str(value).replace("|", "\\|")
                widths[i] = max(widths[i], len(value))
                vals.append(value)
            return "| " + " | ".join(vals) + " |"
        # Recompute widths after escaping, then format.
        escaped = [[str(v).replace("|", "\\|") for v in row] for row in rows]
        widths = _widths(escaped, columns)
        lines = ["| " + " | ".join(escaped[0]) + " |"]
        specs = []
        for a in align:
            if a == "left": specs.append(":" + "-" * 3)
            elif a == "right": specs.append("-" * 3 + ":")
            elif a == "center": specs.append(":" + "-" * 3 + ":")
            else: specs.append("-" * 3)
        lines.append("| " + " | ".join(specs) + " |")
        for row in escaped[1:]:
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines) + "\n"

    if fmt != "rst-grid":
        raise TableError("不支持的表格格式")
    widths = _widths(rows, columns)
    def border(ch="-"): return "+" + "+".join(ch * (w + 2) for w in widths) + "+"
    def row_line(row): return "|" + "|".join(" " + _pad_display(v, widths[i]) + " " for i, v in enumerate(row)) + "|"
    out = [border()]
    if header:
        out.append(row_line(rows[0]))
        out.append(border("="))
        body = rows[1:]
    else:
        body = rows
    for row in body:
        out.append(row_line(row))
        out.append(border())
    return "\n".join(out) + "\n"
