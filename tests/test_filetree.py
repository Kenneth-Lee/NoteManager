import threading
from pathlib import Path

from notemanager import builder
from notemanager.daemon import NoteManagerDaemon


def _bare_daemon(root: Path) -> NoteManagerDaemon:
    daemon = object.__new__(NoteManagerDaemon)
    daemon._lock = threading.Lock()
    daemon._observers = {str(root): None}
    daemon._notify_change = lambda *args, **kwargs: None
    return daemon


def test_landing_tree_has_create_and_delete_actions(tmp_path):
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "one.md").write_text("# One\n\nhello\n", encoding="utf-8")
    builder.full_build(root)
    landing = (root / ".NoteManager" / "html" / "index.html").read_text(encoding="utf-8")

    assert 'data-nm-add-dir="sub"' in landing
    assert 'data-nm-delete-source="sub/one.md"' in landing
    assert 'id="nm-new-root"' in landing
    assert "nm-filetree.js" in landing
    doc = (root / ".NoteManager" / "html" / "sub" / "one.html").read_text(encoding="utf-8")
    assert 'id="nm-delete-current"' in doc
    assert 'data-nm-delete-source="sub/one.md"' in doc
    assert 'data-nm-source-path="sub/one.md"' in doc


def test_create_note_in_dir_and_delete_to_trash(tmp_path):
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "one.md").write_text("# One\n\nhello\n", encoding="utf-8")
    builder.full_build(root)
    daemon = _bare_daemon(root)

    created = daemon.create_note_in_dir(0, "sub", "new note", ".md")
    assert created["ok"] is True
    assert created["source_rel_path"] == "sub/new note.md"
    assert (root / "sub" / "new note.md").is_file()
    assert (root / ".NoteManager" / "html" / "sub" / "new note.html").is_file()

    info = daemon.note_delete_info(0, "sub/new note.md")
    assert info["ok"] is True
    assert info["source_rel_path"] == "sub/new note.md"

    deleted = daemon.delete_managed_source(0, "sub/new note.md")
    assert deleted["ok"] is True
    assert not (root / "sub" / "new note.md").exists()
    assert not (root / ".NoteManager" / "html" / "sub" / "new note.html").exists()
    assert deleted["trash_rel"].startswith(".NoteManager/trash/")
    assert (root / deleted["trash_rel"]).is_file()
