"""Daemon-side source validation for the external editor API."""

from pathlib import Path

from notemanager.daemon import NoteManagerDaemon
from notemanager import editor_launcher


def _daemon(tmp_path: Path) -> NoteManagerDaemon:
    daemon = NoteManagerDaemon()
    daemon._web_state_path = tmp_path / "web_state.json"
    daemon._main_id = None
    daemon._main_disconnected_since = None
    return daemon


def test_open_editor_for_managed_source(monkeypatch, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    note = root / "note.md"
    note.write_text("# Demo\n", encoding="utf-8")

    daemon = _daemon(tmp_path)
    daemon._observers = {str(root): None}
    calls = []

    def fake_open(config, path):
        calls.append((config, str(path)))
        return {"ok": True, "editor": "gvim", "file": str(path)}

    monkeypatch.setattr(editor_launcher, "open_in_editor", fake_open)
    monkeypatch.setattr("notemanager.daemon._load_config", lambda: {"editor": ["gvim", "{file}"]})

    result = daemon.open_editor(
        {"kind": "managed", "project": 0, "source_rel_path": "note.md"}
    )

    assert result["ok"] is True
    assert result["source_path"] == str(note.resolve())
    assert calls == [(["gvim", "{file}"], str(note.resolve()))]


def test_open_editor_rejects_traversal_and_bad_project(monkeypatch, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "note.md").write_text("# Demo\n", encoding="utf-8")

    daemon = _daemon(tmp_path)
    daemon._observers = {str(root): None}
    monkeypatch.setattr(editor_launcher, "open_in_editor", lambda *a: {"ok": True})

    assert daemon.open_editor(
        {"project": 0, "source_rel_path": "../outside.md"}
    )["ok"] is False
    assert daemon.open_editor(
        {"project": 5, "source_rel_path": "note.md"}
    )["ok"] is False
    assert daemon.open_editor(
        {"project": 0, "source_rel_path": "note.exe"}
    )["ok"] is False


def test_open_editor_for_temp_page(monkeypatch, tmp_path):
    source = tmp_path / "unmanaged.md"
    source.write_text("# Demo\n", encoding="utf-8")

    daemon = _daemon(tmp_path)
    temp_dir = tmp_path / "temp_views"
    monkeypatch.setattr(daemon, "get_temp_view_dir", lambda: temp_dir)
    monkeypatch.setattr(editor_launcher, "open_in_editor", lambda *a: {"ok": True})
    monkeypatch.setattr("notemanager.daemon._load_config", lambda: {})

    url = daemon._render_temp_view(source)
    key = Path(url).stem
    result = daemon.open_editor({"kind": "temp", "temp_key": key})

    assert result["ok"] is True
    assert result["source_path"] == str(source.resolve())


def test_open_editor_rejects_unknown_temp_key(monkeypatch, tmp_path):
    daemon = _daemon(tmp_path)
    temp_dir = tmp_path / "temp_views"
    temp_dir.mkdir()
    monkeypatch.setattr(daemon, "get_temp_view_dir", lambda: temp_dir)

    assert daemon.open_editor({"temp_key": "deadbeefdeadbeef"})["ok"] is False
    assert daemon.open_editor({"temp_key": "../../etc/passwd"})["ok"] is False
