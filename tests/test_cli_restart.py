"""CLI start/restart: config.json is authoritative unless CLI flags override."""

from types import SimpleNamespace

from notemanager import cli


def _fake_running_sequence():
    state = {"running": 2}

    def fake_running():
        # First call (before stop) sees the daemon; the wait loop then sees it gone.
        if state["running"] > 0:
            state["running"] -= 1
            return 123
        return None

    return fake_running


class FakeDaemon:
    def __init__(self, port, web_port):
        self.port = port
        self.web_port = web_port
        self.created = {"port": port, "web_port": web_port}


def _patch_restart(monkeypatch, *, config, created, calls=None, current=(19999, 19998)):
    monkeypatch.setattr(cli, "is_daemon_running", _fake_running_sequence())
    monkeypatch.setattr(cli, "_get_daemon_port", lambda: current[0])
    monkeypatch.setattr(cli, "get_daemon_web_port", lambda: current[1])
    monkeypatch.setattr(cli, "_load_config", lambda: config)
    monkeypatch.setattr(
        cli, "send_command",
        lambda *a, **k: (calls.append((a, k)) if calls is not None else None)
        or {"ok": True, "message": "正在停止..."},
    )
    monkeypatch.setattr(cli, "NoteManagerDaemon", lambda port, web_port: _Capture(FakeDaemon, created, port, web_port))
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)


class _Capture:
    def __init__(self, cls, created, port, web_port):
        self.inner = cls(port, web_port)
        created.update(self.inner.created)

    def run_background(self):
        return True


def test_start_restart_falls_back_to_current_ports_when_config_has_none(monkeypatch):
    calls = []
    created = {}
    _patch_restart(monkeypatch, config={}, created=created, calls=calls)

    cli.cmd_start(SimpleNamespace(restart=True, port=0, web_port=0))

    assert calls and calls[0][0][0] == {"cmd": "stop"}
    assert created == {"port": 19999, "web_port": 19998}


def test_start_restart_prefers_config_ports_over_current(monkeypatch):
    created = {}
    _patch_restart(
        monkeypatch,
        config={"port": 18888, "web_port": 18889},
        created=created,
    )

    cli.cmd_start(SimpleNamespace(restart=True, port=0, web_port=0))

    assert created == {"port": 18888, "web_port": 18889}


def test_start_restart_cli_flags_override_config(monkeypatch):
    created = {}
    _patch_restart(
        monkeypatch,
        config={"port": 18888, "web_port": 18889},
        created=created,
    )

    cli.cmd_start(SimpleNamespace(restart=True, port=17777, web_port=17778))

    assert created == {"port": 17777, "web_port": 17778}


def test_start_restart_starts_when_daemon_is_not_running(monkeypatch):
    created = {}

    class Fake:
        def __init__(self, port, web_port):
            created["port"] = port
            created["web_port"] = web_port

        def run_background(self):
            return True

    monkeypatch.setattr(cli, "is_daemon_running", lambda: None)
    monkeypatch.setattr(cli, "_load_config", lambda: {})
    monkeypatch.setattr(cli, "NoteManagerDaemon", Fake)

    cli.cmd_start(SimpleNamespace(restart=True, port=0, web_port=0))

    assert created == {"port": cli.DEFAULT_PORT, "web_port": cli.DEFAULT_WEB_PORT}


def test_plain_start_uses_default_ports(monkeypatch):
    created = {}

    class Fake:
        def __init__(self, port, web_port):
            created["port"] = port
            created["web_port"] = web_port

        def run_background(self):
            return True

    monkeypatch.setattr(cli, "is_daemon_running", lambda: None)
    monkeypatch.setattr(cli, "_load_config", lambda: {})
    monkeypatch.setattr(cli, "NoteManagerDaemon", Fake)

    cli.cmd_start(SimpleNamespace(restart=False, port=0, web_port=0))

    assert created == {"port": cli.DEFAULT_PORT, "web_port": cli.DEFAULT_WEB_PORT}


def test_plain_start_uses_config_ports(monkeypatch):
    created = {}

    class Fake:
        def __init__(self, port, web_port):
            created["port"] = port
            created["web_port"] = web_port

        def run_background(self):
            return True

    monkeypatch.setattr(cli, "is_daemon_running", lambda: None)
    monkeypatch.setattr(cli, "_load_config", lambda: {"port": 18888, "web_port": 18889})
    monkeypatch.setattr(cli, "NoteManagerDaemon", Fake)

    cli.cmd_start(SimpleNamespace(restart=False, port=0, web_port=0))

    assert created == {"port": 18888, "web_port": 18889}
