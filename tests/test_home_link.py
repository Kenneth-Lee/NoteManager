"""Sidebar Home link: project landing page first, console as fallback.

Design intent:
- managed project pages -> current project's landing page (``/p/N/index.html``)
- temp single-file views (no project) -> console ``/`` so the user can pick one
"""

from pathlib import Path

from notemanager.builder import _DEFAULT_JS
from notemanager.daemon import NoteManagerDaemon
from notemanager.renderer.template import wrap_html


def test_document_home_returns_to_project_landing_page() -> None:
    html = wrap_html("<p>x</p>", "t", "docs/guide.html")
    assert '<div class="nm-sidebar-home"><a href="../index.html">Home</a>' in html
    assert 'id="nm-new-root"' in html


def test_top_level_document_home_returns_to_index() -> None:
    html = wrap_html("<p>x</p>", "t", "guide.html")
    assert '<div class="nm-sidebar-home"><a href="index.html">Home</a>' in html
    assert 'id="nm-new-root"' in html


def test_explicit_home_href_overrides_project_home() -> None:
    html = wrap_html("<p>x</p>", "t", "index.html", home_href="/")
    assert '<div class="nm-sidebar-home"><a href="/">Home</a>' in html
    assert 'id="nm-new-root"' in html


def test_temp_view_home_falls_back_to_console(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "unmanaged.md"
    source.write_text("# Demo\n", encoding="utf-8")

    daemon = NoteManagerDaemon()
    temp_dir = tmp_path / "temp_views"
    monkeypatch.setattr(daemon, "get_temp_view_dir", lambda: temp_dir)
    url = daemon._render_temp_view(source)
    page = temp_dir / Path(url).name
    html = page.read_text(encoding="utf-8")

    assert '<div class="nm-sidebar-home"><a href="/">Home</a>' in html
    assert 'id="nm-new-root"' in html
    # 临时页没有工程首页，必须用确定目标，不能再依赖浏览器历史。
    assert "history.back" not in _DEFAULT_JS
