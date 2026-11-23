"""Config file editor: validation, atomic save, backup, and startup config."""

import json
from pathlib import Path

import pytest

import notemanager.daemon as daemon_mod
from notemanager import config_store
from notemanager.daemon import NoteManagerDaemon


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(daemon_mod, "HOME_NM_DIR", home)
    monkeypatch.setattr(daemon_mod, "CONFIG_FILE", home / "config.json")
    monkeypatch.setattr(daemon_mod, "CONFIG_BACKUP_FILE", home / "config.json.bak")
    monkeypatch.setattr(daemon_mod, "DAEMON_FILE", home / "daemon.json")
    return home


def _write_config(home: Path, data: dict) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    daemon_mod.CONFIG_FILE.write_text(text, encoding="utf-8")
    return text


def test_save_config_text_writes_file_and_backup(config_home):
    _write_config(config_home, {"directories": [], "port": 19832, "web_port": 19833})
    daemon = NoteManagerDaemon()
    new_text = json.dumps({
        "directories": [],
        "port": 19999,
        "web_port": 19998,
    }, indent=2) + "\n"

    result = daemon.save_config_text(new_text)

    assert result["ok"] is True
    assert result["changed"] is True
    assert result["restart_required"] is True
    assert daemon_mod.CONFIG_FILE.read_text(encoding="utf-8") == new_text
    assert (config_home / "config.json.bak").is_file()
    assert daemon.web_port == 19833  # not applied to the running process


def test_save_config_text_rejects_bad_json_and_bad_fields(config_home):
    daemon = NoteManagerDaemon()

    bad_json = daemon.save_config_text("{not json")
    assert bad_json["ok"] is False
    assert "JSON" in bad_json["error"]

    bad_port = daemon.save_config_text(json.dumps({"port": 70000}))
    assert bad_port["ok"] is False
    assert "port" in bad_port["error"]

    same_ports = daemon.save_config_text(json.dumps({"port": 19999, "web_port": 19999}))
    assert same_ports["ok"] is False


def test_save_config_text_detects_external_change(config_home):
    _write_config(config_home, {"directories": [], "port": 19832})
    daemon = NoteManagerDaemon()
    loaded = daemon.get_config_file()
    base = loaded["sha256"]

    # Another writer changes the file after the editor loaded it.
    _write_config(config_home, {"directories": [], "port": 18888})
    result = daemon.save_config_text(json.dumps({"port": 19999}), base_sha256=base)
    assert result["ok"] is False
    assert result["conflict"] is True

    forced = daemon.save_config_text(
        json.dumps({"port": 19999}), base_sha256=base, force=True,
    )
    assert forced["ok"] is True


def test_save_config_preserves_unknown_keys(config_home):
    daemon = NoteManagerDaemon()
    text = json.dumps({
        "port": 19832,
        "future_setting": {"a": 1},
    }, indent=2) + "\n"
    result = daemon.save_config_text(text)
    assert result["ok"] is True
    saved = json.loads(daemon_mod.CONFIG_FILE.read_text(encoding="utf-8"))
    assert saved["future_setting"] == {"a": 1}


def test_get_config_file_and_backup_source(config_home):
    text = _write_config(config_home, {"directories": [], "port": 19832, "web_port": 19833})
    daemon = NoteManagerDaemon()
    info = daemon.get_config_file()
    assert info["ok"] is True
    assert info["exists"] is True
    assert info["text"] == text
    assert info["runtime"]["web_port"] == daemon.web_port

    daemon.save_config_text(json.dumps({"directories": [], "port": 19999, "web_port": 19998}))
    backup = daemon.get_config_file("backup")
    assert backup["ok"] is True
    assert backup["exists"] is True
    assert '"port": 19832' in backup["text"]


def test_mutate_config_file_preserves_editor_changes(config_home):
    _write_config(config_home, {
        "directories": ["/old"],
        "port": 19999,
        "web_port": 19998,
        "future_setting": True,
    })
    updated = daemon_mod._mutate_config_file(
        lambda cfg: daemon_mod._cfg_add_directory(cfg, "/new"),
    )
    assert updated is not None
    on_disk = json.loads(daemon_mod.CONFIG_FILE.read_text(encoding="utf-8"))
    assert on_disk["port"] == 19999
    assert on_disk["web_port"] == 19998
    assert on_disk["future_setting"] is True
    assert on_disk["directories"] == ["/old", "/new"]


def test_config_transport_and_socket_are_read_at_startup(config_home):
    _write_config(config_home, {
        "directories": [],
        "transport": "unix",
        "socket_path": str(config_home / "custom.sock"),
    })
    daemon = NoteManagerDaemon()
    assert daemon.transport == "unix"
    assert daemon.socket_path == str(config_home / "custom.sock")


def test_config_editor_page_has_structured_fields_and_restart_warning():
    from notemanager.web import _render_config_editor
    html = _render_config_editor()
    for field_id in (
        "dir-list", "default-project", "port", "web-port", "transport",
        "socket-path", "editor-mode", "editor-command", "form-save",
    ):
        assert f'id="{field_id}"' in html
    # 原始 JSON 仍作为高级修复入口保留
    assert 'id="raw-editor"' in html
    assert 'id="raw-save"' in html
    assert "/api/config_file" in html
    assert "nmr start --restart" in html
    assert "base_sha256" in html


def test_config_store_validation_warnings():
    errors, warnings = config_store.validate_config_data({
        "directories": ["/definitely/not/here"],
        "default_project": "/other",
        "port": 19832,
        "web_port": 19833,
    })
    assert not errors
    assert any("目录不存在" in w for w in warnings)
    assert any("default_project" in w for w in warnings)


def test_pending_config_detects_file_vs_runtime(config_home):
    _write_config(config_home, {
        "directories": [], "port": 19832, "web_port": 19833,
    })
    daemon = NoteManagerDaemon(port=19832, web_port=19833)
    assert daemon.get_pending_config_changes()["fields"] == []

    _write_config(config_home, {
        "directories": [], "port": 19999, "web_port": 19998,
    })
    pending = daemon.get_pending_config_changes()
    assert pending["changed"] is True
    assert {"port", "web_port"} <= set(pending["fields"])

    info = daemon.get_config_file()
    assert info["pending_restart"] is True
    assert "port" in info["pending_fields"]
    status = daemon.get_status()["data"]
    assert status["config_pending"] is True
    assert "port" in status["config_pending_fields"]


def test_pending_config_clears_after_restart(config_home):
    _write_config(config_home, {
        "directories": [], "port": 19999, "web_port": 19998,
    })
    old = NoteManagerDaemon(port=19832, web_port=19833)
    assert old.get_pending_config_changes()["changed"] is True

    fresh = NoteManagerDaemon(port=19999, web_port=19998)
    assert fresh.get_pending_config_changes()["fields"] == []


def test_pending_config_error_for_invalid_json(config_home):
    daemon_mod.CONFIG_FILE.write_text("{bad", encoding="utf-8")
    daemon = NoteManagerDaemon()
    pending = daemon.get_pending_config_changes()
    assert pending["error"]


def test_pending_config_detects_directory_file_change(config_home, tmp_path):
    existing = tmp_path / "project"
    existing.mkdir()
    _write_config(config_home, {
        "directories": [str(existing)], "port": 19832, "web_port": 19833,
    })
    daemon = NoteManagerDaemon(port=19832, web_port=19833)
    daemon._observers = {str(existing): None}
    assert daemon.get_pending_config_changes()["fields"] == []

    _write_config(config_home, {
        "directories": [str(existing), str(tmp_path / "another")],
        "port": 19832, "web_port": 19833,
    })
    assert "directories" in daemon.get_pending_config_changes()["fields"]


def test_config_editor_page_has_pending_banner():
    from notemanager.web import _render_config_editor
    html = _render_config_editor()
    assert 'id="pending-config"' in html
    assert "配置文件与当前运行实例不一致" in html


def test_console_shows_pending_config_banner(config_home):
    _write_config(config_home, {
        "directories": [], "port": 19999, "web_port": 19998,
    })
    daemon = NoteManagerDaemon(port=19832, web_port=19833)
    from notemanager.web import _render_panel
    html = _render_panel(daemon)
    assert "配置文件与当前运行实例不一致" in html
    assert "TCP 端口" in html
    assert "Web 端口" in html
