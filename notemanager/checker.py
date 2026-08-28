"""Lightweight syntax checking for NoteManager source files."""

from __future__ import annotations

import io
import json
import re
import subprocess
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from docutils.core import publish_doctree

from notemanager.renderer.md_renderer import render_md


SUPPORTED_MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdown"}
SUPPORTED_RST_SUFFIXES = {".rst"}
CHECK_SCOPES = {"all", "math", "mermaid"}
INPUT_FORMATS = {"md", "rst", "mermaid", "math"}


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
    for open_pat, close_pat, open_token, close_token, label in (
        (r"(?<!\\)\\\(", r"(?<!\\)\\\)", r"\(", r"\)", "行内公式"),
        (r"(?<!\\)\\\[", r"(?<!\\)\\\]", r"\[", r"\]", "块级公式"),
    ):
        opens = []
        for i, line in enumerate(lines, start=1):
            for match in re.finditer(open_pat, line):
                opens.append((i, match.start()))
            for match in re.finditer(close_pat, line):
                if not opens:
                    diagnostics.append(
                        Diagnostic(
                            path=path,
                            line=i,
                            kind="Markdown Math",
                            message=f"LaTeX {label} 缺少对应的开启符号 {open_token}。",
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
                    message=f"LaTeX {label} 未闭合：缺少结束符号 {close_token}。",
                )
            )

    return diagnostics


def _check_markdown_mermaid(content: str, path: str) -> list[Diagnostic]:
    """Check Mermaid with the same markdown-to-html pipeline used by view.

    We parse rendered HTML and read Mermaid ``pre`` text as a browser would do,
    which keeps checker behavior aligned with real page rendering.
    """
    diagnostics: list[Diagnostic] = []
    rendered = render_md(content, source_path=path)
    body = str(rendered.get("body", ""))
    html_blocks = _extract_mermaid_blocks_from_html(body)
    source_start_lines = _list_mermaid_start_lines(content)

    for idx, html_block in enumerate(html_blocks):
        block_code = html_block.strip()
        if not block_code:
            continue
        start_line = source_start_lines[idx] if idx < len(source_start_lines) else (source_start_lines[0] if source_start_lines else 1)
        for msg in _run_mermaid_parse(block_code):
            if _is_ignorable_mermaid_runtime_noise(msg):
                continue
            kind, text = _classify_mermaid_message(msg)
            diagnostics.append(
                Diagnostic(
                    path=path,
                    line=start_line,
                    kind=kind,
                    message=text,
                )
            )
    return diagnostics


def _classify_mermaid_message(msg: str) -> tuple[str, str]:
    lowered = msg.lower()
    runtime_markers = (
        "addhook is not a function",
        "sanitize is not a function",
        "document is not defined",
        "window is not defined",
        "structuredclone is not defined",
        "运行时缺失",
        "运行环境缺失",
        "解析器执行失败",
        "输出不可解析",
    )
    if any(marker in lowered for marker in runtime_markers):
        return (
            "Markdown Mermaid Runtime",
            f"Mermaid 运行环境错误（非文档语法错误）：{msg}",
        )
    if "parse error" in lowered:
        return ("Markdown Mermaid", f"Mermaid 语法错误：{msg}")
    return ("Markdown Mermaid", f"Mermaid 错误：{msg}")


def _is_ignorable_mermaid_runtime_noise(msg: str) -> bool:
    lowered = msg.lower()
    # Known Node sandbox-only noise: these do not indicate source syntax issues.
    markers = (
        "addhook is not a function",
        "sanitize is not a function",
        "createdocument is not a function",
        "document is not defined",
        "window is not defined",
    )
    return any(marker in lowered for marker in markers)


class _MermaidPreExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_mermaid = False
        self._buf: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() != "pre":
            return
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        classes = attr_map.get("class", "")
        if "mermaid" in classes.split():
            self._in_mermaid = True
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._in_mermaid:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "pre" and self._in_mermaid:
            self.blocks.append("".join(self._buf))
            self._in_mermaid = False
            self._buf = []


def _extract_mermaid_blocks_from_html(html_body: str) -> list[str]:
    parser = _MermaidPreExtractor()
    try:
        parser.feed(html_body)
        parser.close()
    except Exception:
        return []
    return parser.blocks


def _list_mermaid_start_lines(markdown_content: str) -> list[int]:
    block_re = re.compile(r"```\s*mermaid\s*\n([\s\S]*?)```", re.IGNORECASE)
    return [markdown_content.count("\n", 0, match.start()) + 1 for match in block_re.finditer(markdown_content)]


def _run_mermaid_parse(block_code: str) -> list[str]:
    js_path = Path(__file__).resolve().parent / "renderer" / "assets" / "mermaid.min.js"
    if not js_path.is_file():
        return ["Mermaid 运行时缺失：未找到 renderer/assets/mermaid.min.js"]

    script = r"""
const fs = require('fs');
const vm = require('vm');
const code = process.argv[1];
const jsPath = process.argv[2];
const js = fs.readFileSync(jsPath, 'utf8');
const quietConsole = { log() {}, warn() {}, error() {} };

function NodeLike() {}
NodeLike.prototype = { nodeType: 1, childNodes: [], ownerDocument: null };

function ElementLike() {
        this.style = {};
        this.classList = { add() {}, remove() {}, contains() { return false; } };
        this.attributes = {};
        this.childNodes = [];
        this.ownerDocument = null;
}
ElementLike.prototype = Object.create(NodeLike.prototype);
ElementLike.prototype.appendChild = function() { return new ElementLike(); };
ElementLike.prototype.insertBefore = function() { return new ElementLike(); };
ElementLike.prototype.setAttribute = function() {};
ElementLike.prototype.setAttributeNS = function() {};
ElementLike.prototype.getAttribute = function() { return null; };
ElementLike.prototype.hasAttribute = function() { return false; };
ElementLike.prototype.removeAttribute = function() {};
ElementLike.prototype.querySelector = function() { return null; };
ElementLike.prototype.querySelectorAll = function() { return []; };
ElementLike.prototype.addEventListener = function() {};
ElementLike.prototype.removeEventListener = function() {};
ElementLike.prototype.getBBox = function() { return { width: 0, height: 0 }; };

function makeNode() {
        const node = new ElementLike();
        node.ownerDocument = null;
        node.firstChild = null;
        node.parentNode = null;
        return node;
}

const ElementCtor = ElementLike;
const NodeCtor = NodeLike;
const NamedNodeMapCtor = function NamedNodeMapCtor() {};
const HTMLTemplateElementCtor = function HTMLTemplateElementCtor() {
        this.content = { ownerDocument: null };
};
const HTMLFormElementCtor = function HTMLFormElementCtor() {};
const NodeFilterCtor = function NodeFilterCtor() {};
const DOMParserCtor = function DOMParserCtor() {
        this.parseFromString = function(text) {
                return { body: { innerHTML: text } };
        };
};

const documentStub = {
        nodeType: 9,
        currentScript: null,
        implementation: {
                createHTMLDocument() {
                        return { body: makeNode(), documentElement: makeNode() };
                },
        createDocument() {
            return { body: makeNode(), documentElement: makeNode() };
        },
        },
        createElement() { return makeNode(); },
        createElementNS() { return makeNode(); },
        createDocumentFragment() { return makeNode(); },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        getElementsByTagName() { return []; },
        addEventListener() {},
        removeEventListener() {},
        body: makeNode(),
        documentElement: makeNode(),
};

const domPurifyStub = {
        addHook() {},
        removeHook() {},
        removeHooks() {},
        sanitize(input) { return input; },
        isSupported: true,
};

const windowStub = {
        document: documentStub,
        Document: function Document() {},
        Element: ElementCtor,
        Node: NodeCtor,
        NamedNodeMap: NamedNodeMapCtor,
        HTMLFormElement: HTMLFormElementCtor,
        HTMLTemplateElement: HTMLTemplateElementCtor,
        NodeFilter: NodeFilterCtor,
        DOMParser: DOMParserCtor,
        addEventListener() {},
        removeEventListener() {},
        location: { href: '' },
        navigator: {},
        Error,
        TypeError,
        SyntaxError,
        Promise,
        structuredClone: (x) => JSON.parse(JSON.stringify(x)),
        DOMPurify: domPurifyStub,
        getComputedStyle() { return {}; },
};

documentStub.defaultView = windowStub;
documentStub.ownerDocument = null;
documentStub.documentElement.ownerDocument = documentStub;
documentStub.body.ownerDocument = documentStub;
windowStub.window = windowStub;
windowStub.self = windowStub;

const sandbox = {
        module: { exports: {} },
        exports: {},
        require,
        console: quietConsole,
        window: windowStub,
        document: documentStub,
        Document: function Document() {},
        navigator: windowStub.navigator,
        location: windowStub.location,
        DOMPurify: domPurifyStub,
        Element: ElementCtor,
        Node: NodeCtor,
        NamedNodeMap: NamedNodeMapCtor,
        HTMLTemplateElement: HTMLTemplateElementCtor,
        HTMLFormElement: HTMLFormElementCtor,
        NodeFilter: NodeFilterCtor,
        DOMParser: DOMParserCtor,
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
        mermaid.initialize({ startOnLoad: false, theme: 'default' });
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
        return ["Mermaid 运行环境缺失：未找到 node 可执行文件"]

    stdout = (proc.stdout or "").strip()
    if not stdout:
        err = (proc.stderr or "").strip()
        if err:
            return [f"Mermaid 解析器执行失败：{err.splitlines()[0]}"]
        return []
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return [f"Mermaid 解析器输出不可解析：{stdout[:120]}"]
    messages: list[str] = []
    for item in payload:
        msg = str(item.get("message") or "Mermaid 解析失败").strip()
        if msg:
            messages.append(msg)
    return messages


def _check_markdown_file(path: Path, *, scope: str = "all") -> list[Diagnostic]:
    content = path.read_text(encoding="utf-8", errors="replace")
    return check_text(content, input_format="md", source_name=str(path), scope=scope)


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


def _check_mermaid_text(content: str, source_name: str) -> list[Diagnostic]:
    text = content.strip()
    if not text:
        return []
    diagnostics: list[Diagnostic] = []
    for msg in _run_mermaid_parse(text):
        if _is_ignorable_mermaid_runtime_noise(msg):
            continue
        kind, text_msg = _classify_mermaid_message(msg)
        normalized_kind = "Mermaid" if kind == "Markdown Mermaid" else "Mermaid Runtime"
        diagnostics.append(
            Diagnostic(
                path=source_name,
                line=1,
                kind=normalized_kind,
                message=text_msg,
            )
        )
    return diagnostics


def check_text(content: str, *, input_format: str, source_name: str = "<stdin>", scope: str = "all") -> list[Diagnostic]:
    """Check text input by explicit format.

    Args:
        content: Source text to check.
        input_format: One of md/rst/mermaid/math.
        source_name: Display name in diagnostics.
        scope: all/math/mermaid; only meaningful for Markdown input.
    """
    fmt = input_format.lower().strip()
    scp = scope.lower().strip()
    if fmt not in INPUT_FORMATS:
        return [Diagnostic(path=source_name, line=1, kind="Check", message=f"未知输入格式: {input_format}")]
    if scp not in CHECK_SCOPES:
        return [Diagnostic(path=source_name, line=1, kind="Check", message=f"未知检查范围: {scope}")]

    if fmt == "math":
        return sorted(_check_markdown_math(content, source_name), key=lambda d: (d.line, d.kind, d.message))
    if fmt == "mermaid":
        return sorted(_check_mermaid_text(content, source_name), key=lambda d: (d.line, d.kind, d.message))
    if fmt == "rst":
        if scp != "all":
            return [
                Diagnostic(
                    path=source_name,
                    line=1,
                    kind="Check",
                    message="RST 输入仅支持 scope=all（math/mermaid 仅适用于 Markdown）。",
                )
            ]
        warning_stream = io.StringIO()
        try:
            publish_doctree(
                content,
                source_path=source_name,
                settings_overrides={
                    "report_level": 5,
                    "halt_level": 5,
                    "warning_stream": warning_stream,
                    "output_encoding": "utf-8",
                },
            )
        except Exception as exc:  # pragma: no cover - docutils error conversion path
            msg = str(exc).strip() or "docutils 解析失败"
            line = _extract_line_number_from_warning(msg)
            return [Diagnostic(path=source_name, line=line, kind="RST Error", message=msg)]
        return sorted(_parse_rst_warnings(source_name, source_name, warning_stream.getvalue()), key=lambda d: (d.line, d.kind, d.message))

    diags: list[Diagnostic] = []
    if scp in {"all", "math"}:
        diags.extend(_check_markdown_math(content, source_name))
    if scp in {"all", "mermaid"}:
        diags.extend(_check_markdown_mermaid(content, source_name))
    return sorted(diags, key=lambda d: (d.line, d.kind, d.message))


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
    if suffix in SUPPORTED_MARKDOWN_SUFFIXES:
        return _check_markdown_file(p, scope="all")
    if suffix in SUPPORTED_RST_SUFFIXES:
        return _check_rst_file(p)
    return []


def check_file_with_options(path: str | Path, *, input_format: str = "auto", scope: str = "all") -> list[Diagnostic]:
    p = Path(path)
    if not p.is_file():
        return [Diagnostic(path=str(p), line=1, kind="File", message="文件不存在或不可访问。")]

    fmt = input_format.lower().strip()
    if fmt == "auto":
        suffix = p.suffix.lower()
        if suffix in SUPPORTED_MARKDOWN_SUFFIXES:
            fmt = "md"
        elif suffix in SUPPORTED_RST_SUFFIXES:
            fmt = "rst"
        else:
            return [Diagnostic(path=str(p), line=1, kind="File", message="无法自动识别格式，请使用 --format 指定。")]

    content = p.read_text(encoding="utf-8", errors="replace")
    return check_text(content, input_format=fmt, source_name=str(p), scope=scope)


def check_files(paths: list[str | Path]) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    for path in paths:
        diags.extend(check_file(path))
    return sorted(diags, key=lambda d: (d.path, d.line, d.kind, d.message))
