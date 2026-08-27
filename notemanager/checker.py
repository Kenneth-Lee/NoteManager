"""Lightweight syntax checking for NoteManager source files."""

from __future__ import annotations

import io
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from docutils.core import publish_doctree


@dataclass(frozen=True)
class Diagnostic:
    """Single source-level syntax problem discovered while checking a file."""

    path: str
    line: int
    kind: str
    message: str

    def format(self) -> str:
        return f"{self.path}:{self.line}: {self.kind}: {self.message}"


def _extract_line_number_from_warning(line: str) -> int:
    m = re.search(r"(?P<line>\d+)", line)
    if not m:
        return 1
    return int(m.group("line"))


def _parse_rst_warnings(source: str, path: str, warning_text: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for raw in warning_text.splitlines():
        text = raw.strip()
        if not text:
            continue
        line = _extract_line_number_from_warning(text)
        if text.startswith("<string>"):
            text = text.split(":", 2)[-1].strip()
        if not text:
            continue
        kind = "RST"
        if "WARNING" in text.upper():
            kind = "RST Warning"
        elif "ERROR" in text.upper() or "SEVERE" in text.upper():
            kind = "RST Error"
        diagnostics.append(Diagnostic(path=path, line=line, kind=kind, message=text))
    return diagnostics


def _check_markdown_math(content: str, path: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    lines = content.splitlines()

    odd_dollar = 0
    dol_line = 1
    for i, line in enumerate(lines, start=1):
        count = len(re.findall(r"(?<!\\)\$\$", line))
        if count % 2:
            odd_dollar += count
            dol_line = i
    if odd_dollar % 2:
        diagnostics.append(
            Diagnostic(
                path=path,
                line=dol_line,
                kind="Markdown Math",
                message="LaTeX 显示公式未闭合：缺少结束的 $$，请检查行内/块级公式边界。",
            )
        )

    env_stack: list[tuple[str, int]] = []
    env_re = re.compile(r"(?<!\\)\\begin\s*\{(\w+\*?)\}")
    end_re = re.compile(r"(?<!\\)\\end\s*\{(\w+\*?)\}")
    for i, line in enumerate(lines, start=1):
        for env_match in env_re.finditer(line):
            env_stack.append((env_match.group(1), i))
        for end_match in end_re.finditer(line):
            env_name = end_match.group(1)
            if not env_stack:
                diagnostics.append(
                    Diagnostic(
                        path=path,
                        line=i,
                        kind="Markdown Math",
                        message=(
                            f"LaTeX 环境缺少匹配的 \\begin{{{env_name}}}："
                            f"发现 \\end{{{env_name}}} 但未开启。"
                        ),
                    )
                )
                continue
            open_name, open_line = env_stack.pop()
            if open_name != env_name:
                diagnostics.append(
                    Diagnostic(
                        path=path,
                        line=i,
                        kind="Markdown Math",
                        message=(
                            f"LaTeX 环境不匹配：在 L{open_line} 启动的是 \\begin{{{open_name}}}，"
                            f"但这里关闭的是 \\end{{{env_name}}}。"
                        ),
                    )
                )
    for env_name, env_line in env_stack:
        diagnostics.append(
            Diagnostic(
                path=path,
                line=env_line,
                kind="Markdown Math",
                message=f"LaTeX 环境未闭合：\\begin{{{env_name}}} 缺少对应的 \\end{{{env_name}}}。",
            )
        )

    # Inline/Display math in \( ... \), \[ ... \]
    for marker, close_marker, label in ((r"\\(", r"\\)", "行内公式"), (r"\\[", r"\\]", "块级公式")):
        opens = []
        for i, line in enumerate(lines, start=1):
            for match in re.finditer(rf"(?<!\\){re.escape(marker)}", line):
                opens.append((i, match.start()))
            for match in re.finditer(rf"(?<!\\){re.escape(close_marker)}", line):
                if not opens:
                    diagnostics.append(
                        Diagnostic(
                            path=path,
                            line=i,
                            kind="Markdown Math",
                            message=f"LaTeX {label} 缺少对应的开启符号 {marker}。",
                        )
                    )
                    continue
                opens.pop()
        while opens:
            line, _ = opens.pop()
            diagnostics.append(
                Diagnostic(
                    path=path,
                    line=line,
                    kind="Markdown Math",
                    message=f"LaTeX {label} 未闭合：缺少结束符号 {close_marker}。",
                )
            )

    return diagnostics


def _check_markdown_mermaid(content: str, path: str) -> list[Diagnostic]:
    """Use the exact Mermaid runtime bundled with NoteManager to validate blocks.

    This intentionally matches the frontend behavior in ``template.py`` and keeps the
    checker aligned with the same parser used by the rendered page.
    """
    diagnostics: list[Diagnostic] = []
    block_re = re.compile(r"```\s*mermaid\s*\n([\s\S]*?)```", re.IGNORECASE)
    js_path = Path(__file__).resolve().parent / "renderer" / "assets" / "mermaid.min.js"
    if not js_path.is_file():
        return diagnostics

    for match in block_re.finditer(content):
        block_code = match.group(1).strip()
        if not block_code:
            continue
        start_line = content.count("\n", 0, match.start()) + 1
        script = r"""
const fs = require('fs');
const vm = require('vm');
const code = process.argv[1];
const jsPath = process.argv[2];
const js = fs.readFileSync(jsPath, 'utf8');
const quietConsole = { log() {}, warn() {}, error() {} };

function makeNode() {
    return {
        style: {},
        classList: { add() {} },
        appendChild() { return makeNode(); },
        insertBefore() { return makeNode(); },
        setAttribute() {},
        setAttributeNS() {},
        querySelector() { return null; },
        querySelectorAll() { return []; },
        addEventListener() {},
        removeEventListener() {},
        getBBox() { return { width: 0, height: 0 }; },
        ownerDocument: null,
        firstChild: null,
    };
}

const documentStub = {
    createElement: () => makeNode(),
    createElementNS: () => makeNode(),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    removeEventListener() {},
    body: makeNode(),
    documentElement: makeNode(),
};

const windowStub = {
    document: documentStub,
    addEventListener() {},
    removeEventListener() {},
    location: { href: '' },
    navigator: {},
    Error,
    TypeError,
    SyntaxError,
    Promise,
    structuredClone: (x) => JSON.parse(JSON.stringify(x)),
};

documentStub.defaultView = windowStub;
windowStub.window = windowStub;
windowStub.self = windowStub;

const sandbox = {
    module: { exports: {} },
    exports: {},
    require,
    console: quietConsole,
    window: windowStub,
    document: documentStub,
    navigator: windowStub.navigator,
    location: windowStub.location,
    globalThis: windowStub,
    setTimeout,
    clearTimeout,
    Error,
    TypeError,
    SyntaxError,
    Promise,
    structuredClone: windowStub.structuredClone,
};
sandbox.global = sandbox;
sandbox.self = sandbox;
new vm.Script(js, { filename: 'mermaid.min.js' }).runInNewContext(sandbox);
const mermaid = sandbox.module.exports || sandbox.mermaid || sandbox.window.mermaid;
(async () => {
  try {
    if (!mermaid) {
      process.stdout.write('[]');
      return;
    }
    await mermaid.parse(code);
    process.stdout.write('[]');
  } catch (e) {
    const msg = e && e.message ? e.message : String(e);
    process.stdout.write(JSON.stringify([{ message: msg }]));
  }
})();
"""
        try:
            proc = subprocess.run(
                ["node", "-e", script, block_code, str(js_path)],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return diagnostics

        stdout = (proc.stdout or "").strip()
        if not stdout:
            continue
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            continue
        for item in payload:
            msg = str(item.get("message") or "Mermaid 解析失败").strip()
            if not msg:
                continue
            if msg:
                diagnostics.append(
                    Diagnostic(
                        path=path,
                        line=start_line,
                        kind="Markdown Mermaid",
                        message=f"Mermaid 语法错误：{msg}",
                    )
                )
    return diagnostics


def _check_markdown_file(path: Path) -> list[Diagnostic]:
    content = path.read_text(encoding="utf-8", errors="replace")
    diags = _check_markdown_math(content, str(path))
    diags.extend(_check_markdown_mermaid(content, str(path)))
    if not diags:
        return []
    ordered = sorted(diags, key=lambda d: (d.line, d.kind, d.message))
    return ordered


def _check_rst_file(path: Path) -> list[Diagnostic]:
    content = path.read_text(encoding="utf-8", errors="replace")
    warning_stream = io.StringIO()
    try:
        publish_doctree(
            content,
            source_path=str(path),
            settings_overrides={
                "report_level": 5,
                "halt_level": 5,
                "warning_stream": warning_stream,
                "output_encoding": "utf-8",
            },
        )
    except Exception as exc:  # pragma: no cover - docutils error conversion path
        msg = str(exc).strip()
        if not msg:
            msg = "docutils 解析失败"
        line = _extract_line_number_from_warning(msg)
        return [Diagnostic(path=str(path), line=line, kind="RST Error", message=msg)]
    return _parse_rst_warnings(str(path), str(path), warning_stream.getvalue())


def check_file(path: str | Path) -> list[Diagnostic]:
    """Check a single Markdown or reStructuredText source file.

    The checker intentionally focuses on syntax problems that are meaningful to the
    current NoteManager renderers: Markdown math delimiters, Mermaid fences, and
    docutils parsing warnings from the RST pipeline.
    """
    p = Path(path)
    if not p.is_file():
        return [Diagnostic(path=str(p), line=1, kind="File", message="文件不存在或不可访问。")]

    suffix = p.suffix.lower()
    if suffix in {".md", ".markdown", ".mdown"}:
        return _check_markdown_file(p)
    if suffix == ".rst":
        return _check_rst_file(p)
    return []


def check_files(paths: list[str | Path]) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    for path in paths:
        diags.extend(check_file(path))
    return sorted(diags, key=lambda d: (d.path, d.line, d.kind, d.message))
