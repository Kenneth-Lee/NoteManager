import threading
from datetime import date
from pathlib import Path

from notemanager import daemon as daemon_mod
from notemanager.builder import full_build
from notemanager.daemon import NoteManagerDaemon


def _bare_daemon(root: Path) -> NoteManagerDaemon:
    daemon = object.__new__(NoteManagerDaemon)
    daemon._lock = threading.Lock()
    daemon._observers = {str(root): None}
    daemon._notify_change = lambda *args, **kwargs: None
    return daemon


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "source.md").write_text("# Source\n", encoding="utf-8")
    full_build(root)
    return root


def test_project_template_overrides_user_and_substitutes(tmp_path, monkeypatch):
    user_dir = tmp_path / "user-config"
    user_dir.mkdir()
    monkeypatch.setattr(daemon_mod, "HOME_NM_DIR", user_dir)
    (user_dir / "template.md").write_text("USER {{title}}\n", encoding="utf-8")

    root = _project(tmp_path)
    config = root / ".NoteManager"
    config.mkdir(exist_ok=True)
    (config / "template.md").write_text(
        "# {{title}}\n"
        "file={{filename}} stem={{stem}} date={{date}} year={{year}} "
        "project={{project}} dir={{dir}}\n",
        encoding="utf-8",
    )
    daemon = _bare_daemon(root)

    result = daemon.create_note_same_dir(0, "sub/source.md", "two", ".md")

    assert result["ok"] is True
    text = (root / "sub" / "two.md").read_text(encoding="utf-8")
    assert text.startswith("# two\n")
    assert "file=two.md stem=two date=" + date.today().isoformat() in text
    assert "year=" + date.today().strftime("%Y") in text
    assert "project=proj dir=sub" in text
    assert "USER " not in text


def test_user_template_is_fallback_for_rst_and_txt(tmp_path, monkeypatch):
    user_dir = tmp_path / "user-config"
    user_dir.mkdir()
    monkeypatch.setattr(daemon_mod, "HOME_NM_DIR", user_dir)
    (user_dir / "template.rst").write_text(
        "{{title}}\n{{multi:=}}\n{{date}}\n", encoding="utf-8"
    )
    (user_dir / "template.txt").write_text(
        "plain {{filename}} {{date}}\n", encoding="utf-8"
    )

    root = _project(tmp_path)
    daemon = _bare_daemon(root)

    rst = daemon.create_note_same_dir(0, "sub/source.md", "rst-note", ".rst")
    txt = daemon.create_note_same_dir(0, "sub/source.md", "plain-note", ".txt")
    assert rst["ok"] is True and txt["ok"] is True
    assert (root / "sub" / "rst-note.rst").read_text(encoding="utf-8").startswith(
        "rst note\n========\n" + date.today().isoformat()
    )
    assert (root / "sub" / "plain-note.txt").read_text(encoding="utf-8") == (
        "plain plain-note.txt " + date.today().isoformat() + "\n"
    )


def test_missing_template_keeps_default_content(tmp_path, monkeypatch):
    user_dir = tmp_path / "user-config"
    user_dir.mkdir()
    monkeypatch.setattr(daemon_mod, "HOME_NM_DIR", user_dir)
    root = _project(tmp_path)
    daemon = _bare_daemon(root)

    result = daemon.create_note_same_dir(0, "sub/source.md", "default", ".md")
    assert result["ok"] is True
    text = (root / "sub" / "default.md").read_text(encoding="utf-8")
    assert text.startswith("# default\n")
    assert "TODO" in text


def test_create_rejects_other_extension_with_same_html_stem(tmp_path, monkeypatch):
    user_dir = tmp_path / "user-config"
    user_dir.mkdir()
    monkeypatch.setattr(daemon_mod, "HOME_NM_DIR", user_dir)
    root = _project(tmp_path)
    daemon = _bare_daemon(root)

    result = daemon.create_note_same_dir(0, "sub/source.md", "source", ".rst")

    assert result["ok"] is False
    assert "HTML 路径" in result["error"]
    assert not (root / "sub" / "source.rst").exists()
