import json

from notemanager.builder import full_build
from notemanager.indexer import filter_search_documents


def test_index_role_on_title_line_is_searchable(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.rst").write_text(
        ":index:`决策树`\n"
        "***************\n\n"
        "正文\n",
        encoding="utf-8",
    )
    full_build(root)
    index = json.loads(
        (root / ".NoteManager" / "html" / "_index" / "search_index.json").read_text(
            encoding="utf-8"
        )
    )
    hits = filter_search_documents(index["documents"], "index:决策树")
    assert any(h.get("type") == "index" and h.get("text") == "决策树" for h in hits)


def test_keyword_refs_are_emitted_for_browser_resolution():
    from notemanager.ref_resolver import resolve_references
    from notemanager.renderer.rst_renderer import render_rst

    body = render_rst("See :ref:`dtag:Linux` and :ref:`index:决策树`.\n")["body"]
    out = resolve_references(body, "note.html", {})
    assert 'data-nm-keyword-ref="dtag:Linux"' in out
    assert 'data-nm-keyword-ref="index:决策树"' in out
    assert 'href="#ref-dtag:Linux"' not in out
