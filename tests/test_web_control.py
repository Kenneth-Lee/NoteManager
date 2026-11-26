import threading
from pathlib import Path

from notemanager import daemon, web
from notemanager.daemon import NoteManagerDaemon


def test_log_tail_reads_last_lines(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon, "HOME_NM_DIR", tmp_path)
    (tmp_path / "daemon.log").write_text("one\ntwo\nthree\n", encoding="utf-8")
    result = NoteManagerDaemon.get_log_tail(object.__new__(NoteManagerDaemon), 2)
    assert result["ok"] is True
    assert result["text"] == "two\nthree"
    assert result["path"].endswith("daemon.log")


def _fake_daemon():
    fake = object.__new__(NoteManagerDaemon)
    fake._lock = threading.Lock()
    fake._observers = {}
    fake.config = {}
    fake.get_pending_config_changes = lambda: {
        "changed": False, "fields": [], "error": None,
    }
    return fake


def test_control_panel_has_popup_logs_and_manual_path_add(monkeypatch):
    page = web._render_panel(_fake_daemon())
    assert "id=\"log-modal\"" in page
    assert "id=\"log-view\"" in page
    assert "id=\"dir-input\"" in page
    assert "id=\"add-status\"" in page
    assert "id=\"pick-dir-btn\"" not in page
    assert "浏览" not in page
    assert "/api/logs" in page
    assert "/api/add_async" in page
