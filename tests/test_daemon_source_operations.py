import threading
from pathlib import Path

from notemanager.builder import full_build
from notemanager.daemon import NoteManagerDaemon


def _bare_daemon(root: Path) -> NoteManagerDaemon:
    daemon = object.__new__(NoteManagerDaemon)
    daemon._lock = threading.Lock()
    daemon._observers = {str(root): None}
    daemon._notify_change = lambda *args, **kwargs: None
    return daemon


def test_save_managed_source_updates_html(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.md").write_text("# A\n\nold\n", encoding="utf-8")
    full_build(root)
    daemon = _bare_daemon(root)

    result = daemon.save_managed_source(0, "a.md", "# A\n\nnew body\n")

    assert result["ok"] is True
    assert (root / "a.md").read_text(encoding="utf-8") == "# A\n\nnew body\n"
    html = (root / ".NoteManager" / "html" / "a.html").read_text(encoding="utf-8")
    assert "new body" in html


def test_create_note_same_dir_uses_source_parent(tmp_path):
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "one.md").write_text("# One\n", encoding="utf-8")
    full_build(root)
    daemon = _bare_daemon(root)

    result = daemon.create_note_same_dir(0, "sub/one.md", "two", ".rst")

    assert result["ok"] is True
    assert result["source_rel_path"] == "sub/two.rst"
    assert (root / "sub" / "two.rst").is_file()
    assert (root / ".NoteManager" / "html" / "sub" / "two.html").is_file()
