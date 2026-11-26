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
