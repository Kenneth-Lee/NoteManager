import json
import time
from pathlib import Path

from notemanager.daemon import NoteManagerDaemon
from notemanager.renderer.template import wrap_html


def test_wrap_html_includes_mermaid_error_handler() -> None:
    html = wrap_html(
        '<pre class="mermaid">graph TD\nA-->B</pre>',
        'Demo',
        'demo.html',
    )
    assert 'nmMermaidError' in html
    assert 'mermaid.run' in html


def test_temp_view_refreshes_when_source_file_changes(tmp_path: Path) -> None:
    source = tmp_path / 'demo.md'
    source.write_text('# Demo\n\nhello\n', encoding='utf-8')

    daemon = NoteManagerDaemon()
    daemon._render_temp_view(source)
    first_name = (daemon.get_temp_view_dir() / (daemon._temp_key_for_source(source) + '.html')).read_text(encoding='utf-8')

    source.write_text('# Demo\n\nupdated\n', encoding='utf-8')
    time.sleep(0.02)
    daemon._render_temp_view(source)

    second_name = (daemon.get_temp_view_dir() / (daemon._temp_key_for_source(source) + '.html')).read_text(encoding='utf-8')
    assert 'updated' in second_name
    assert first_name != second_name

    meta = json.loads((daemon.get_temp_view_dir() / (daemon._temp_key_for_source(source) + '.meta.json')).read_text(encoding='utf-8'))
    assert meta['source_path'] == str(source)
    assert meta['mtime_ns'] == source.stat().st_mtime_ns


def test_temp_view_html_includes_auto_refresh_probe(tmp_path: Path) -> None:
    source = tmp_path / 'probe.md'
    source.write_text('# Demo\n\nhello\n', encoding='utf-8')

    daemon = NoteManagerDaemon()
    html = (daemon.get_temp_view_dir() / (daemon._temp_key_for_source(source) + '.html'))
    daemon._render_temp_view(source)
    content = html.read_text(encoding='utf-8')

    assert '/temp/check' in content
    assert 'setInterval' in content
