from notemanager.relations import build_relations


def test_same_directory_siblings_exclude_self():
    docs = [
        {"path": "a/x.html", "title": "X", "body": "<p>x</p>"},
        {"path": "a/y.html", "title": "Y", "body": "<p>y</p>"},
        {"path": "b/z.html", "title": "Z", "body": "<p>z</p>"},
    ]
    data = build_relations(docs, [])
    x = data["docs"]["a/x.html"]
    assert [item["path"] for item in x["same_dir"]] == ["a/y.html"]
    assert x["same_dir_count"] == 1
    assert x["same_dir"][0]["title"] == "Y"
    z = data["docs"]["b/z.html"]
    assert z["same_dir"] == []
    assert z["same_dir_count"] == 0
