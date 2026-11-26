"""Regression tests for the SSE connection leak.

Every NoteManager page opens a never-ending ``/api/events`` stream.  When the
same tab navigates again it keeps its ``client_id`` (stored in ``window.name``),
so the daemon must close the previous stream when the new page registers.
Otherwise the stale stream keeps one of the browser's six per-host connections
busy forever; after a few navigations the pool is full and the next page (even
its ``document``) queues for tens of seconds.
"""

import re
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from notemanager.daemon import NoteManagerDaemon
from notemanager.web import EventBroadcaster, broadcaster


def _pair():
    """A socketpair plus an unbuffered wfile-like writer for the server side."""
    srv, cli = socket.socketpair()
    return srv, cli, srv.makefile("wb", 0)


def _closed(sock, timeout=2.0):
    """True when the peer closed the stream (EOF or reset) within *timeout*."""
    sock.settimeout(timeout)
    try:
        return sock.recv(1) == b""
    except OSError:
        return True


def _close(*sockets):
    for sock in sockets:
        try:
            sock.close()
        except Exception:
            pass


def test_add_client_closes_previous_stream_for_same_id():
    bus = EventBroadcaster()
    s1, c1, w1 = _pair()
    s2, c2, w2 = _pair()
    try:
        bus.add_client("nm-dup", w1, "/p/0/a.html")
        bus.add_client("nm-dup", w2, "/p/0/b.html")
        assert _closed(c1), "old SSE stream was not closed after re-registering"
        assert bus.get_client("nm-dup") is not None
    finally:
        _close(s1, s2, c1, c2)


def test_add_client_keeps_other_pages_open():
    bus = EventBroadcaster()
    s1, c1, w1 = _pair()
    s2, c2, w2 = _pair()
    try:
        bus.add_client("nm-a", w1, "/p/0/a.html")
        bus.add_client("nm-b", w2, "/p/0/b.html")
        assert bus.send_to("nm-a", "ping", {"ok": True})
        c1.settimeout(2.0)
        assert b"ping" in c1.recv(256)
        assert bus.send_to("nm-b", "ping", {"ok": True})
    finally:
        _close(s1, s2, c1, c2)


def test_daemon_register_closes_replaced_page():
    daemon = NoteManagerDaemon()
    daemon._main_id = "nm-tab"          # keep web_state.json untouched
    daemon._main_disconnected_since = None
    s1, c1, w1 = _pair()
    s2, c2, w2 = _pair()
    try:
        daemon.register_web_client(w1, "nm-tab", "/p/0/a.html")
        daemon.register_web_client(w2, "nm-tab", "/p/0/b.html")
        assert _closed(c1), "daemon kept the previous page's SSE stream open"
        assert broadcaster.get_client("nm-tab") is not None
    finally:
        daemon.unregister_web_client(w2)
        _close(s1, s2, c1, c2)


def test_bootstrap_closes_stream_on_pagehide():
    from notemanager.web_client_js import CLIENT_BOOTSTRAP_JS

    assert "pagehide" in CLIENT_BOOTSTRAP_JS
    assert "openEventStream" in CLIENT_BOOTSTRAP_JS
    assert "closeEventStream" in CLIENT_BOOTSTRAP_JS


def test_asset_version_is_stable_across_renders():
    from notemanager.renderer import template

    kwargs = dict(body="x", title="t", html_rel_path="index.html", home_href="/")
    v1 = re.search(r"notemanager\.js\?v=(\d+)", template.wrap_html(**kwargs)).group(1)
    v2 = re.search(r"notemanager\.js\?v=(\d+)", template.wrap_html(**kwargs)).group(1)
    assert v1 == v2, "asset version changed between two renders in one process"


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print("FAIL", name, "->", repr(exc))
    raise SystemExit(1 if failed else 0)
