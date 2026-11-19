"""Launch a local editor for a source file shown in the Web UI.

The default target is gvim (MacVim on macOS).  A user-supplied ``editor``
configuration can override it with an argv template; ``{file}`` and ``{dir}``
are substituted without going through a shell.
"""

from __future__ import annotations

import glob
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

_SOURCE_SUFFIXES = {".md", ".rst", ".txt"}


def _find_windows_gvim() -> str | None:
    for name in ("gvim", "gvim.exe"):
        exe = shutil.which(name)
        if exe and Path(exe).suffix.lower() == ".exe":
            return exe
    roots = []
    for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        root = os.environ.get(key)
        if root:
            roots.append(root)
    patterns = (
        ("Vim", "vim*", "gvim.exe"),
        ("Vim", "*", "gvim.exe"),
        ("Programs", "Vim", "vim*", "gvim.exe"),
        ("Programs", "Vim", "*", "gvim.exe"),
    )
    found: list[str] = []
    for root in roots:
        for pattern in patterns:
            found.extend(glob.glob(os.path.join(root, *pattern)))
    existing = [p for p in found if Path(p).is_file()]
    return sorted(existing)[-1] if existing else None


def _macvim_app_binary() -> str | None:
    candidates = [
        Path("/Applications/MacVim.app/Contents/bin/mvim"),
        Path.home() / "Applications" / "MacVim.app" / "Contents" / "bin" / "mvim",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _default_editor_tokens() -> tuple[list[str] | None, str | None]:
    """Return default argv tokens (with a ``{file}`` placeholder) and an error."""
    if sys.platform == "win32":
        exe = _find_windows_gvim()
        if not exe:
            return None, (
                "未找到 gvim.exe。请安装 gvim，或在 ~/.NoteManager/config.json "
                '中配置 "editor": ["gvim.exe", "{file}"]'
            )
        return [exe, "{file}"], None

    if sys.platform == "darwin":
        gvim = shutil.which("gvim")
        if gvim:
            return [gvim, "{file}"], None
        mvim = shutil.which("mvim") or _macvim_app_binary()
        if mvim:
            return [mvim, "{file}"], None
        if Path("/Applications/MacVim.app").exists():
            return ["open", "-a", "MacVim", "{file}"], None
        return None, (
            "未找到 gvim 或 MacVim。请安装 MacVim/gvim，或在 "
            '~/.NoteManager/config.json 中配置 "editor"'
        )

    # Linux and other POSIX desktops: gvim needs an X11/Wayland session.
    gvim = shutil.which("gvim")
    if not gvim:
        return None, (
            "未找到 gvim。请安装 gvim，或在 ~/.NoteManager/config.json "
            '中配置 "editor": ["gvim", "{file}"]'
        )
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return None, (
            "没有可用的图形会话（DISPLAY / WAYLAND_DISPLAY 未设置），"
            "无法启动 gvim；请从桌面终端启动 daemon"
        )
    return [gvim, "{file}"], None


def _parse_editor_config(config) -> tuple[list[str] | None, str | None]:
    if config is None:
        return None, None
    if isinstance(config, str):
        text = config.strip()
        if not text:
            return [], None
        try:
            tokens = shlex.split(text, posix=(os.name != "nt"))
        except ValueError as e:
            return None, f"editor 配置无法解析: {e}"
        if os.name == "nt":
            # shlex(posix=False) keeps surrounding quotes on Windows tokens.
            cleaned = []
            for token in tokens:
                if len(token) >= 2 and token[0] == token[-1] and token[0] in ('"', "'"):
                    token = token[1:-1]
                cleaned.append(token)
            tokens = cleaned
        return tokens, None
    if isinstance(config, (list, tuple)):
        return [str(item) for item in config], None
    return None, 'editor 配置必须是字符串或字符串数组，例如 ["gvim", "{file}"]'


def _substitute(tokens: list[str], abs_path: str) -> list[str]:
    dir_path = str(Path(abs_path).parent)
    used_file = False
    out: list[str] = []
    for token in tokens:
        if "{file}" in token:
            used_file = True
        replaced = token.replace("{file}", abs_path).replace("{dir}", dir_path)
        if replaced:
            out.append(replaced)
    if not used_file:
        out.append(abs_path)
    return out


def build_editor_command(
    editor_config, file_path: str | Path,
) -> tuple[list[str] | None, str | None]:
    """Build the argv for opening *file_path*, or return an error."""
    abs_path = str(Path(file_path).expanduser().resolve())

    if editor_config is not None:
        tokens, err = _parse_editor_config(editor_config)
        if err:
            return None, err
        if not tokens:
            return None, "editor 配置为空"
        command = _substitute(tokens, abs_path)
        if not command:
            return None, "editor 配置为空"
        exe = command[0]
        if not Path(exe).is_file() and shutil.which(exe) is None:
            return None, f"找不到编辑器命令: {exe}"
        return command, None

    tokens, err = _default_editor_tokens()
    if err:
        return None, err
    if not tokens:
        return None, "未找到可用的编辑器"
    return _substitute(tokens, abs_path), None


def launch_editor(command: list[str], file_path: str | Path) -> dict:
    """Start the editor detached and return immediately."""
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        return {"ok": False, "error": f"文件不存在: {path}"}

    kwargs = {
        "cwd": str(path.parent),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(command, **kwargs)
    except (OSError, ValueError) as e:
        return {"ok": False, "error": f"启动编辑器失败: {e}"}
    return {
        "ok": True,
        "editor": Path(command[0]).name,
        "file": str(path),
        "pid": getattr(proc, "pid", None),
    }


def open_in_editor(editor_config, file_path: str | Path) -> dict:
    """Build and launch the configured/default editor for *file_path*."""
    command, err = build_editor_command(editor_config, file_path)
    if err:
        return {"ok": False, "error": err}
    assert command is not None
    return launch_editor(command, file_path)
