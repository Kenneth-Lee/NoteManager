import threading
from pathlib import Path

from notemanager import builder
from notemanager.daemon import NoteManagerDaemon


def _daemon(root: Path) -> NoteManagerDaemon:
    daemon = object.__new__(NoteManagerDaemon)
    daemon._lock = threading.Lock()
    daemon._observers = {str(root): None}
    daemon._notify_change = lambda *args, **kwargs: None
    return daemon


def test_save_annotation_sidecar_and_copy(tmp_path):
    root = tmp_path / "proj"
    (root / "sub" / "_static").mkdir(parents=True)
    image = root / "sub" / "_static" / "diagram.png"
    image.write_bytes(b"png")
    (root / "sub" / "note.md").write_text("# Note\n", encoding="utf-8")
    builder.full_build(root)
    daemon = _daemon(root)

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<ellipse cx="50" cy="50" rx="20" ry="10" fill="none" stroke="#e53935"/>'
        "</svg>"
    )
    result = daemon.save_annotation(0, "sub/_static/diagram.png", svg)
    assert result["ok"] is True
    assert result["annotation_rel_path"] == "sub/_static/diagram.png.ann.svg"
    assert (root / "sub" / "_static" / "diagram.png.ann.svg").read_text() == svg
    assert (
        root / ".NoteManager" / "html" / "sub" / "_static" / "diagram.png.ann.svg"
    ).is_file()

    bad = daemon.save_annotation(
        0, "sub/_static/diagram.png", '<svg><script>alert(1)</script></svg>'
    )
    assert bad["ok"] is False


def test_annotation_script_is_in_document_page(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "note.md").write_text("# Note\n", encoding="utf-8")
    builder.full_build(root)
    page = (root / ".NoteManager" / "html" / "note.html").read_text(encoding="utf-8")
    assert "nm-annotations.js" in page
    assert (
        root / ".NoteManager" / "html" / "_static" / "nm-annotations.js"
    ).is_file()


def test_markdown_image_width_attr_list():
    from notemanager.renderer.md_renderer import render_md

    body = render_md("![alt](_static/diagram.png){width=320px}", "x")["body"]
    assert 'src="_static/diagram.png"' in body
    assert 'width="320px"' in body


def test_rst_image_width_option():
    from notemanager.renderer.rst_renderer import render_rst

    body = render_rst(
        ".. image:: _static/diagram.png\n   :width: 320px\n", "x"
    )["body"]
    assert 'src="_static/diagram.png"' in body
    assert 'width: 320px' in body
