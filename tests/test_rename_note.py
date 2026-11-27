import json
import threading
from pathlib import Path

from notemanager.builder import full_build
from notemanager.daemon import NoteManagerDaemon


class _FakeDaemon:
    def __init__(self, root):
        self._lock = threading.RLock()
        self._observers = {str(root): object()}
    _validated_source_rel = NoteManagerDaemon._validated_source_rel


def test_rename_managed_source_rebuilds_and_removes_old_html(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    old = root / "a.rst"
    old.write_text("A\n=\n\nbody\n", encoding="utf-8")
    full_build(root)
    assert (root / ".NoteManager" / "html" / "a.html").is_file()

    daemon = _FakeDaemon(root)
    result = NoteManagerDaemon.rename_managed_source(
        daemon, 0, "a.rst", "renamed"
    )
    assert result["ok"] is True
    assert result["source_rel_path"] == "renamed.rst"
    assert (root / "renamed.rst").is_file()
    assert not old.exists()
    assert (root / ".NoteManager" / "html" / "renamed.html").is_file()
    assert not (root / ".NoteManager" / "html" / "a.html").exists()
    index = json.loads(
        (root / ".NoteManager" / "html" / "_index" / "search_index.json").read_text(
            encoding="utf-8"
        )
    )
    assert any(e.get("path") == "renamed.html" for e in index["documents"])
