from pathlib import Path

from notemanager.builder import full_build
from notemanager.daemon import _need_rebuild_on_startup


def test_editing_existing_note_invalidates_startup_shortcut(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    note = root / "a.rst"
    note.write_text("A\n=\n\nbody\n", encoding="utf-8")
    full_build(root)
    assert _need_rebuild_on_startup(root) is False

    # Same file count and directory mtime; only content changes.
    note.write_text("A\n=\n\nchanged body\n", encoding="utf-8")
    assert _need_rebuild_on_startup(root) is True
