"""Cross-platform editor command construction and detached launch."""

import subprocess
from pathlib import Path

from notemanager import editor_launcher


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "note.md"
    path.write_text("# Demo\n", encoding="utf-8")
    return path


def test_default_linux_uses_gvim(monkeypatch, tmp_path):
    monkeypatch.setattr(editor_launcher.sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(
        editor_launcher.shutil, "which",
        lambda name: "/usr/bin/gvim" if name == "gvim" else None,
    )
    src = _source(tmp_path)

    command, err = editor_launcher.build_editor_command(None, src)

    assert err is None
    assert command == ["/usr/bin/gvim", str(src.resolve())]


def test_default_linux_requires_graphical_session(monkeypatch, tmp_path):
    monkeypatch.setattr(editor_launcher.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(
        editor_launcher.shutil, "which",
        lambda name: "/usr/bin/gvim" if name == "gvim" else None,
    )
    src = _source(tmp_path)

    command, err = editor_launcher.build_editor_command(None, src)

    assert command is None
    assert "图形会话" in err


def test_default_macos_prefers_mvim(monkeypatch, tmp_path):
    monkeypatch.setattr(editor_launcher.sys, "platform", "darwin")
    monkeypatch.setattr(
        editor_launcher.shutil, "which",
        lambda name: "/opt/homebrew/bin/mvim" if name == "mvim" else None,
    )
    src = _source(tmp_path)

    command, err = editor_launcher.build_editor_command(None, src)

    assert err is None
    assert command == ["/opt/homebrew/bin/mvim", str(src.resolve())]


def test_default_windows_uses_gvim_exe(monkeypatch, tmp_path):
    monkeypatch.setattr(editor_launcher.sys, "platform", "win32")
    monkeypatch.setattr(
        editor_launcher, "_find_windows_gvim",
        lambda: r"C:\Program Files\Vim\vim91\gvim.exe",
    )
    src = _source(tmp_path)

    command, err = editor_launcher.build_editor_command(None, src)

    assert err is None
    assert command == [r"C:\Program Files\Vim\vim91\gvim.exe", str(src.resolve())]


def test_configured_editor_substitutes_file_and_dir(monkeypatch, tmp_path):
    src = _source(tmp_path)
    command, err = editor_launcher.build_editor_command(
        ["/bin/echo", "-f", "{file}", "--dir", "{dir}"], src,
    )
    assert err is None
    assert command == [
        "/bin/echo", "-f", str(src.resolve()), "--dir", str(src.parent.resolve()),
    ]


def test_configured_editor_appends_file_when_placeholder_missing(tmp_path):
    src = _source(tmp_path)
    command, err = editor_launcher.build_editor_command(["/bin/echo"], src)
    assert err is None
    assert command == ["/bin/echo", str(src.resolve())]


def test_configured_editor_accepts_string(tmp_path):
    src = _source(tmp_path)
    command, err = editor_launcher.build_editor_command('/bin/echo "{file}"', src)
    assert err is None
    assert command == ["/bin/echo", str(src.resolve())]


def test_configured_editor_rejects_unknown_executable(tmp_path):
    src = _source(tmp_path)
    command, err = editor_launcher.build_editor_command(["no-such-editor-xyz", "{file}"], src)
    assert command is None
    assert "找不到编辑器命令" in err


def test_configured_editor_rejects_bad_type(tmp_path):
    src = _source(tmp_path)
    command, err = editor_launcher.build_editor_command({"command": "gvim"}, src)
    assert command is None
    assert "必须是字符串" in err


def test_launch_editor_is_detached_and_returns_immediately(monkeypatch, tmp_path):
    src = _source(tmp_path)
    captured = {}

    class FakeProc:
        pid = 4321

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(editor_launcher.subprocess, "Popen", fake_popen)

    result = editor_launcher.launch_editor(["/bin/echo", str(src)], src)

    assert result["ok"] is True
    assert result["editor"] == "echo"
    assert result["file"] == str(src.resolve())
    assert captured["command"] == ["/bin/echo", str(src)]
    assert captured["kwargs"]["cwd"] == str(src.parent.resolve())
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL
    assert captured["kwargs"]["stdout"] is subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] is subprocess.DEVNULL
    assert captured["kwargs"]["start_new_session"] is True
    assert "shell" not in captured["kwargs"]
