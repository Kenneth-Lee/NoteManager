/* NoteManager: in-browser source editor (CodeMirror 5) + save + paste image */
(function () {
  function projectFromPath() {
    var m = window.location.pathname.match(/^\/p\/(\d+)\//);
    return m ? parseInt(m[1], 10) : -1;
  }

  /** 仅 nmrd Web 以 /p/<工程号>/ 提供页面时可编辑；拷贝出去的 html 目录走只读降级 */
  function isNmWebUiPath() {
    return /^\/p\/\d+\//.test(window.location.pathname || '');
  }

  function scheduleRefresh(cm) {
    if (!cm) return;
    var h = Math.max(360, window.innerHeight - 220) + 'px';
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        cm.setSize(null, h);
        cm.refresh();
        cm.setSize(null, h);
        cm.refresh();
      });
    });
  }

  function mountReadonlyFallback(mount, initial) {
    var tb = document.querySelector('.nm-source-toolbar');
    if (tb) tb.style.display = 'none';
    var hint = document.createElement('div');
    hint.className = 'nm-source-readonly-hint';
    hint.textContent =
      '当前页面不是通过 NoteManager 控制台打开（或无法访问 /p/… 路径），源文件以只读方式显示；编辑与保存需在本机使用 Web 控制台浏览工程。';
    mount.appendChild(hint);
    var pre = document.createElement('pre');
    pre.className = 'nm-source-static-fallback';
    pre.textContent = initial;
    mount.appendChild(pre);
  }

  document.addEventListener('DOMContentLoaded', function () {
    var metaEl = document.getElementById('nm-page-meta');
    if (!metaEl) return;
    var cfg;
    try {
      cfg = JSON.parse(metaEl.textContent);
    } catch (e) {
      return;
    }
    if (!cfg.editable) return;

    var srcEl = document.getElementById('nm-source-json');
    if (!srcEl) return;
    var initial = '';
    try {
      initial = JSON.parse(srcEl.textContent);
    } catch (e2) {
      initial = '';
    }
    if (typeof initial !== 'string') initial = '';

    var mount = document.getElementById('nm-editor-mount');
    if (!mount) return;

    var webUi = isNmWebUiPath();
    var hasCM = typeof CodeMirror !== 'undefined';
    if (!webUi || !hasCM) {
      mountReadonlyFallback(mount, initial);
      return;
    }

    var project = projectFromPath();
    if (project < 0) {
      mountReadonlyFallback(mount, initial);
      return;
    }

    var preCode = document.querySelector('#nm-panel-source pre');
    if (preCode) preCode.style.display = 'none';

    var ta = document.createElement('textarea');
    ta.value = initial;
    mount.appendChild(ta);

    var ext = (cfg.source_ext || '').toLowerCase();
    var modeName = ext === '.md' ? 'markdown' : null;
    var cm = CodeMirror.fromTextArea(ta, {
      lineNumbers: true,
      mode: modeName,
      theme: 'dracula',
      indentUnit: 4,
      lineWrapping: true,
    });

    var editorHeight = Math.max(360, window.innerHeight - 220) + 'px';
    cm.setSize(null, editorHeight);

    window.__nmCM = cm;
    window.__nmInitialSource = initial;
    window.__nmDirty = false;

    function recomputeDirty() {
      window.__nmDirty = cm.getValue() !== window.__nmInitialSource;
    }

    cm.on('change', function () {
      recomputeDirty();
    });

    window.addEventListener('beforeunload', function (e) {
      if (!window.__nmDirty) return;
      e.preventDefault();
      e.returnValue = '';
    });

    var sourcePanel = document.getElementById('nm-panel-source');
    var tabsLeft = document.querySelector('.nm-tabs-left');
    if (tabsLeft) {
      tabsLeft.addEventListener(
        'click',
        function (e) {
          var tab = e.target.closest && e.target.closest('.nm-tab');
          if (!tab) return;
          var name = tab.getAttribute('data-tab');
          if (!name || name === 'source') return;
          if (!sourcePanel || !sourcePanel.classList.contains('active')) return;
          if (!window.__nmDirty) return;
          if (
            !window.confirm(
              '源文件有未保存的修改，切换到「' +
                (name === 'render' ? '渲染' : name === 'warnings' ? '诊断' : name) +
                '」将丢弃这些修改。\n\n点「取消」留在源文件；请先点「保存并刷新渲染」。'
            )
          ) {
            e.preventDefault();
            e.stopPropagation();
            e.stopImmediatePropagation();
          }
        },
        true
      );

      tabsLeft.addEventListener('click', function (e) {
        var tab = e.target.closest && e.target.closest('.nm-tab');
        if (!tab) return;
        if (tab.getAttribute('data-tab') !== 'source') return;
        scheduleRefresh(window.__nmCM);
      });
    }

    if (sourcePanel && window.IntersectionObserver) {
      var io = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (en) {
            if (en.target !== sourcePanel) return;
            if (en.intersectionRatio <= 0) return;
            scheduleRefresh(window.__nmCM);
          });
        },
        { threshold: [0, 0.01, 1] }
      );
      io.observe(sourcePanel);
    }

    window.addEventListener('resize', function () {
      if (!sourcePanel || !sourcePanel.classList.contains('active')) return;
      scheduleRefresh(window.__nmCM);
    });

    var saveBtn = document.getElementById('nm-source-save');
    var statusEl = document.getElementById('nm-source-status');

    function setStatus(msg, isErr) {
      if (!statusEl) return;
      statusEl.textContent = msg || '';
      statusEl.style.color = isErr ? '#f88' : '#98c379';
    }

    if (saveBtn) {
      saveBtn.addEventListener('click', function () {
        setStatus('保存中…');
        fetch('/api/save_source', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json; charset=utf-8' },
          body: JSON.stringify({
            project: project,
            source_rel_path: cfg.source_rel,
            content: cm.getValue(),
          }),
        })
          .then(function (r) {
            if (!r.ok) {
              return r
                .text()
                .then(function (t) {
                  throw new Error(t || 'HTTP ' + r.status);
                });
            }
            return r.json();
          })
          .then(function (d) {
            if (d.ok) {
              window.__nmInitialSource = cm.getValue();
              window.__nmDirty = false;
              setStatus('已保存，正在刷新…');
              window.location.reload();
            } else {
              setStatus(d.error || '保存失败', true);
            }
          })
          .catch(function (err) {
            setStatus(err && err.message ? err.message : '网络错误', true);
          });
      });
    }

    cm.on('paste', function (editor, e) {
      var items = e.clipboardData && e.clipboardData.items;
      if (!items) return;
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        if (!it.type || it.type.indexOf('image/') !== 0) continue;
        e.preventDefault();
        var f = it.getAsFile();
        if (!f) continue;
        var fd = new FormData();
        fd.append('project', String(project));
        fd.append('source_rel_path', cfg.source_rel);
        fd.append('file', f, f.name || 'paste.png');
        setStatus('上传截图…');
        fetch('/api/paste_image', { method: 'POST', body: fd })
          .then(function (r) {
            if (!r.ok) {
              return r.text().then(function (t) {
                throw new Error(t || 'HTTP ' + r.status);
              });
            }
            return r.json();
          })
          .then(function (d) {
            if (!d.ok) {
              setStatus(d.error || '上传失败', true);
              return;
            }
            var line = ext === '.rst' ? d.insert_rst : d.insert_markdown;
            if (!line) return;
            editor.replaceSelection(line + '\n');
            recomputeDirty();
            setStatus('已插入图片引用，请点击「保存并刷新渲染」');
          })
          .catch(function (err) {
            setStatus(err && err.message ? err.message : '上传失败', true);
          });
        return;
      }
    });
  });
})();
