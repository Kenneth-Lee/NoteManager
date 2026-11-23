import json
from pathlib import Path

from notemanager import builder, relations


def _doc(path: str, title: str, body: str) -> dict:
    return {"path": path, "title": title, "body": body}


def _entries():
    return [
        {"type": "chapter", "path": "a/one.html#common", "title": "Common"},
        {"type": "chapter", "path": "b/two.html#common", "title": "Common"},
        {"type": "dtag", "path": "a/one.html", "title": "共享标签"},
        {"type": "dtag", "path": "b/two.html", "title": "共享标签"},
        {"type": "index", "path": "a/one.html", "title": "索引词"},
        {"type": "index", "path": "b/two.html", "title": "索引词"},
        {"type": "grep", "path": "a/one.html", "text": "一篇关于共享主题的正文，包含足够长的描述。"},
        {"type": "grep", "path": "b/two.html", "text": "另一篇关于共享主题的正文，包含足够长的描述。"},
    ]


def test_relations_backlinks_outgoing_related_and_orphan():
    docs = [
        _doc("a/one.html", "One", '<p><a href="../b/two.html">Two</a></p>'),
        _doc("b/two.html", "Two", '<p><a href="../a/one.html">One</a></p>'),
        _doc("c/solo.html", "Solo", "<p>没有链接。</p>"),
    ]
    data = relations.build_relations(docs, _entries())

    one = data["docs"]["a/one.html"]
    two = data["docs"]["b/two.html"]
    solo = data["docs"]["c/solo.html"]

    assert [x["path"] for x in one["outgoing"]] == ["b/two.html"]
    assert [x["path"] for x in one["backlinks"]] == ["b/two.html"]
    assert one["backlink_count"] == 1
    assert one["orphan"] is False
    assert two["outgoing"][0]["path"] == "a/one.html"
    assert any(x["path"] == "b/two.html" for x in one["related"])
    assert solo["orphan"] is True
    assert data["orphans"][0]["path"] == "c/solo.html"


def test_full_build_writes_relations_and_related_placeholder(tmp_path):
    root = tmp_path / "proj"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    (root / "c").mkdir()
    (root / "a" / "one.md").write_text(
        "# One\n\nIntro.\n\n[Two](../b/two.md)\n", encoding="utf-8"
    )
    (root / "b" / "two.md").write_text(
        "# Two\n\nIntro.\n\n[One](../a/one.md)\n", encoding="utf-8"
    )
    (root / "c" / "solo.md").write_text("# Solo\n\nNo links.\n", encoding="utf-8")

    builder.full_build(root)
    html_dir = root / ".NoteManager" / "html"
    data = json.loads((html_dir / "_index" / "relations.json").read_text(encoding="utf-8"))

    assert data["docs"]["a/one.html"]["outgoing"][0]["path"] == "b/two.html"
    assert data["docs"]["a/one.html"]["backlinks"][0]["path"] == "b/two.html"
    assert data["docs"]["c/solo.html"]["orphan"] is True
    page = (html_dir / "a" / "one.html").read_text(encoding="utf-8")
    assert 'id="nm-related"' in page
    assert "nm-relations.js" in page
    assert (html_dir / "_static" / "nm-relations.js").is_file()


def test_update_file_refreshes_relations(tmp_path):
    root = tmp_path / "proj"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    a = root / "a" / "one.md"
    b = root / "b" / "two.md"
    a.write_text("# One\n\nNo links.\n", encoding="utf-8")
    b.write_text("# Two\n\n[One](../a/one.md)\n", encoding="utf-8")
    builder.full_build(root)
    rel_path = root / ".NoteManager" / "html" / "_index" / "relations.json"

    b.write_text("# Two\n\nNo links now.\n", encoding="utf-8")
    builder.update_file(root, b, refresh_static_assets=False)
    data = json.loads(rel_path.read_text(encoding="utf-8"))

    assert data["docs"]["a/one.html"]["outgoing"] == []
    assert data["docs"]["a/one.html"]["backlinks"] == []
    assert data["docs"]["b/two.html"]["outgoing"] == []
    assert data["docs"]["b/two.html"]["orphan"] is True
