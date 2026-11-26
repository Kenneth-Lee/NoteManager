import sys

import notemanager.cli as cli
from notemanager.daemon import NoteManagerDaemon


def test_custom_page_id_is_valid():
    assert NoteManagerDaemon._valid_web_client_id("notes")
    assert NoteManagerDaemon._valid_web_client_id("work-01")
    assert NoteManagerDaemon._valid_web_client_id("nm-abcd")
    assert not NoteManagerDaemon._valid_web_client_id("bad name")
    assert not NoteManagerDaemon._valid_web_client_id("")


def test_view_accepts_optional_new_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(cli, "cmd_view", lambda args: captured.update(args=args))
    monkeypatch.setattr(sys, "argv", ["nmr", "view", "a.rst", "--new", "notes"])
    cli.main()
    assert captured["args"].new == "notes"


def test_view_accepts_bare_new(monkeypatch):
    captured = {}
    monkeypatch.setattr(cli, "cmd_view", lambda args: captured.update(args=args))
    monkeypatch.setattr(sys, "argv", ["nmr", "view", "a.rst", "--new"])
    cli.main()
    assert captured["args"].new == ""


def test_search_accepts_optional_new_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(cli, "cmd_search", lambda args: captured.update(args=args))
    monkeypatch.setattr(sys, "argv", ["nmr", "search", "term", "--new", "notes"])
    cli.main()
    assert captured["args"].new == "notes"


def test_named_page_url_uses_requested_id():
    import threading

    class Dummy:
        _web_lock = threading.RLock()
        web_port = 19833
        _valid_web_client_id = staticmethod(NoteManagerDaemon._valid_web_client_id)
        _append_query_param = staticmethod(NoteManagerDaemon._append_query_param)

    result = NoteManagerDaemon._new_page_with_optional_id(
        Dummy(), {"new_id": "notes"}, "/p/0/a.html", "cli_view", {}
    )
    assert result["new_client_id"] == "notes"
    assert "nm_client_id=notes" in result["new_url"]


def test_invalid_named_page_is_rejected():
    import threading

    class Dummy:
        _web_lock = threading.RLock()
        web_port = 19833
        _valid_web_client_id = staticmethod(NoteManagerDaemon._valid_web_client_id)
        _append_query_param = staticmethod(NoteManagerDaemon._append_query_param)

    result = NoteManagerDaemon._new_page_with_optional_id(
        Dummy(), {"new_id": "bad name"}, "/p/0/a.html", "cli_view", {}
    )
    assert result["ok"] is False


def test_existing_named_page_is_reused(monkeypatch):
    import threading
    import notemanager.daemon as daemon

    class Dummy:
        _web_lock = threading.RLock()
        web_port = 19833
        _valid_web_client_id = staticmethod(NoteManagerDaemon._valid_web_client_id)
        _append_query_param = staticmethod(NoteManagerDaemon._append_query_param)

    monkeypatch.setattr(daemon.broadcaster, "get_client", lambda cid: object())
    monkeypatch.setattr(daemon.broadcaster, "send_to", lambda cid, event, payload: True)
    result = NoteManagerDaemon._new_page_with_optional_id(
        Dummy(), {"new_id": "notes"}, "/p/0/a.html", "cli_view", {}
    )
    assert result["delivered"] is True
    assert "new_client_id" not in result
