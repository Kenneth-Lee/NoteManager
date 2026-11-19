"""Web UI hooks for opening the current source in an external editor."""

from pathlib import Path

from notemanager.renderer.template import wrap_html

ASSET = Path(__file__).resolve().parents[1] / "notemanager" / "renderer" / "assets" / "nm-external-editor.js"


def test_page_has_hidden_external_editor_button_and_script():
    html = wrap_html("<p>x</p>", "t", "index.html", source="hello\n", source_path="note.md")
    assert 'id="nm-open-external"' in html
    assert "nm-external-editor.js" in html
    assert 'style="display:none"' in html


def test_external_editor_asset_detects_managed_and_temp_pages():
    js = ASSET.read_text(encoding="utf-8")
    assert "nm-page-meta" in js
    assert "nm-temp-meta" in js
    assert "/api/open_editor" in js
    assert "__nmDirty" in js
    assert "confirm(" in js
    assert "temp_key" in js
