"""Sidebar hierarchy: console -> project -> document.

Design intent:
- managed project pages -> project landing via the project breadcrumb
- temp single-file views (no project) -> only console + document
"""

from pathlib import Path

from notemanager.builder import _DEFAULT_JS
from notemanager.daemon import NoteManagerDaemon
from notemanager.renderer.template import wrap_html


def test_document_breadcrumb_returns_to_project_landing_page() -> None:
    html = wrap_html(
        "<p>x</p>", "t", "docs/guide.html", project_name="MyProject"
    )
    assert 'class="nm-sidebar-crumbs"' in html
    assert 'href="../index.html" title="项目首页">MyProject</a>' in html
    assert 'href="/" title="返回控制台">控制台</a>' in html
    assert 'id="nm-new-root"' in html


def test_top_level_document_project_crumb_returns_to_index() -> None:
    html = wrap_html(
        "<p>x</p>", "t", "guide.html", project_name="MyProject"
    )
    assert 'href="index.html" title="项目首页">MyProject</a>' in html
    assert 'id="nm-new-root"' in html


def test_explicit_home_href_overrides_project_home() -> None:
    html = wrap_html(
        "<p>x</p>", "t", "index.html", home_href="/", project_name="MyProject"
    )
    assert 'href="/" title="项目首页">MyProject</a>' in html
    assert 'id="nm-new-root"' in html


def test_temp_view_shows_console_and_document_only(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "unmanaged.md"
    source.write_text("# Demo\n", encoding="utf-8")

    daemon = NoteManagerDaemon()
    temp_dir = tmp_path / "temp_views"
    monkeypatch.setattr(daemon, "get_temp_view_dir", lambda: temp_dir)
    url = daemon._render_temp_view(source)
    page = temp_dir / Path(url).name
    html = page.read_text(encoding="utf-8")

    assert 'href="/" title="返回控制台">控制台</a>' in html
    assert "nm-sidebar-home" not in html
    assert 'id="nm-new-root"' in html
    # 临时页没有工程首页，必须用确定目标，不能再依赖浏览器历史。
    assert "history.back" not in _DEFAULT_JS
