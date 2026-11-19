"""Web page registry, main-page election, and targeted view/search routing."""

import http.client
import json
import time

import pytest

from notemanager.builder import _DEFAULT_JS
from notemanager.daemon import NoteManagerDaemon
from notemanager.web import broadcaster, start_web_server
from notemanager.web_client_js import CLIENT_BOOTSTRAP_JS


class FakeWFile:
    def __init__(self) -> None:
        self.data = b""

    def write(self, data: bytes) -> None:
        self.data += data

    def flush(self) -> None:
        pass

    def text(self) -> str:
        return self.data.decode("utf-8", "replace")


@pytest.fixture
def daemon(tmp_path):
    # Start from a clean global registry; tests must not leak fake clients.
    for client in list(broadcaster.list_clients()):
        broadcaster.remove_client(client.wfile)

    d = NoteManagerDaemon()
    d._web_state_path = tmp_path / "web_state.json"
    d._main_id = None
    d._main_disconnected_since = None
    yield d

    for client in list(broadcaster.list_clients()):
        broadcaster.remove_client(client.wfile)


def _project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    note = root / "note.md"
    note.write_text("# Demo\n", encoding="utf-8")
    return root, note


def test_first_page_is_main_and_later_pages_are_secondary(daemon):
    w1, w2 = FakeWFile(), FakeWFile()
    info1 = daemon.register_web_client(w1, "nm-aaaa1111", "/")
    info2 = daemon.register_web_client(w2, "nm-bbbb2222", "/p/0/index.html")

    assert info1["is_main"] is True
    assert info2["is_main"] is False
    assert daemon.get_status()["data"]["main_client"] == "nm-aaaa1111"


def test_view_defaults_to_main_only(daemon, tmp_path):
    root, note = _project(tmp_path)
    daemon._observers = {str(root): None}
    w1, w2 = FakeWFile(), FakeWFile()
    daemon.register_web_client(w1, "nm-aaaa1111", "/")
    daemon.register_web_client(w2, "nm-bbbb2222", "/p/0/index.html")

    resp = daemon.handle_view({"cmd": "view", "file": str(note)})

    assert resp["delivered"] is True
    assert resp["target_client"] == "nm-aaaa1111"
    assert "cli_view" in w1.text()
    assert "cli_view" not in w2.text()


def test_view_can_target_a_specific_page_by_prefix(daemon, tmp_path):
    root, note = _project(tmp_path)
    daemon._observers = {str(root): None}
    w1, w2 = FakeWFile(), FakeWFile()
    daemon.register_web_client(w1, "nm-aaaa1111", "/")
    daemon.register_web_client(w2, "nm-bbbb2222", "/p/0/index.html")

    resp = daemon.handle_view({"cmd": "view", "file": str(note), "target": "nm-bbbb"})

    assert resp["delivered"] is True
    assert resp["target_client"] == "nm-bbbb2222"
    assert "cli_view" not in w1.text()
    assert "cli_view" in w2.text()


def test_view_new_returns_bootstrap_url_without_touching_pages(daemon, tmp_path):
    root, note = _project(tmp_path)
    daemon._observers = {str(root): None}
    w1 = FakeWFile()
    daemon.register_web_client(w1, "nm-aaaa1111", "/")

    resp = daemon.handle_view({"cmd": "view", "file": str(note), "new": True})

    assert resp["ok"] is True
    assert resp["new_client_id"].startswith("nm-")
    assert "nm_client_id=" in resp["new_url"]
    assert "cli_view" not in w1.text()
    assert resp["new_url"].startswith("/p/0/note.html")


def test_main_role_survives_reload_but_hands_off_after_grace(daemon):
    w1, w2 = FakeWFile(), FakeWFile()
    daemon.register_web_client(w1, "nm-aaaa1111", "/")
    daemon.register_web_client(w2, "nm-bbbb2222", "/p/0/index.html")
    broadcaster.touch("nm-bbbb2222")

    daemon.unregister_web_client(w1)
    status = daemon.get_status()["data"]
    # Navigation/reload window: the old main is still favoured and not connected.
    assert status["main_client"] == "nm-aaaa1111"
    assert status["main_connected"] is False

    daemon._main_disconnected_since = time.time() - 10
    status = daemon.get_status()["data"]
    assert status["main_client"] == "nm-bbbb2222"
    assert status["main_connected"] is True


def test_main_role_is_kept_when_same_id_reconnects(daemon):
    w1, w2 = FakeWFile(), FakeWFile()
    daemon.register_web_client(w1, "nm-aaaa1111", "/")
    daemon.register_web_client(w2, "nm-bbbb2222", "/p/0/index.html")

    daemon.unregister_web_client(w1)
    info = daemon.register_web_client(FakeWFile(), "nm-aaaa1111", "/p/0/index.html")

    assert info["is_main"] is True
    assert daemon.get_status()["data"]["main_client"] == "nm-aaaa1111"


def test_handle_main_changes_default_target(daemon):
    w1, w2 = FakeWFile(), FakeWFile()
    daemon.register_web_client(w1, "nm-aaaa1111", "/")
    daemon.register_web_client(w2, "nm-bbbb2222", "/p/0/index.html")

    resp = daemon.handle_main({"cmd": "main", "client": "nm-bbbb"})

    assert resp["ok"] is True
    assert resp["main_client"] == "nm-bbbb2222"
    assert daemon.get_status()["data"]["main_client"] == "nm-bbbb2222"


def test_unknown_target_returns_connected_clients(daemon, tmp_path):
    root, note = _project(tmp_path)
    daemon._observers = {str(root): None}
    w1 = FakeWFile()
    daemon.register_web_client(w1, "nm-aaaa1111", "/")

    resp = daemon.handle_view({"cmd": "view", "file": str(note), "target": "nm-dead"})

    assert resp["ok"] is False
    assert "找不到" in resp["error"]
    assert resp["clients"][0]["id"] == "nm-aaaa1111"


def test_search_new_builds_project_search_url(daemon, tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    daemon._observers = {str(root): None}
    monkeypatch.setattr(
        "notemanager.daemon._first_search_hit_open_url",
        lambda idx, project_dir, query: None,
    )

    resp = daemon.handle_search({"cmd": "search", "query": "hello world", "project": 0, "new": True})

    assert resp["ok"] is True
    assert resp["new_url"].startswith("/p/0/?q=hello%20world")
    assert "nm_client_id=" in resp["new_url"]


def test_append_query_param_keeps_fragment():
    assert NoteManagerDaemon._append_query_param(
        "/p/0/doc.html#anchor", "nm_client_id", "nm-abc"
    ) == "/p/0/doc.html?nm_client_id=nm-abc#anchor"


def test_default_js_has_client_identity_and_target_hooks():
    assert "NM_CLIENT_ID" in _DEFAULT_JS
    assert "/api/client_active" in _DEFAULT_JS
    assert "window.NM_EVENT_SOURCE" in _DEFAULT_JS
    assert "client_id=' + encodeURIComponent" in _DEFAULT_JS or "client_id=" in _DEFAULT_JS
    # 非本工程页面收到 cli_search 时要跳到目标工程，而不是在 /temp 下加载索引。
    assert "curProject !== data.project" in _DEFAULT_JS
    assert "new EventSource('/api/events')" not in _DEFAULT_JS


def test_shared_client_bootstrap_uses_window_name_and_activity_ping():
    assert "window.name" in CLIENT_BOOTSTRAP_JS
    assert "nm_client_id" in CLIENT_BOOTSTRAP_JS
    assert "/api/events?client_id=" in CLIENT_BOOTSTRAP_JS
    assert "/api/client_active" in CLIENT_BOOTSTRAP_JS


def test_sse_registers_client_and_sends_init(daemon):
    server = start_web_server(daemon, 0)
    port = server.server_address[1]
    conn = None
    resp = None
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "GET",
            "/api/events?client_id=nm-sse0001&path=/p/0/index.html",
        )
        resp = conn.getresponse()
        init = None
        while True:
            line = resp.fp.readline()
            if not line:
                break
            if line.startswith(b"data: "):
                init = json.loads(line[6:])
                break
        assert init is not None
        assert init["client_id"] == "nm-sse0001"
        assert init["is_main"] is True
        assert init["clients"][0]["path"] == "/p/0/index.html"
        assert init["clients"][0]["project"] == 0
        assert init["client_count"] == 1
    finally:
        if resp is not None:
            resp.close()
        if conn is not None:
            conn.close()
        server.shutdown()
        server.server_close()
