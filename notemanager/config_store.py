"""Helpers for reading, validating and atomically writing config.json.

The daemon owns the runtime interpretation of the config; this module only
deals with the file representation so the Web config editor and the daemon's
own merge-on-write paths use the same rules.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
from pathlib import Path

from notemanager import editor_launcher

CONFIG_WRITE_LOCK = threading.Lock()

# Values accepted by the Web config editor. Unknown top-level keys are allowed
# so future fields do not get dropped by an older editor.
KNOWN_KEYS = {
    "directories",
    "default_project",
    "editor",
    "port",
    "web_port",
    "transport",
    "socket_path",
}


class ConfigValidationError(ValueError):
    """Raised when a config text cannot be used as a JSON object."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text(path: Path) -> tuple[str | None, str | None]:
    """Return (text, sha256); text is None when the file does not exist."""
    if not path.is_file():
        return None, None
    text = path.read_text(encoding="utf-8")
    return text, sha256_text(text)


def _file_mode(path: Path) -> int | None:
    try:
        return path.stat().st_mode & 0o777
    except OSError:
        return None


def write_text_atomic(path: Path, text: str, mode: int | None = None) -> None:
    """Write *text* to *path* via a temporary file and os.replace()."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            try:
                os.chmod(tmp, mode)
            except OSError:
                pass
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def backup_file(path: Path, backup_path: Path) -> Path | None:
    """Copy the current config to backup_path; return None when absent."""
    if not path.is_file():
        return None
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_path)
    return backup_path


def parse_config_text(text: str) -> dict:
    """Parse and require a top-level JSON object."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigValidationError(
            f"JSON 语法错误（第 {e.lineno} 行第 {e.colno} 列）：{e.msg}"
        ) from e
    if not isinstance(data, dict):
        raise ConfigValidationError("配置文件顶层必须是 JSON 对象")
    return data


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_config_data(data: dict) -> tuple[list[str], list[str]]:
    """Validate known fields.

    Returns (errors, warnings).  Errors block saving; warnings are shown to the
    user but still allow saving.
    """
    errors: list[str] = []
    warnings: list[str] = []

    directories = data.get("directories", [])
    if not isinstance(directories, list) or not all(isinstance(d, str) for d in directories):
        errors.append("directories 必须是字符串数组")
        directories = []
    else:
        missing = [d for d in directories if d and not Path(d).expanduser().is_dir()]
        if missing:
            warnings.append("目录不存在（启动时会跳过）：" + "、".join(missing))

    default_project = data.get("default_project")
    if default_project is not None and not isinstance(default_project, str):
        errors.append("default_project 必须是字符串")
    elif default_project and directories and default_project not in directories:
        warnings.append("default_project 不在 directories 中")

    for key in ("port", "web_port"):
        value = data.get(key)
        if value is None:
            continue
        if not _is_int(value):
            errors.append(f"{key} 必须是整数")
        elif not 1 <= value <= 65535:
            errors.append(f"{key} 必须在 1–65535 之间")

    port = data.get("port")
    web_port = data.get("web_port")
    if _is_int(port) and _is_int(web_port) and port == web_port:
        errors.append("port 与 web_port 不能相同")

    transport = data.get("transport")
    if transport is not None:
        if not isinstance(transport, str) or transport.lower() not in ("unix", "tcp"):
            errors.append('transport 只能是 "unix" 或 "tcp"')
        elif os.name == "nt" and transport.lower() == "unix":
            errors.append("Windows 上不支持 transport=\"unix\"")
        else:
            transport = transport.lower()

    socket_path = data.get("socket_path")
    effective_transport = transport or ("tcp" if os.name == "nt" else "unix")
    if socket_path is not None:
        if not isinstance(socket_path, str):
            errors.append("socket_path 必须是字符串")
        elif effective_transport == "unix" and socket_path and not Path(socket_path).is_absolute():
            errors.append("transport=unix 时 socket_path 必须是绝对路径")

    editor = data.get("editor")
    if editor is not None:
        if isinstance(editor, str):
            if not editor.strip():
                warnings.append("editor 为空字符串")
        elif isinstance(editor, (list, tuple)):
            if not all(isinstance(x, str) for x in editor):
                errors.append("editor 数组中的每一项都必须是字符串")
        else:
            errors.append("editor 必须是字符串或字符串数组")

    return errors, warnings


def editor_command_warning(data: dict) -> str | None:
    """Return a warning when an explicitly configured editor cannot be found."""
    editor = data.get("editor")
    if editor is None:
        return None
    try:
        command, err = editor_launcher.build_editor_command(
            editor, Path("__nm_config_check__.md"),
        )
    except Exception as e:
        return f"editor 配置无法解析: {e}"
    if err:
        return f"editor 不可用: {err}"
    if not command:
        return "editor 配置为空"
    return None


def dump_config(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"
