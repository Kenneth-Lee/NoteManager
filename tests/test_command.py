from pathlib import Path

from notemanager import builder


def test_full_build_writes_command_palette_hooks(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "one.md").write_text("# One\n\nhello\n", encoding="utf-8")

    builder.full_build(root)
    html_dir = root / ".NoteManager" / "html"
    landing = (html_dir / "index.html").read_text(encoding="utf-8")
    doc = (html_dir / "one.html").read_text(encoding="utf-8")

    assert 'id="nm-saved-views"' in landing
    assert "nm-command.js" in landing
    assert "nm-command.js" in doc
    assert "window.nmRunSearch" in (html_dir / "_static" / "notemanager.js").read_text(
        encoding="utf-8"
    )
    assert (html_dir / "_static" / "nm-command.js").is_file()
