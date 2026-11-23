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
    assert "a" not in {t["name"] for t in overview["themes"]}
    assert any(d["inlinks"] >= 1 for d in overview["core_docs"])

    tree = overview["directory_tree"]
    assert tree["dirs"]["a"]["doc_count"] == 2
    assert tree["dirs"]["a"]["section_count"] > 0
    assert tree["dirs"]["a"]["files"]

    landing = (html_dir / "index.html").read_text(encoding="utf-8")
    assert "nm-dir-overview" in landing
    assert "nm-dir-graph" in landing
    assert "nm-g-node" in landing
    assert "nm-dir-graph-data" in landing
    assert "nm-dir-back" in landing
    assert "nm-overview.js" in landing
    assert "nm-tree" in landing  # directory tree still in the sidebar
    assert "topics.html" in landing
    assert "主题" in landing

    topics_page = (html_dir / "topics.html").read_text(encoding="utf-8")
    assert "nm-topic-section" in topics_page
    assert "a/one.html" in topics_page
    assert "index.html" in topics_page
    for theme in overview["themes"][:6]:
        assert f'topics.html#{theme["id"]}' in landing
        assert f'id="{theme["id"]}"' in topics_page


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
    assert "nm-dir-overview" in (html_dir / "index.html").read_text(encoding="utf-8")


def test_directory_graph_renders_metadata_and_file_sections():
    from notemanager import overview
    tree = overview._build_directory_tree(
        {
            "a/one.html": {
                "title": "One", "path": "a/one.html", "chapter_count": 3,
                "inlinks": 2, "mtime": 0.0,
            },
            "a/two.html": {
                "title": "Two", "path": "a/two.html", "chapter_count": 2,
                "inlinks": 0, "mtime": 0.0,
            },
            "b/three.html": {
                "title": "Three", "path": "b/three.html", "chapter_count": 1,
                "inlinks": 1, "mtime": 0.0,
            },
        },
        recent_cutoff=0.0,
    )
    html = overview.render_directory_overview({"directory_tree": tree})
    assert "nm-dir-overview" in html
    assert "nm-dir-graph" in html
    assert "nm-g-node" in html
    assert "a" in html and "b" in html
    assert "2 篇" in html
    assert "nmRevealSidebarDir" in html
    assert "nm-dir-graph-data" in html
    assert "nm-dir-back" in html
    assert "nm-dir-graph-scale" in html


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


def test_flat_project_uses_directory_tree_and_cards(tmp_path):
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
    assert "treemap" not in overview
    assert any(t["kind"] == "document" for t in overview["themes"])
    landing = (html_dir / "index.html").read_text(encoding="utf-8")
    assert "nm-dir-overview" not in landing
    assert "nm-dir-graph" not in landing
    assert "nm-topic-card" in landing
    assert "topics.html" in landing


def test_sidebar_tree_is_collapsed_by_default():
    from notemanager.builder import _render_tree
    html = _render_tree(
        {"a": {"x.html": {"path": "a/x.html", "title": "X"}}},
        0,
    )
    assert "<details" in html
    assert "nm-dir-" in html
    assert "<details open" not in html
