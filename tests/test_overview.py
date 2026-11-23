import json
from pathlib import Path

from notemanager import builder


def _write_project(root: Path) -> None:
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir(parents=True)
    (root / "a" / "one.md").write_text(
        "# One\n\nIntro one.\n\n## Alpha\n\nAlpha text.\n\n## Common\n\n"
        "Common one. [Two](two.md)\n",
        encoding="utf-8",
    )
    (root / "a" / "two.md").write_text(
        "# Two\n\nIntro two.\n\n## Beta\n\nBeta text.\n\n## Common\n\nCommon two.\n",
        encoding="utf-8",
    )
    (root / "b" / "three.md").write_text(
        "# Three\n\nIntro three. [One](../a/one.md)\n\n## Gamma\n\nGamma text.\n",
        encoding="utf-8",
    )


def _overview(root: Path) -> dict:
    path = root / ".NoteManager" / "html" / "_index" / "overview.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_full_build_creates_overview_and_topic_page(tmp_path):
    root = tmp_path / "proj"
    _write_project(root)
    builder.full_build(root)

    html_dir = root / ".NoteManager" / "html"
    overview = _overview(root)
    assert overview["stats"]["documents"] == 3
    assert overview["stats"]["links"] >= 1
    names = {t["name"] for t in overview["topics"]}
    assert "a" in names
    assert "b" in names
    assert "Common" in names
    assert any(d["inlinks"] >= 1 for d in overview["core_docs"])

    landing = (html_dir / "index.html").read_text(encoding="utf-8")
    assert "nm-treemap" in landing
    assert "nm-tree" in landing  # directory tree still in the sidebar
    assert "topics.html" in landing
    assert "重点主题" in landing

    topics_page = (html_dir / "topics.html").read_text(encoding="utf-8")
    assert "nm-topic-section" in topics_page
    assert "a/one.html" in topics_page
    assert "index.html" in topics_page
    for topic in overview["topics"][:6]:
        assert f'topics.html#{topic["id"]}' in landing
        assert f'id="{topic["id"]}"' in topics_page


def test_update_and_remove_refresh_overview(tmp_path):
    root = tmp_path / "proj"
    _write_project(root)
    builder.full_build(root)
    html_dir = root / ".NoteManager" / "html"

    (root / "c").mkdir()
    new_doc = root / "c" / "four.md"
    new_doc.write_text("# Four\n\n## Delta\n\nNew content.\n", encoding="utf-8")
    builder.update_file(root, new_doc, refresh_static_assets=False)

    overview = _overview(root)
    assert overview["stats"]["documents"] == 4
    assert "c" in {t["name"] for t in overview["topics"]}
    assert "nm-topic-section" in (html_dir / "topics.html").read_text(encoding="utf-8")

    new_doc.unlink()
    builder.remove_file(root, new_doc)

    overview = _overview(root)
    assert overview["stats"]["documents"] == 3
    assert "c" not in {t["name"] for t in overview["topics"]}
    # remove_file also rewrites the landing page with a valid overview.
    assert "nm-treemap" in (html_dir / "index.html").read_text(encoding="utf-8")


def test_treemap_svg_handles_empty_and_many_topics():
    from notemanager import overview
    assert overview._treemap_svg({"treemap": []}) == ""
    items = [
        {"id": f"t{i}", "name": f"主题{i}", "kind": "tag", "doc_count": i + 1,
         "weight": float(i + 1)}
        for i in range(15)
    ]
    svg = overview._treemap_svg({"treemap": items})
    assert "<svg" in svg
    assert svg.count("<rect") == 15
    assert "topics.html#t" in svg


def test_removing_last_document_clears_stale_overview(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    doc = root / "only.md"
    doc.write_text("# Only\n\nSome content.\n", encoding="utf-8")
    builder.full_build(root)
    html_dir = root / ".NoteManager" / "html"
    assert (html_dir / "topics.html").exists()

    doc.unlink()
    builder.remove_file(root, doc)

    assert not (html_dir / "topics.html").exists()
    assert not (html_dir / "_index" / "overview.json").exists()
    assert "NoteManager" in (html_dir / "index.html").read_text(encoding="utf-8")


def test_flat_project_uses_cards_instead_of_tiny_treemap(tmp_path):
    root = tmp_path / "flat"
    root.mkdir()
    for i in range(3):
        (root / f"chapter-{i}.md").write_text(
            f"# Chapter {i}\n\nIntro {i}.\n\n## Section\n\nText {i}.\n",
            encoding="utf-8",
        )
    builder.full_build(root)
    html_dir = root / ".NoteManager" / "html"
    overview = _overview(root)
    assert overview["treemap"] == []
    assert any(t["kind"] == "document" for t in overview["topics"])
    landing = (html_dir / "index.html").read_text(encoding="utf-8")
    assert "nm-treemap" not in landing
    assert "nm-topic-card" in landing
    assert "topics.html" in landing
