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
    assert 'id="nm-trash-open"' in landing
    assert 'id="nm-assets-open"' in landing
    assert "window.__nmSuppressBuildReload" in (
        root / ".NoteManager" / "html" / "_static" / "notemanager.js"
    ).read_text(encoding="utf-8")
    assert "window.__nmSuppressBuildReload" in (
        root / ".NoteManager" / "html" / "_static" / "nm-filetree.js"
    ).read_text(encoding="utf-8")
    doc = (root / ".NoteManager" / "html" / "sub" / "one.html").read_text(encoding="utf-8")
    assert 'id="nm-trash-open"' in doc
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
    assert created["open_url"] == "/p/0/sub/new%20note.html"
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
    trash_dir = root / deleted["trash_rel"]
    assert (trash_dir / "source" / "sub" / "new note.md").is_file()
    assert (trash_dir / "meta.json").is_file()


def test_exclusive_image_cleanup_and_restore(tmp_path):
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    (root / "other.md").write_text(
        "# Other\n\n![shared](sub/shared.png)\n", encoding="utf-8"
    )
    (root / "sub" / "one.md").write_text(
        "# One\n\n![exclusive](exclusive.png)\n![shared](shared.png)\n",
        encoding="utf-8",
    )
    (root / "sub" / "exclusive.png").write_bytes(b"exclusive")
    (root / "sub" / "shared.png").write_bytes(b"shared")
    builder.full_build(root)
    daemon = _bare_daemon(root)

    import json
    assets = json.loads(
        (root / ".NoteManager" / "html" / "_index" / "assets.json").read_text(
            encoding="utf-8"
        )
    )
    assert assets["assets"]["sub/exclusive.png"]["referenced_by"][0]["source_path"] == "sub/one.md"
    assert len(assets["assets"]["sub/shared.png"]["referenced_by"]) == 2

    info = daemon.note_delete_info(0, "sub/one.md")
    assert info["exclusive_image_count"] == 1
    assert info["shared_image_count"] == 1

    deleted = daemon.delete_managed_source(0, "sub/one.md", delete_assets=True)
    assert deleted["ok"] is True
    assert deleted["assets_deleted"] == ["sub/exclusive.png"]
    assert not (root / "sub" / "one.md").exists()
    assert not (root / "sub" / "exclusive.png").exists()
    assert (root / "sub" / "shared.png").is_file()
    assert not (root / ".NoteManager" / "html" / "sub" / "exclusive.png").exists()

    listed = daemon.list_trash(0)
    assert listed["ok"] is True
    assert len(listed["items"]) == 1
    item = listed["items"][0]
    assert item["asset_count"] == 1

    restored = daemon.restore_trash(0, item["trash_id"])
    assert restored["ok"] is True
    assert (root / "sub" / "one.md").is_file()
    assert (root / "sub" / "exclusive.png").is_file()
    assert (root / "sub" / "shared.png").is_file()
    assert (root / ".NoteManager" / "html" / "sub" / "exclusive.png").is_file()
    assert daemon.list_trash(0)["items"] == []


def test_legacy_trash_entry_can_be_restored(tmp_path):
    import shutil
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    source = root / "sub" / "old.md"
    source.write_text("# Old\n\nlegacy\n", encoding="utf-8")
    builder.full_build(root)
    daemon = _bare_daemon(root)

    batch = root / ".NoteManager" / "trash" / "20200101-000000"
    dest = batch / "sub" / "old.md"
    dest.parent.mkdir(parents=True)
    shutil.move(str(source), str(dest))

    items = daemon.list_trash(0)["items"]
    assert len(items) == 1
    assert items[0]["legacy"] is True
    restored = daemon.restore_trash(0, items[0]["trash_id"])
    assert restored["ok"] is True
    assert source.is_file()


def test_asset_index_reports_orphans_and_missing(tmp_path):
    import json
    root = tmp_path / "proj"
    root.mkdir()
    (root / "note.md").write_text(
        "# Note\n\n![missing](missing.png)\n", encoding="utf-8"
    )
    (root / "orphan.png").write_bytes(b"orphan")
    builder.full_build(root)
    data = json.loads(
        (root / ".NoteManager" / "html" / "_index" / "assets.json").read_text(
            encoding="utf-8"
        )
    )
    assert "orphan.png" in data["orphans"]
    assert "missing.png" in data["missing"]
    assert data["assets"]["missing.png"]["exists"] is False
    assert data["assets"]["orphan.png"]["exists"] is True


def test_orphan_asset_cleanup_and_restore(tmp_path):
    import json
    root = tmp_path / "proj"
    root.mkdir()
    (root / "note.md").write_text("# Note\n\nhello\n", encoding="utf-8")
    (root / "orphan.png").write_bytes(b"orphan")
    builder.full_build(root)
    daemon = _bare_daemon(root)

    listed = daemon.list_orphan_assets(0)
    assert listed["ok"] is True
    assert [x["path"] for x in listed["items"]] == ["orphan.png"]

    trashed = daemon.trash_orphan_assets(0, ["orphan.png"])
    assert trashed["ok"] is True
    assert not (root / "orphan.png").exists()
    data = json.loads(
        (root / ".NoteManager" / "html" / "_index" / "assets.json").read_text(
            encoding="utf-8"
        )
    )
    assert "orphan.png" not in data["orphans"]
    assert "orphan.png" not in data["assets"]

    trash_items = daemon.list_trash(0)["items"]
    asset_batch = [x for x in trash_items if x["kind"] == "assets"][0]
    assert asset_batch["asset_count"] == 1

    restored = daemon.restore_trash(0, asset_batch["trash_id"])
    assert restored["ok"] is True
    assert (root / "orphan.png").is_file()
    data = json.loads(
        (root / ".NoteManager" / "html" / "_index" / "assets.json").read_text(
            encoding="utf-8"
        )
    )
    assert "orphan.png" in data["orphans"]


def test_paste_image_goes_to_directory_static(tmp_path):
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    source = root / "sub" / "my note.md"
    source.write_text("# My Note\n\n", encoding="utf-8")
    builder.full_build(root)
    daemon = _bare_daemon(root)

    result = daemon.save_paste_image(
        0, "sub/my note.md", b"fake-png", "image/png"
    )
    assert result["ok"] is True
    assert result["insert_markdown"].startswith("![](_static/nm-paste-my-note-")
    assert result["insert_rst"].startswith(".. image:: _static/nm-paste-my-note-")
    static_files = list((root / "sub" / "_static").glob("nm-paste-my-note-*.png"))
    assert len(static_files) == 1
    assert (root / ".NoteManager" / "html" / "sub" / "_static" / static_files[0].name).is_file()
