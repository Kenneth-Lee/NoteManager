from notemanager.renderer.template import wrap_html
from notemanager.web_client_js import CLIENT_BOOTSTRAP_JS


def test_page_id_badge_and_copy_bootstrap_are_present():
    page = wrap_html("<p>x</p>", "X", "x.html")
    assert 'id="nm-page-id"' in page
    assert 'id="nm-page-id"' in page
    assert "nm-page-id" in CLIENT_BOOTSTRAP_JS
    assert "页面 ID:" in CLIENT_BOOTSTRAP_JS


def test_search_help_contains_regex_cheatsheet():
    page = wrap_html("<p>x</p>", "X", "x.html")
    assert "正则表达式速查" in page
    assert "&#92;bpi&#92;b" in page
    assert "chapter:^pi$" in page
    assert "ASCII 单词边界" in page
