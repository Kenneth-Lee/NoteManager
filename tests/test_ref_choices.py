from pathlib import Path

from notemanager.builder import full_build
from notemanager.ref_resolver import extract_labels
from notemanager.scanner import scan_directory


def test_duplicate_rst_labels_produce_candidate_link(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.rst").write_text(".. _same:\n\nA\n=\n\na\n", encoding="utf-8")
    (root / "b.rst").write_text(".. _same:\n\nB\n=\n\nb\n", encoding="utf-8")
    (root / "c.rst").write_text("See :ref:`same`.\n", encoding="utf-8")
    files = scan_directory(root)
    _, candidates = extract_labels(root, files, with_candidates=True)
    assert len(candidates["same"]) == 2
    full_build(root)
    page = (root / ".NoteManager" / "html" / "c.html").read_text(encoding="utf-8")
    assert "data-nm-ref-candidates=" in page
    assert "a.html#same" in page
    assert "b.html#same" in page
