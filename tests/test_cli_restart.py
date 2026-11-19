"""CLI start --restart: stop the old daemon, then start with inherited ports."""

from types import SimpleNamespace

from notemanager import cli


def test_start_restart_inherits_running_daemon_ports(monkeypatch):
    calls = []
    state = {"running": 2}

    def fake_running():
        # First call (before stop) sees the daemon; the wait loop then sees it gone.
        if state["running"] > 0:
            state["running"] -= 1
            return 123
        return None

    created = {}

    class FakeDaemon:
        def __init__(self, port, web_port):
            created["port"] = port
            created["web_port"] = web_port

        def run_background(self):
            return True

    monkeypatch.setattr(cli, "is_daemon_running", fake_running)
    monkeypatch.setattr(cli, "_get_daemon_port", lambda: 19999)
    monkeypatch.setattr(cli, "get_daemon_web_port", lambda: 19998)
    monkeypatch.setattr(cli, "send_command", lambda *a, **k: calls.append((a, k)) or {"ok": True, "message": "正在停止..."})
    monkeypatch.setattr(cli, "NoteManagerDaemon", FakeDaemon)
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)

    cli.cmd_start(SimpleNamespace(restart=True, port=0, web_port=0))

    assert calls and calls[0][0][0] == {"cmd": "stop"}
    assert created == {"port": 19999, "web_port": 19998}


def test_start_restart_starts_when_daemon_is_not_running(monkeypatch):
    created = {}

    class FakeDaemon:
        def __init__(self, port, web_port):
            created["port"] = port
            created["web_port"] = web_port

        def run_background(self):
            return True

    monkeypatch.setattr(cli, "is_daemon_running", lambda: None)
    monkeypatch.setattr(cli, "NoteManagerDaemon", FakeDaemon)

    cli.cmd_start(SimpleNamespace(restart=True, port=0, web_port=0))

    assert created == {"port": cli.DEFAULT_PORT, "web_port": cli.DEFAULT_WEB_PORT}


def test_plain_start_uses_default_ports(monkeypatch):
    created = {}

    class FakeDaemon:
        def __init__(self, port, web_port):
            created["port"] = port
            created["web_port"] = web_port

        def run_background(self):
            return True

    monkeypatch.setattr(cli, "is_daemon_running", lambda: None)
    monkeypatch.setattr(cli, "NoteManagerDaemon", FakeDaemon)

    cli.cmd_start(SimpleNamespace(restart=False, port=0, web_port=0))

    assert created == {"port": cli.DEFAULT_PORT, "web_port": cli.DEFAULT_WEB_PORT}
