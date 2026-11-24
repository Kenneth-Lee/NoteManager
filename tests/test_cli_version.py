import subprocess
import sys
from types import SimpleNamespace

from notemanager import __version__, cli


def test_cli_version_flag():
    result = subprocess.run(
        [sys.executable, "-m", "notemanager.cli", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f"NoteManager {__version__}"


def test_status_reports_version_when_daemon_stopped(monkeypatch, capsys):
    monkeypatch.setattr(cli, "is_daemon_running", lambda: None)
    cli.cmd_status(SimpleNamespace(json=False))
    out = capsys.readouterr().out
    assert f"NoteManager 版本: {__version__}" in out
    assert "Daemon 未运行" in out
