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


def _run_browser(page: Path, script: str, suffix: str = "") -> dict:
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
        url = f"http://127.0.0.1:{server.server_port}/p/0/{page.name}{suffix}"
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


def test_home_hash_directory_is_used_for_new_note(tmp_path):
    root = tmp_path / "project"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "one.md").write_text("# One\n", encoding="utf-8")
    full_build(root)
    page = root / ".NoteManager" / "html" / "index.html"
    result = _run_browser(
        page,
        """<script>
window.addEventListener('load', function () {
  setTimeout(function () {
    document.getElementById('nm-new-root').click();
    setTimeout(function () {
      var dir = document.getElementById('nm-note-dir');
      var crumb = document.getElementById('nm-sidebar-current');
      document.title = JSON.stringify({
        dir: dir ? dir.textContent : null,
        crumb: crumb ? crumb.textContent : null
      });
    }, 300);
  }, 800);
});
</script>""",
        suffix="#nm-dir=sub",
    )
    assert result["dir"] == "sub"
    assert result["crumb"] == "sub"


def test_clicking_leaf_directory_updates_current_path(tmp_path):
    root = tmp_path / "project"
    (root / "sub" / "leaf").mkdir(parents=True)
    (root / "sub" / "leaf" / "one.md").write_text("# One\n", encoding="utf-8")
    full_build(root)
    page = root / ".NoteManager" / "html" / "index.html"
    result = _run_browser(
        page,
        """<script>
window.addEventListener('load', function () {
  setTimeout(function () {
    var link = document.querySelector('a.nm-g-node[data-dir-path="sub/leaf"]');
    if (!link) { document.title = JSON.stringify({error: 'no leaf node'}); return; }
    var graph = document.querySelector('.nm-dir-graph');
    var widthBefore = graph ? graph.style.width : null;
    var slider = document.getElementById('nm-dir-graph-scale');
    slider.value = '200';
    slider.dispatchEvent(new Event('input', {bubbles: true}));
    link.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
    setTimeout(function () {
      var graphAfter = document.querySelector('.nm-dir-graph');
      var widthAfter = graphAfter ? graphAfter.style.width : null;
      var wrapAfter = graphAfter ? graphAfter.parentElement : null;
      var scaleLabel = document.getElementById('nm-dir-graph-scale-label');
      document.getElementById('nm-new-root').click();
      setTimeout(function () {
        var dir = document.getElementById('nm-note-dir');
        var crumb = document.getElementById('nm-sidebar-current');
        document.title = JSON.stringify({
          hash: decodeURIComponent(location.hash),
          dir: dir ? dir.textContent : null,
          crumb: crumb ? crumb.textContent : null,
          widthBefore: widthBefore,
          widthAfter: widthAfter,
          wrapWidth: wrapAfter ? wrapAfter.clientWidth : null,
          scale: scaleLabel ? scaleLabel.textContent : null
        });
      }, 250);
    }, 350);
  }, 900);
});
</script>""",
    )
    assert result["hash"] == "#nm-dir=sub/leaf"
    assert result["dir"] == "sub/leaf"
    assert result["crumb"] == "sub/leaf"
    assert result["scale"] == "100%"
    assert result["widthAfter"]
    assert int(result["widthAfter"].replace("px", "")) <= result["wrapWidth"]


def test_large_radial_graph_starts_fitted_without_scrollbars(tmp_path):
    root = tmp_path / "project"
    for i in range(18):
        d = root / ("dir-%02d" % i)
        d.mkdir(parents=True)
        for j in range(3):
            (d / ("doc-%02d.md" % j)).write_text("# Doc\n", encoding="utf-8")
    full_build(root)
    page = root / ".NoteManager" / "html" / "index.html"
    result = _run_browser(
        page,
        """<script>
window.addEventListener('load', function () {
  setTimeout(function () {
    var graph = document.querySelector('.nm-dir-graph');
    var wrap = graph ? graph.parentElement : null;
    var label = document.getElementById('nm-dir-graph-scale-label');
    var r = graph ? graph.getBoundingClientRect() : null;
    document.title = JSON.stringify({
      scale: label ? label.textContent : null,
      w: r ? Math.round(r.width) : null,
      h: r ? Math.round(r.height) : null,
      cw: wrap ? wrap.clientWidth : null,
      ch: wrap ? wrap.clientHeight : null,
      sw: wrap ? wrap.scrollWidth : null,
      sh: wrap ? wrap.scrollHeight : null
    });
  }, 1200);
});
</script>""",
    )
    assert result["scale"] == "100%"
    assert result["w"] <= result["cw"] + 1, result
    assert result["h"] <= result["ch"] + 1, result
    assert result["sw"] <= result["cw"] + 1, result
    assert result["sh"] <= result["ch"] + 1, result


def test_external_build_does_not_reload_page_with_unsaved_edits(tmp_path):
    ui = _make_project(tmp_path, "# Dirty\n\nbody\n")
    page = ui / "a.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</body>",
            """<script>
window.addEventListener('load', function () {
  setTimeout(function () {
    if (sessionStorage.getItem('nmReloadProbe') === '1') {
      document.title = 'RELOADED';
      return;
    }
    sessionStorage.setItem('nmReloadProbe', '1');
    var es = window.NM_EVENT_SOURCE;
    if (!es) { document.title = 'NO_EVENT_SOURCE'; return; }
    window.__nmDirty = true;
    es.dispatchEvent(new MessageEvent('build_complete', {
      data: JSON.stringify({project: 0})
    }));
    setTimeout(function () {
      document.title = 'STILL_OPEN';
    }, 500);
  }, 800);
});
</script></body>""",
        ),
        encoding="utf-8",
    )

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(page.parent), **kwargs)

        def translate_path(self, path):
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
        completed = subprocess.run(
            [
                CHROMIUM,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--window-size=1400,1000",
                f"--user-data-dir={page.parent / '.browser-profile'}",
                "--virtual-time-budget=5000",
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

    match = re.search(r"<title>(.*?)</title>", completed.stdout, re.S)
    assert match, completed.stderr[-2000:]
    assert html.unescape(match.group(1)) == "STILL_OPEN"


def test_source_toolbar_stays_outside_editor_scroll(tmp_path):
    lines = ["# Toolbar", ""] + ["line %d" % i for i in range(300)]
    ui = _make_project(tmp_path, "\n".join(lines) + "\n")
    result = _run_browser(
        ui / "a.html",
        """<script>
window.addEventListener('load', function () {
  setTimeout(function () {
    document.querySelector('.nm-tab[data-tab="source"]').click();
    setTimeout(function () {
      var cm = window.__nmCM;
      var toolbar = document.querySelector('.nm-source-toolbar');
      var scroller = cm.getScrollerElement();
      var before = Math.round(toolbar.getBoundingClientRect().top);
      cm.refresh();
      scroller.scrollTop = scroller.scrollHeight;
      setTimeout(function () {
        document.title = JSON.stringify({
          before: before,
          after: Math.round(toolbar.getBoundingClientRect().top),
          scroll: scroller.scrollTop
        });
      }, 250);
    }, 1000);
  }, 800);
});
</script>""",
    )
    assert result["scroll"] > 0, result
    assert result["before"] == result["after"]

def test_render_tab_is_visible_after_source_layout_change(tmp_path):
    ui = _make_project(tmp_path, "# Render\n\nvisible body\n")
    result = _run_browser(
        ui / "a.html",
        """<script>
window.addEventListener('load', function () {
  setTimeout(function () {
    var source = document.getElementById('nm-panel-source');
    var render = document.getElementById('nm-panel-render');
    var out = {
      initialRender: getComputedStyle(render).display,
      initialSource: getComputedStyle(source).display
    };
    document.querySelector('.nm-tab[data-tab="source"]').click();
    setTimeout(function () {
      out.sourceActive = getComputedStyle(source).display;
      out.renderHidden = getComputedStyle(render).display;
      document.querySelector('.nm-tab[data-tab="render"]').click();
      setTimeout(function () {
        out.renderActive = getComputedStyle(render).display;
        out.sourceHidden = getComputedStyle(source).display;
        document.title = JSON.stringify(out);
      }, 250);
    }, 250);
  }, 800);
});
</script>""",
    )
    assert result["initialRender"] != "none"
    assert result["initialSource"] == "none"
    assert result["sourceActive"] != "none"
    assert result["renderHidden"] == "none"
    assert result["renderActive"] != "none"
    assert result["sourceHidden"] == "none"


def test_render_vim_scroll_keys_when_source_vim_enabled(tmp_path):
    body = "\n\n".join("paragraph %d" % i for i in range(250))
    ui = _make_project(tmp_path, "# Render Vim\n\n" + body + "\n")
    result = _run_browser(
        ui / "a.html",
        """<script>
window.addEventListener('load', function () {
  setTimeout(function () {
    var checkbox = document.getElementById('nm-source-vim');
    checkbox.click();
    document.querySelector('.nm-tab[data-tab="render"]').click();
    setTimeout(function () {
      var panel = document.getElementById('nm-panel-render');
      function press(key, opts) {
        var ev = new KeyboardEvent('keydown', Object.assign({key: key, bubbles: true, cancelable: true}, opts || {}));
        document.dispatchEvent(ev);
      }
      panel.scrollTop = 0;
      press('j');
      var afterJ = panel.scrollTop;
      press('G');
      var afterG = panel.scrollTop;
      press('g');
      press('g');
      var afterGG = panel.scrollTop;
      press('/');
      var search = document.querySelector('.nm-vim-search');
      document.title = JSON.stringify({
        afterJ: afterJ,
        afterG: afterG,
        afterGG: afterGG,
        searchVisible: !!(search && !search.hidden)
      });
    }, 400);
  }, 900);
});
</script>""",
    )
    assert result["afterJ"] > 0, result
    assert result["afterG"] > result["afterJ"], result
    assert result["afterGG"] == 0, result
    assert result["searchVisible"] is True


def test_render_vim_search_n_and_shift_n(tmp_path):
    body = (
        "alpha first paragraph\n\n"
        + "\n\n".join("filler %d" % i for i in range(80))
        + "\n\nalpha second paragraph\n\n"
        + "\n\n".join("later %d" % i for i in range(80))
        + "\n\nalpha third paragraph\n"
    )
    ui = _make_project(tmp_path, "# Search\n\n" + body)
    result = _run_browser(
        ui / "a.html",
        """<script>
window.addEventListener('load', function () {
  setTimeout(function () {
    document.getElementById('nm-source-vim').click();
    document.querySelector('.nm-tab[data-tab="render"]').click();
    setTimeout(function () {
      var panel = document.getElementById('nm-panel-render');
      panel.scrollTop = 0;
      function topOfSelection() {
        var sel = window.getSelection();
        if (!sel || !sel.rangeCount) return null;
        var node = sel.anchorNode;
        if (!node) return null;
        var el = node.nodeType === 1 ? node : node.parentElement;
        return el ? Math.round(el.getBoundingClientRect().top) : null;
      }
      function press(key) {
        document.dispatchEvent(new KeyboardEvent('keydown', {
          key: key, bubbles: true, cancelable: true
        }));
      }
      var first = window.nmRenderVim.runSearch('alpha', 1, false);
      var top1 = topOfSelection();
      press('n');
      var top2 = topOfSelection();
      press('N');
      var top3 = topOfSelection();
      press('?');
      var box = document.querySelector('.nm-vim-search');
      document.title = JSON.stringify({
        first: first,
        top1: top1,
        top2: top2,
        top3: top3,
        reverseBox: !!(box && !box.hidden)
      });
    }, 400);
  }, 900);
});
</script>""",
    )
    assert result["first"] is True, result
    assert result["top1"] is not None
    assert result["top2"] > result["top1"], result
    assert result["top3"] <= result["top2"], result
    assert result["reverseBox"] is True


def test_sidebar_view_hotkeys_and_labels(tmp_path):
    ui = _make_project(tmp_path, "# Sidebar\n\nbody\n")
    result = _run_browser(
        ui / "a.html",
        """<script>
window.addEventListener('load', function () {
  setTimeout(function () {
    var toc = document.getElementById('nm-sidebar-view-toc');
    var results = document.getElementById('nm-sidebar-view-results');
    document.dispatchEvent(new KeyboardEvent('keydown', {
      code: 'KeyF', key: 'f', ctrlKey: true, altKey: true, bubbles: true
    }));
    var resultsActive = results.classList.contains('active');
    document.dispatchEvent(new KeyboardEvent('keydown', {
      code: 'KeyD', key: 'd', ctrlKey: true, altKey: true, bubbles: true
    }));
    document.getElementById('nm-shortcuts-help-link').click();
    setTimeout(function () {
      document.title = JSON.stringify({
        resultsActive: resultsActive,
        tocActive: toc.classList.contains('active'),
        tocLabel: toc.textContent,
        resultsLabel: results.textContent,
        searchHelp: (document.getElementById('nm-search-help') || {}).textContent || '',
        help: (document.querySelector('.nm-help-overlay') || {}).textContent || ''
      });
    }, 150);
  }, 800);
});
</script>""",
    )
    assert result["resultsActive"] is True
    assert result["tocActive"] is True
    assert result["tocLabel"].strip() == "目录"
    assert result["resultsLabel"].strip() == "搜索结果"
    assert "Ctrl/⌘+Alt+D" not in result["searchHelp"]
    assert "Ctrl/⌘+Alt+D" in result["help"]
    assert "Ctrl/⌘+Alt+F" in result["help"]
    assert "Vim" in result["help"]


def test_render_vim_search_enter_closes_box_and_n_works(tmp_path):
    ui = _make_project(
        tmp_path,
        "# Search\n\nalpha first\n\n" + "\n\n".join("filler %d" % i for i in range(30))
        + "\n\nalpha second\n",
    )
    result = _run_browser(
        ui / "a.html",
        """<script>
window.addEventListener('load', function () {
  setTimeout(function () {
    document.getElementById('nm-source-vim').click();
    document.querySelector('.nm-tab[data-tab="render"]').click();
    setTimeout(function () {
      document.dispatchEvent(new KeyboardEvent('keydown', {
        key: '/', bubbles: true, cancelable: true
      }));
      var box = document.querySelector('.nm-vim-search');
      var input = box.querySelector('.nm-vim-search-input');
      input.value = 'alpha';
      input.dispatchEvent(new KeyboardEvent('keydown', {
        key: 'Enter', bubbles: true, cancelable: true
      }));
      var closed = box.hidden;
      var sel = window.getSelection();
      var firstTop = sel && sel.rangeCount
        ? Math.round(sel.anchorNode.parentElement.getBoundingClientRect().top) : null;
      document.dispatchEvent(new KeyboardEvent('keydown', {
        key: 'n', bubbles: true, cancelable: true
      }));
      sel = window.getSelection();
      var secondTop = sel && sel.rangeCount
        ? Math.round(sel.anchorNode.parentElement.getBoundingClientRect().top) : null;
      document.title = JSON.stringify({
        closed: closed,
        firstTop: firstTop,
        secondTop: secondTop
      });
    }, 400);
  }, 900);
});
</script>""",
    )
    assert result["closed"] is True, result
    assert result["secondTop"] > result["firstTop"], result


def test_render_vim_single_key_navigation(tmp_path):
    ui = _make_project(tmp_path, "# Single Keys\n\nbody\n")
    result = _run_browser(
        ui / "a.html",
        """<script>
window.addEventListener('load', function () {
  setTimeout(function () {
    document.getElementById('nm-source-vim').click();
    document.querySelector('.nm-tab[data-tab="render"]').click();
    setTimeout(function () {
      function press(key) {
        document.dispatchEvent(new KeyboardEvent('keydown', {
          key: key, bubbles: true, cancelable: true
        }));
      }
      function activeTab() {
        var t = document.querySelector('.nm-tab.active');
        return t ? t.getAttribute('data-tab') : null;
      }
      var out = {};
      press('2'); out.two = activeTab();
      document.querySelector('.nm-tab[data-tab="render"]').click();
      press('3'); out.three = activeTab();
      document.querySelector('.nm-tab[data-tab="render"]').click();
      press('c'); out.c = activeTab();
      document.querySelector('.nm-tab[data-tab="render"]').click();
      press('d'); out.d = document.getElementById('nm-sidebar-view-toc').classList.contains('active');
      press('f'); out.f = document.getElementById('nm-sidebar-view-results').classList.contains('active');
      press('s'); out.s = document.body.classList.contains('nm-split');
      document.title = JSON.stringify(out);
    }, 350);
  }, 900);
});
</script>""",
    )
    assert result["two"] == "source", result
    assert result["three"] == "warnings", result
    assert result["c"] == "source", result
    assert result["d"] is True
    assert result["f"] is True
    assert result["s"] is True
