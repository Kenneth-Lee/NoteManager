import json
import re
from pathlib import Path

from notemanager import builder


def test_full_build_writes_activity_and_reading_hooks(tmp_path):
    root = tmp_path / "proj"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    (root / "a" / "one.md").write_text("# One\n\nhello\n", encoding="utf-8")
    (root / "b" / "two.md").write_text("# Two\n\nworld\n", encoding="utf-8")

    builder.full_build(root)
    html_dir = root / ".NoteManager" / "html"
    activity = json.loads(
        (html_dir / "_index" / "activity.json").read_text(encoding="utf-8")
    )
    assert {d["path"] for d in activity["docs"]} == {"a/one.html", "b/two.html"}

    landing = (html_dir / "index.html").read_text(encoding="utf-8")
    assert 'id="nm-reading-home"' in landing
    assert "nm-reading.js" in landing
    project_ids = re.findall(r'data-nm-project="([^"]*)"', landing)
    assert project_ids and project_ids[0]

    doc = (html_dir / "a" / "one.html").read_text(encoding="utf-8")
    assert 'id="nm-reading-controls"' in doc
    assert 'data-nm-page-path="a/one.html"' in doc
    assert project_ids[0] in doc
    assert (html_dir / "_static" / "nm-reading.js").is_file()
