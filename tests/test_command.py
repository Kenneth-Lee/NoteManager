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
    assert 'id="nm-command-hint-btn"' in doc
    assert 'id="nm-shortcuts-help-link"' in doc
    command_js = (html_dir / "_static" / "nm-command.js").read_text(encoding="utf-8")
    assert "nm-help-overlay" in command_js
    assert "nmOpenShortcutsHelp" in command_js
    assert "window.nmRunSearch" in (html_dir / "_static" / "notemanager.js").read_text(
        encoding="utf-8"
    )
    assert (html_dir / "_static" / "nm-command.js").is_file()


def test_ensure_refreshes_old_command_asset(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "one.md").write_text("# One\n\nhello\n", encoding="utf-8")
    builder.full_build(root)
    html_dir = root / ".NoteManager" / "html"
    static = html_dir / "_static" / "nm-command.js"
    static.write_text("/* command palette */\n", encoding="utf-8")

    builder._ensure_overview_assets(html_dir)

    assert "nmOpenShortcutsHelp" in static.read_text(encoding="utf-8")
