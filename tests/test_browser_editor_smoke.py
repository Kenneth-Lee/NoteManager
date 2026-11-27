"""Headless-browser smoke tests for the source object editors.

These tests intentionally use the Chromium binary directly instead of adding a
Node/Playwright dependency to the project.  They are skipped when Chromium is
not installed.
"""

from __future__ import annotations

import html
import http.server
import json
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from notemanager.builder import full_build


CHROMIUM = (
    shutil.which("chromium")
    or shutil.which("chromium-browser")
    or shutil.which("google-chrome")
)
pytestmark = pytest.mark.skipif(CHROMIUM is None, reason="Chromium is not installed")


def _make_project(tmp_path: Path, source: str, ext: str = ".md") -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / ("a" + ext)).write_text(source, encoding="utf-8")
    full_build(root)
    return root / ".NoteManager" / "html"


def _run_browser(page: Path, script: str) -> dict:
    page.write_text(
        page.read_text(encoding="utf-8").replace("</body>", script + "</body>"),
        encoding="utf-8",
    )

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(page.parent), **kwargs)

        def translate_path(self, path):
            # The editor only initializes on /p/<project>/... URLs.
            if path.startswith("/p/0/"):
                path = path[len("/p/0"):]
            return super().translate_path(path)

        def log_message(self, format, *args):  # noqa: A002
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/p/0/{page.name}"
        result = subprocess.run(
            [
                CHROMIUM,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--window-size=1400,1000",
                f"--user-data-dir={page.parent / '.browser-profile'}",
                "--virtual-time-budget=8000",
                "--dump-dom",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    match = re.search(r"<title>(.*?)</title>", result.stdout, re.S)
    assert match, result.stderr[-2000:]
    return json.loads(html.unescape(match.group(1)))


def test_source_focus_and_math_palette(tmp_path):
    ui = _make_project(
        tmp_path,
        "# Math\n\ntext\n\nInline $x^2$.\n",
    )
    result = _run_browser(
        ui / "a.html",
        """<script>
window.addEventListener('load', function () {
  setTimeout(function () {
    var out = {};
    try {
      var cm = window.__nmCM;
      document.dispatchEvent(new KeyboardEvent('keydown', {
        code: 'KeyC', key: 'c', ctrlKey: true, altKey: true, bubbles: true
      }));
      setTimeout(function () {
        try {
          out.source = (document.querySelector('.nm-tab.active') || {}).getAttribute('data-tab');
          out.focus = !!(document.activeElement && document.activeElement.closest('.CodeMirror'));
          cm.setCursor({line: 4, ch: 9});
          document.getElementById('nm-source-object').click();
          setTimeout(function () {
            out.math = !!document.querySelector('.nm-math-dialog');
            out.palette = document.querySelectorAll('.nm-math-symbol-btn').length;
            document.title = JSON.stringify(out);
          }, 500);
        } catch (e) { out.error = String(e); document.title = JSON.stringify(out); }
      }, 150);
    } catch (e) { out.error = String(e); document.title = JSON.stringify(out); }
  }, 800);
});
</script>""",
    )
    assert "error" not in result, result
    assert result["source"] == "source"
    assert result["focus"] is True
    assert result["math"] is True
    assert result["palette"] >= 10


def test_mermaid_parameter_preview_and_scoped_q(tmp_path):
    ui = _make_project(
        tmp_path,
        "# Git\n\n```mermaid\ngitGraph\n    commit\n```\n",
    )
    result = _run_browser(
        ui / "a.html",
        """<script>
window.addEventListener('load', function () {
  setTimeout(function () {
    var out = {};
    try {
      var cm = window.__nmCM;
      cm.setOption('keyMap', 'vim');
      document.querySelector('.nm-tab[data-tab="source"]').click();
      setTimeout(function () {
        try {
          cm.setCursor({line: 4, ch: 4});
          document.getElementById('nm-source-object').click();
          setTimeout(function () {
            try {
              out.mermaid = !!document.querySelector('.nm-mermaid-dialog');
              var commit = Array.from(document.querySelectorAll('[data-mermaid-palette]'))
                .filter(function (b) { return b.textContent.indexOf('提交') === 0; })[0];
              commit.click();
              setTimeout(function () {
                try {
                  var id = document.querySelector('[data-param="id"]');
                  id.value = 'abc123';
                  id.dispatchEvent(new Event('input', {bubbles: true}));
                  setTimeout(function () {
                    try {
                      out.param = !!document.querySelector('.nm-mermaid-param-dialog');
                      out.svg = !!document.querySelector('#nm-mermaid-param-diagram svg');
                      document.getElementById('nm-mermaid-param-insert').click();
                      setTimeout(function () {
                        try {
                          var mcm = document.querySelector('.nm-mermaid-dialog .CodeMirror').CodeMirror;
                          out.inserted = mcm.getValue().indexOf('commit id: "abc123"') >= 0;
                          CodeMirror.Vim.handleEx(mcm, 'q');
                          setTimeout(function () {
                            out.closed = !document.querySelector('.nm-mermaid-dialog');
                            out.tab = (document.querySelector('.nm-tab.active') || {}).getAttribute('data-tab');
                            document.title = JSON.stringify(out);
                          }, 250);
                        } catch (e) { out.error = String(e); document.title = JSON.stringify(out); }
                      }, 250);
                    } catch (e) { out.error = String(e); document.title = JSON.stringify(out); }
                  }, 700);
                } catch (e) { out.error = String(e); document.title = JSON.stringify(out); }
              }, 350);
            } catch (e) { out.error = String(e); document.title = JSON.stringify(out); }
          }, 1000);
        } catch (e) { out.error = String(e); document.title = JSON.stringify(out); }
      }, 150);
    } catch (e) { out.error = String(e); document.title = JSON.stringify(out); }
  }, 800);
});
</script>""",
    )
    assert "error" not in result, result
    assert result["mermaid"] is True
    assert result["param"] is True
    assert result["svg"] is True
    assert result["inserted"] is True
    assert result["closed"] is True
    assert result["tab"] == "source"


def test_unsaved_dialog_can_discard(tmp_path):
    ui = _make_project(tmp_path, "# Unsaved\n\nbody\n")
    result = _run_browser(
        ui / "a.html",
        """<script>
window.addEventListener('load', function () {
  setTimeout(function () {
    var out = {};
    try {
      var cm = window.__nmCM;
      document.querySelector('.nm-tab[data-tab="source"]').click();
      setTimeout(function () {
        try {
          var original = cm.getValue();
          cm.setValue(original + '\\nchanged');
          document.querySelector('.nm-tab[data-tab="render"]').click();
          setTimeout(function () {
            try {
              out.choices = document.querySelectorAll('[data-unsaved]').length;
              var discard = document.querySelector('[data-unsaved="discard"]');
              if (discard) discard.click();
              setTimeout(function () {
                out.tab = (document.querySelector('.nm-tab.active') || {}).getAttribute('data-tab');
                out.restored = cm.getValue() === original;
                document.title = JSON.stringify(out);
              }, 300);
            } catch (e) { out.error = String(e); document.title = JSON.stringify(out); }
          }, 250);
        } catch (e) { out.error = String(e); document.title = JSON.stringify(out); }
      }, 150);
    } catch (e) { out.error = String(e); document.title = JSON.stringify(out); }
  }, 800);
});
</script>""",
    )
    assert "error" not in result, result
    assert result["choices"] == 2
    assert result["tab"] == "render"
    assert result["restored"] is True
