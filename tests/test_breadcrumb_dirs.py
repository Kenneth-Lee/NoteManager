import re
from urllib.parse import quote

from notemanager.renderer.template import wrap_html


def test_sidebar_breadcrumb_includes_directory_links():
    page = wrap_html(
        "<p>x</p>",
        "决策树",
        "学习笔记/data_science/决策树.html",
        project_name="MySummary.Prv",
    )
    crumb = re.search(r'<div class="nm-sidebar-crumbs">(.*?)</div>', page, re.S)
    assert crumb
    html = crumb.group(1)
    assert "MySummary.Prv" in html
    assert "学习笔记" in html
    assert "data_science" in html
    assert "#nm-dir=" + quote("学习笔记", safe="") in html
    assert "#nm-dir=" + quote("学习笔记/data_science", safe="") in html
    assert html.rstrip().endswith("决策树</span>")


def test_root_document_has_no_directory_crumb():
    page = wrap_html("<p>x</p>", "A", "a.html", project_name="P")
    crumb = re.search(r'<div class="nm-sidebar-crumbs">(.*?)</div>', page, re.S)
    assert crumb
    assert "nm-dir=" not in crumb.group(1)
