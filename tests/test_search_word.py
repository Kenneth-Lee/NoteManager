from notemanager.indexer import filter_search_documents


def test_exact_chapter_and_word_boundary():
    entries = [
        {"type": "chapter", "title": "pi", "text": "pi", "path": "pi.html"},
        {"type": "chapter", "title": "acpi", "text": "acpi", "path": "acpi.html"},
        {"type": "chapter", "title": "Pivot", "text": "Pivot", "path": "pivot.html"},
    ]
    exact = filter_search_documents(entries, "chapter:^pi$")
    assert [e["path"] for e in exact] == ["pi.html"]

    whole = filter_search_documents(entries, r"chapter:\bpi\b")
    assert [e["path"] for e in whole] == ["pi.html"]


def test_markdown_keyword_ref_links():
    from notemanager.renderer.md_renderer import render_md

    body = render_md(
        "参见 [Linux 笔记](dtag:Linux) 和 [决策树](index:决策树)。\n",
        "note.md",
    )["body"]
    assert 'data-nm-keyword-ref="dtag:Linux"' in body
    assert 'data-nm-keyword-ref="index:决策树"' in body
    assert 'href="dtag:Linux"' not in body
    assert 'href="index:决策树"' not in body
