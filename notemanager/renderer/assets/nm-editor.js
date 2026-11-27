/* NoteManager: in-browser source editor (CodeMirror 5) + save + paste image */
(function () {
  var VIM_LS = 'nm-editor-vim';

  function readVimPref() {
    try {
      return window.localStorage.getItem(VIM_LS) === '1';
    } catch (e) {
      return false;
    }
  }

  function writeVimPref(on) {
    try {
      if (on) window.localStorage.setItem(VIM_LS, '1');
      else window.localStorage.removeItem(VIM_LS);
    } catch (e) {}
  }

  /** 旧版缓存的整页 HTML 可能没有这两个按钮；注入后只需更新 _static/nm-editor.js 即可生效 */
  var NM_BTN_STYLE =
    'padding:6px 12px;border-radius:4px;cursor:pointer;font-size:13px;font-family:inherit;';
  var NM_NEW_STYLE =
    NM_BTN_STYLE + 'background:#3d4451;color:#e0e0e0;border:1px solid #5c6370;';
  var NM_DEL_STYLE =
    NM_BTN_STYLE +
    'background:#3d4451;color:#e5a3a3;border:1px solid #8b4d4d;margin-left:0;';

  function ensureNoteToolbarButtons() {
    var tb = document.querySelector('.nm-source-toolbar');
    if (!tb) return;
    var save = document.getElementById('nm-source-save');
    if (!save) return;
    var after = save;
    function add(id, title, text, style) {
      if (document.getElementById(id)) {
        after = document.getElementById(id);
        return;
      }
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.id = id;
      btn.className = 'nm-source-secondary';
      btn.title = title;
      btn.textContent = text;
      btn.style.cssText = style || NM_NEW_STYLE;
      after.insertAdjacentElement('afterend', btn);
      after = btn;
    }
    add('nm-source-new', '在与当前笔记相同的目录下新建文件', '新建笔记', NM_NEW_STYLE);
    add('nm-source-paste-image', '把剪贴板中的图片粘贴到当前笔记目录的 _static', '粘贴图片', NM_NEW_STYLE);
    add('nm-source-import', '把本地文件或 URL 导入当前笔记目录的 _static', '导入资源', NM_NEW_STYLE);
    add('nm-source-reference', '从工程已有资源中选择并插入当前笔记', '引用资源', NM_NEW_STYLE);
    add('nm-source-delete', '删除磁盘上的当前源文件', '删除当前笔记', NM_DEL_STYLE);
  }

  function projectFromPath() {
    var m = window.location.pathname.match(/^\/p\/(\d+)\//);
    return m ? parseInt(m[1], 10) : -1;
  }

  function getSourceJumpParams() {
    try {
      var u = new URL(window.location.href);
      var src = (u.searchParams.get('src') || '').trim();
      var ln = parseInt(u.searchParams.get('line') || '0', 10);
      if (!src) return null;
      return { src: src.replace(/\\/g, '/'), line: ln > 0 ? ln : 1, url: u };
    } catch (e) {
      return null;
    }
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

    var vimCb = document.getElementById('nm-source-vim');
    function vimKeyMapAvailable() {
      return !!(CodeMirror.keyMap && CodeMirror.keyMap.vim);
    }
    function applyVimKeyMap(useVim) {
      var on = !!(useVim && vimKeyMapAvailable());
      cm.setOption('keyMap', on ? 'vim' : 'default');
      if (vimCb) vimCb.checked = on;
      writeVimPref(on);
    }
    applyVimKeyMap(readVimPref());
    if (vimCb) {
      vimCb.addEventListener('change', function () {
        applyVimKeyMap(vimCb.checked);
      });
    }

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
    function sourcePanelVisible() {
      if (!sourcePanel) return false;
      return (
        sourcePanel.classList.contains('active') ||
        (document.body.classList && document.body.classList.contains('nm-split'))
      );
    }

    var tabsLeft = document.querySelector('.nm-tabs-left');
    if (tabsLeft) {
      tabsLeft.addEventListener(
        'click',
        function (e) {
          var tab = e.target.closest && e.target.closest('.nm-tab');
          if (!tab) return;
          var name = tab.getAttribute('data-tab');
          if (!name || name === 'source') return;
          if (!sourcePanelVisible()) return;
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
      if (!sourcePanelVisible()) return;
      scheduleRefresh(window.__nmCM);
    });

    document.addEventListener('nm-layout-change', function () {
      if (!window.__nmCM) return;
      if (sourcePanelVisible()) scheduleRefresh(window.__nmCM);
    });

    var saveBtn = document.getElementById('nm-source-save');
    ensureNoteToolbarButtons();
    var statusEl = document.getElementById('nm-source-status');

    function setStatus(msg, isErr) {
      if (!statusEl) return;
      statusEl.textContent = msg || '';
      statusEl.style.color = isErr ? '#f88' : '#98c379';
    }

    function saveSource(options) {
      options = options || {};
      if (options.stay) window.__nmSuppressBuildReload = true;
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
            return r.text().then(function (t) {
              throw new Error(t || 'HTTP ' + r.status);
            });
          }
          return r.json();
        })
        .then(function (d) {
          if (d.ok) {
            window.__nmInitialSource = cm.getValue();
            window.__nmDirty = false;
            if (options.stay) {
              setStatus('已保存');
              try { cm.focus(); } catch (e) {}
              // update_file emits build_complete while the request is in
              // flight; keep the source tab open instead of reloading it.
              setTimeout(function () {
                window.__nmSuppressBuildReload = false;
              }, 3000);
              return;
            }
            window.__nmSuppressBuildReload = false;
            if (options.quit && window.nmExitSplit) window.nmExitSplit();
            switchToRenderTab();
            setStatus('已保存，正在刷新…');
            window.location.reload();
          } else {
            window.__nmSuppressBuildReload = false;
            setStatus(d.error || '保存失败', true);
          }
        })
        .catch(function (err) {
          window.__nmSuppressBuildReload = false;
          setStatus(err && err.message ? err.message : '网络错误', true);
        });
    }

    if (saveBtn) {
      saveBtn.addEventListener('click', function () {
        saveSource();
      });
    }

    var importOverlay = null;

    function insertAssetResult(d) {
      var line = ext === '.rst' ? d.insert_rst : d.insert_markdown;
      if (!line) {
        setStatus('服务端没有返回插入片段', true);
        return false;
      }
      cm.replaceSelection(line + '\n');
      recomputeDirty();
      setStatus('已插入资源引用，请保存并刷新渲染');
      return true;
    }

    function uploadPasteFile(file) {
      var fd = new FormData();
      fd.append('project', String(project));
      fd.append('source_rel_path', cfg.source_rel);
      fd.append('file', file, file.name || 'paste.png');
      setStatus('上传图片…');
      fetch('/api/paste_image', { method: 'POST', body: fd })
        .then(function (r) {
          return r.json().then(function (d) {
            if (!r.ok || !d.ok) throw new Error(d.error || ('HTTP ' + r.status));
            return d;
          });
        })
        .then(function (d) {
          if (insertAssetResult(d)) setStatus('图片已插入，请保存并刷新渲染');
        })
        .catch(function (err) {
          setStatus(err && err.message ? err.message : '上传图片失败', true);
        });
    }

    function pasteImageFromClipboard() {
      if (!navigator.clipboard || typeof navigator.clipboard.read !== 'function') {
        setStatus('浏览器不支持从剪贴板读取，请在编辑区按 Ctrl+V', true);
        return;
      }
      setStatus('读取剪贴板…');
      navigator.clipboard.read().then(function (items) {
        for (var i = 0; i < items.length; i++) {
          var types = items[i].types || [];
          for (var j = 0; j < types.length; j++) {
            if (types[j].indexOf('image/') !== 0) continue;
            return items[i].getType(types[j]).then(function (blob) {
              var f = new File([blob], 'paste' + (blob.type === 'image/jpeg' ? '.jpg' : '.png'), {
                type: blob.type,
              });
              uploadPasteFile(f);
            });
          }
        }
        setStatus('剪贴板里没有图片', true);
      }).catch(function (err) {
        setStatus('无法读取剪贴板，请在编辑区按 Ctrl+V 或使用「导入资源」', true);
      });
    }

    function ensureImportDialog() {
      if (importOverlay) return;
      importOverlay = document.createElement('div');
      importOverlay.className = 'nm-note-overlay';
      importOverlay.hidden = true;
      importOverlay.innerHTML = '<div class="nm-note-dialog" role="dialog" aria-label="导入资源">' +
        '<div class="nm-note-head"><h2>导入资源</h2>' +
        '<button type="button" class="nm-note-close" aria-label="关闭">×</button></div>' +
        '<p class="nm-import-hint">资源会保存到当前笔记目录的 <code>_static/</code>，并插入引用。</p>' +
        '<div class="nm-note-field"><label>本地文件</label>' +
        '<input type="file" id="nm-import-file"></div>' +
        '<div class="nm-note-field"><label for="nm-import-url">或资源 URL</label>' +
        '<input type="url" id="nm-import-url" placeholder="https://example.com/image.png" autocomplete="off"></div>' +
        '<div class="nm-note-error" id="nm-import-error" hidden></div>' +
        '<div class="nm-note-actions"><button type="button" class="nm-note-cancel">取消</button>' +
        '<button type="button" class="nm-note-primary" id="nm-import-url-btn">导入 URL</button></div>' +
        '</div>';
      document.body.appendChild(importOverlay);
      importOverlay.addEventListener('mousedown', function (event) {
        if (event.target === importOverlay) closeImportDialog();
      });
      importOverlay.querySelector('.nm-note-close').addEventListener('click', closeImportDialog);
      importOverlay.querySelector('.nm-note-cancel').addEventListener('click', closeImportDialog);
      importOverlay.querySelector('#nm-import-file').addEventListener('change', function () {
        if (this.files && this.files[0]) uploadImportFile(this.files[0]);
      });
      importOverlay.querySelector('#nm-import-url-btn').addEventListener('click', importFromUrl);
      importOverlay.querySelector('#nm-import-url').addEventListener('keydown', function (event) {
        if (event.key === 'Enter') {
          event.preventDefault();
          importFromUrl();
        }
      });
    }

    function setImportError(text) {
      var err = importOverlay && importOverlay.querySelector('#nm-import-error');
      if (!err) return;
      err.textContent = text || '';
      err.hidden = !text;
    }

    function closeImportDialog() {
      if (!importOverlay) return;
      importOverlay.hidden = true;
      document.body.classList.remove('nm-note-open');
    }

    function openImportDialog() {
      ensureImportDialog();
      importOverlay.hidden = false;
      document.body.classList.add('nm-note-open');
      setImportError('');
      var file = importOverlay.querySelector('#nm-import-file');
      if (file) file.value = '';
      var url = importOverlay.querySelector('#nm-import-url');
      if (url) url.value = '';
      setTimeout(function () { if (url) url.focus(); }, 0);
    }

    var referenceOverlay = null;
    var referenceItems = [];

    function refEncodePath(path) {
      return String(path || '').split('/').map(encodeURIComponent).join('/');
    }

    function refFormatSize(size) {
      size = Number(size) || 0;
      if (size < 1024) return size + ' B';
      if (size < 1024 * 1024) return (size / 1024).toFixed(1) + ' KB';
      return (size / 1024 / 1024).toFixed(1) + ' MB';
    }

    function referenceRelative(sourceRel, assetRel) {
      var from = String(sourceRel || '').split('/').slice(0, -1);
      var to = String(assetRel || '').split('/');
      var common = 0;
      while (common < from.length && common < to.length && from[common] === to[common]) common++;
      var out = [];
      for (var i = common; i < from.length; i++) out.push('..');
      return out.concat(to.slice(common)).join('/');
    }

    function referenceScope(item) {
      var scope = referenceOverlay.querySelector('#nm-reference-scope').value;
      var source = String(cfg.source_rel || '').replace(/\\/g, '/');
      var dir = source.indexOf('/') >= 0 ? source.slice(0, source.lastIndexOf('/')) : '';
      var staticPrefix = (dir ? dir + '/' : '') + '_static/';
      if (scope === 'static') return item.path.indexOf(staticPrefix) === 0;
      if (scope === 'subtree') {
        if (!dir) return true;
        return item.path === dir || item.path.indexOf(dir + '/') === 0;
      }
      if (scope === 'orphan') return !Number(item.reference_count || 0);
      return true;
    }

    function referenceGlobMatch(path, query) {
      if (!query) return true;
      if (!/[*?[{]/.test(query)) {
        return path.toLowerCase().indexOf(query.toLowerCase()) >= 0;
      }
      var pattern = query.replace(/\\/g, '/');
      var out = '^';
      function escapeChar(ch) {
        return /[.+^${}()|[\]\\]/.test(ch) ? '\\' + ch : ch;
      }
      for (var i = 0; i < pattern.length; i++) {
        var ch = pattern[i];
        if (ch === '*') {
          out += '.*';
        } else if (ch === '?') {
          out += '.';
        } else if (ch === '[') {
          var close = pattern.indexOf(']', i + 1);
          if (close < 0) {
            out += '\\[';
          } else {
            var body = pattern.slice(i + 1, close);
            if (body.indexOf('|') >= 0) {
              out += '(?:' + body.split('|').map(function (part) {
                return part.split('').map(escapeChar).join('');
              }).join('|') + ')';
            } else {
              out += '[' + body.replace(/\\/g, '\\\\') + ']';
            }
            i = close;
          }
        } else if (ch === '{') {
          var braceClose = pattern.indexOf('}', i + 1);
          if (braceClose < 0) {
            out += '\\{';
          } else {
            out += '(?:' + pattern.slice(i + 1, braceClose).split(',').map(function (part) {
              return part.split('').map(escapeChar).join('');
            }).join('|') + ')';
            i = braceClose;
          }
        } else {
          out += escapeChar(ch);
        }
      }
      out += '$';
      try {
        return new RegExp(out, 'i').test(path);
      } catch (e) {
        return path.toLowerCase().indexOf(query.toLowerCase()) >= 0;
      }
    }

    function renderReferenceList() {
      if (!referenceOverlay) return;
      var query = String(referenceOverlay.querySelector('#nm-reference-search').value || '')
        .trim().toLowerCase();
      var list = referenceOverlay.querySelector('#nm-reference-list');
      var summary = referenceOverlay.querySelector('#nm-reference-summary');
      var insert = referenceOverlay.querySelector('#nm-reference-insert');
      list.textContent = '';
      var visible = referenceItems.filter(function (item) {
        return referenceScope(item) && referenceGlobMatch(item.path, query);
      });
      if (!visible.length) {
        list.textContent = '没有匹配的资源。';
        list.className = 'nm-reference-list nm-assets-empty';
      } else {
        list.className = 'nm-reference-list';
      }
      visible.forEach(function (item) {
        var row = document.createElement('label');
        row.className = 'nm-asset-item';
        var cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'nm-reference-check';
        cb.value = item.path;
        var img = document.createElement('img');
        img.loading = 'lazy';
        img.alt = '';
        img.src = '/p/' + project + '/' + refEncodePath(item.path);
        img.addEventListener('error', function () { img.style.display = 'none'; });
        var main = document.createElement('div');
        main.className = 'nm-asset-main';
        var path = document.createElement('div');
        path.className = 'nm-asset-path';
        path.textContent = item.path;
        var meta = document.createElement('div');
        meta.className = 'nm-asset-size';
        var refs = Number(item.reference_count || 0);
        meta.textContent = (refs ? '引用 ' + refs + ' 篇' : '未引用') +
          ' · ' + refFormatSize(item.size || 0);
        main.appendChild(path);
        main.appendChild(meta);
        row.appendChild(cb);
        row.appendChild(img);
        row.appendChild(main);
        list.appendChild(row);
      });
      var n = list.querySelectorAll('.nm-reference-check').length;
      summary.textContent = '可选 ' + n + ' 张 · 当前目录 _static 优先';
      insert.disabled = n === 0;
    }

    function closeReferenceDialog() {
      if (!referenceOverlay) return;
      referenceOverlay.hidden = true;
      document.body.classList.remove('nm-note-open');
    }

    function openReferenceDialog() {
      if (ext !== '.md' && ext !== '.rst') {
        setStatus('只有 Markdown/reST 笔记可以插入图片引用', true);
        return;
      }
      if (!referenceOverlay) {
        referenceOverlay = document.createElement('div');
        referenceOverlay.className = 'nm-note-overlay';
        referenceOverlay.hidden = true;
        referenceOverlay.innerHTML = '<div class="nm-note-dialog nm-reference-dialog" role="dialog" aria-label="引用资源">' +
          '<div class="nm-note-head"><h2>引用资源</h2>' +
          '<button type="button" class="nm-note-close" aria-label="关闭">×</button></div>' +
          '<p class="nm-import-hint">从工程已有资源中选择；插入后会变成当前笔记的图片引用。</p>' +
          '<div class="nm-reference-filters">' +
          '<select id="nm-reference-scope">' +
          '<option value="static">当前目录 _static</option>' +
          '<option value="subtree">当前目录及子目录</option>' +
          '<option value="orphan">未引用资源</option>' +
          '<option value="all">全部资源</option>' +
          '</select>' +
          '<input id="nm-reference-search" type="search" placeholder="过滤路径（支持 *.png / *.[jpg|png] / *.{jpg,png}）" autocomplete="off">' +
          '</div>' +
          '<div class="nm-assets-summary" id="nm-reference-summary"></div>' +
          '<div class="nm-reference-list" id="nm-reference-list">正在加载…</div>' +
          '<div class="nm-note-error" id="nm-reference-error" hidden></div>' +
          '<div class="nm-note-actions"><button type="button" class="nm-note-cancel">取消</button>' +
          '<button type="button" class="nm-note-primary" id="nm-reference-insert" disabled>确定（插入引用）</button></div>' +
          '</div>';
        document.body.appendChild(referenceOverlay);
        referenceOverlay.addEventListener('mousedown', function (event) {
          if (event.target === referenceOverlay) closeReferenceDialog();
        });
        referenceOverlay.querySelector('.nm-note-close').addEventListener('click', closeReferenceDialog);
        referenceOverlay.querySelector('.nm-note-cancel').addEventListener('click', closeReferenceDialog);
        referenceOverlay.querySelector('#nm-reference-scope').addEventListener('change', renderReferenceList);
        referenceOverlay.querySelector('#nm-reference-search').addEventListener('input', renderReferenceList);
        referenceOverlay.querySelector('#nm-reference-insert').addEventListener('click', insertReferenceSelection);
      }
      referenceOverlay.hidden = false;
      document.body.classList.add('nm-note-open');
      var list = referenceOverlay.querySelector('#nm-reference-list');
      var err = referenceOverlay.querySelector('#nm-reference-error');
      err.hidden = true;
      list.textContent = '正在加载…';
      fetch('/api/list_assets', {
        method: 'POST',
        headers: {'Content-Type': 'application/json; charset=utf-8'},
        body: JSON.stringify({project: project})
      }).then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok || !d.ok) throw new Error(d.error || ('HTTP ' + r.status));
          return d;
        });
      }).then(function (data) {
        referenceItems = (data.items || []).slice();
        renderReferenceList();
      }).catch(function (e) {
        list.textContent = '无法加载资源。';
        err.textContent = e && e.message ? e.message : '加载失败';
        err.hidden = false;
      });
    }

    function insertReferenceSelection() {
      if (!referenceOverlay) return;
      var checked = referenceOverlay.querySelectorAll('.nm-reference-check:checked');
      if (!checked.length) return;
      var blocks = [];
      checked.forEach(function (cb) {
        var rel = referenceRelative(cfg.source_rel, cb.value);
        if (ext === '.rst') blocks.push('.. image:: ' + rel);
        else blocks.push('![' + (cb.value.split('/').pop() || '') + '](' + rel + ')');
      });
      cm.replaceSelection(blocks.join('\n\n') + '\n');
      recomputeDirty();
      closeReferenceDialog();
      setStatus('已插入 ' + blocks.length + ' 条图片引用，请保存并刷新渲染');
    }

    function finishImport(d) {
      if (!d.ok) {
        setImportError(d.error || '导入失败');
        return;
      }
      if (!insertAssetResult(d)) {
        setImportError('服务端没有返回插入片段');
        return;
      }
      closeImportDialog();
    }

    function uploadImportFile(file) {
      if (!importOverlay) return;
      setImportError('');
      var fd = new FormData();
      fd.append('project', String(project));
      fd.append('source_rel_path', cfg.source_rel);
      fd.append('file', file, file.name || 'resource');
      setStatus('导入资源…');
      fetch('/api/import_asset', { method: 'POST', body: fd })
        .then(function (r) {
          return r.json().then(function (d) {
            if (!r.ok || !d.ok) throw new Error(d.error || ('HTTP ' + r.status));
            return d;
          });
        })
        .then(finishImport)
        .catch(function (err) {
          setImportError(err && err.message ? err.message : '导入失败');
        });
    }

    function importFromUrl() {
      var urlInput = importOverlay && importOverlay.querySelector('#nm-import-url');
      if (!urlInput) return;
      var url = String(urlInput.value || '').trim();
      if (!url) {
        setImportError('请输入资源 URL');
        urlInput.focus();
        return;
      }
      setImportError('');
      var btn = importOverlay.querySelector('#nm-import-url-btn');
      btn.disabled = true;
      setStatus('下载资源…');
      fetch('/api/import_url', {
        method: 'POST',
        headers: {'Content-Type': 'application/json; charset=utf-8'},
        body: JSON.stringify({
          project: project,
          source_rel_path: cfg.source_rel,
          url: url,
        }),
      })
        .then(function (r) {
          return r.json().then(function (d) {
            if (!r.ok || !d.ok) throw new Error(d.error || ('HTTP ' + r.status));
            return d;
          });
        })
        .then(finishImport)
        .catch(function (err) {
          setImportError(err && err.message ? err.message : '导入 URL 失败');
        })
        .finally(function () { btn.disabled = false; });
    }

    var pasteImageBtn = document.getElementById('nm-source-paste-image');
    if (pasteImageBtn) {
      pasteImageBtn.addEventListener('click', pasteImageFromClipboard);
    }
    var importBtn = document.getElementById('nm-source-import');
    if (importBtn) {
      importBtn.addEventListener('click', openImportDialog);
    }
    var referenceBtn = document.getElementById('nm-source-reference');
    if (referenceBtn) {
      referenceBtn.addEventListener('click', openReferenceDialog);
    }

    function switchToRenderTab() {
      document.querySelectorAll('.nm-tab').forEach(function (t) {
        t.classList.remove('active');
      });
      document.querySelectorAll('.nm-tab-panel').forEach(function (p) {
        p.classList.remove('active');
      });
      var renderTab = document.querySelector('.nm-tab[data-tab="render"]');
      var renderPanel = document.getElementById('nm-panel-render');
      if (renderTab) renderTab.classList.add('active');
      if (renderPanel) renderPanel.classList.add('active');
    }

    function quitToRender() {
      if (window.__nmDirty) {
        if (
          !window.confirm(
            '源文件有未保存的修改，切换到「渲染」将丢弃这些修改。\n\n点「取消」留在源文件；请先 :w 或点「保存并刷新渲染」。'
          )
        ) {
          return;
        }
      }
      if (window.nmExitSplit) window.nmExitSplit();
      switchToRenderTab();
    }

    if (CodeMirror.Vim && typeof CodeMirror.Vim.defineEx === 'function') {
      CodeMirror.commands.save = function () {
        saveSource({stay: true});
      };
      CodeMirror.Vim.defineEx('quit', 'q', function () {
        quitToRender();
      });
      CodeMirror.Vim.defineEx('wq', 'wq', function () {
        saveSource({quit: true});
      });
      CodeMirror.Vim.defineEx('x', 'x', function () {
        saveSource({quit: true});
      });
    }

    var jump = getSourceJumpParams();
    if (jump && jump.src === String(cfg.source_rel || '').replace(/\\/g, '/')) {
      setTimeout(function () {
        var line0 = Math.max(0, jump.line - 1);
        cm.focus();
        cm.setCursor({ line: line0, ch: 0 });
        cm.scrollIntoView({ line: line0, ch: 0 }, 120);
        var srcTab = document.querySelector('.nm-tab[data-tab="source"]');
        if (srcTab && typeof srcTab.click === 'function') srcTab.click();
        try {
          jump.url.searchParams.delete('src');
          jump.url.searchParams.delete('line');
          window.history.replaceState({}, '', jump.url.pathname + (jump.url.search ? jump.url.search : '') + jump.url.hash);
        } catch (e) {}
      }, 20);
    }

    function encodeHtmlRel(rel) {
      if (!rel) return '';
      return rel.split('/').map(function (seg) {
        return encodeURIComponent(seg);
      }).join('/');
    }

    var newBtn = document.getElementById('nm-source-new');
    var delBtn = document.getElementById('nm-source-delete');

    function warnLoseUnsaved(action) {
      if (!window.__nmDirty) return true;
      return window.confirm(
        '当前笔记有未保存的修改；继续「' +
          action +
          '」将放弃这些修改（不会写入当前文件）。\n\n确定继续吗？'
      );
    }

    if (newBtn) {
      newBtn.addEventListener('click', function () {
        if (!warnLoseUnsaved('新建笔记')) return;
        var sugg = 'untitled' + ext;
        var name = window.prompt(
          '请输入新文件名（与当前笔记在同一目录）。\n使用 .md 或 .rst；不写后缀则与当前笔记类型相同。',
          sugg
        );
        if (name === null) return;
        name = (name || '').trim();
        if (!name) {
          setStatus('未输入文件名', true);
          return;
        }
        setStatus('创建中…');
        window.__nmSuppressBuildReload = true;
        fetch('/api/create_note', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json; charset=utf-8' },
          body: JSON.stringify({
            project: project,
            source_rel_path: cfg.source_rel,
            filename: name,
            source_ext: ext,
          }),
        })
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
              setStatus(d.error || '创建失败', true);
              return;
            }
            var hr = d.html_rel_path ||
              String(d.source_rel_path || '').replace(/\.(md|rst|txt)$/i, '.html');
            var target = d.open_url || (hr ? '/p/' + project + '/' + encodeHtmlRel(hr) : '');
            if (!target) {
              window.__nmSuppressBuildReload = false;
              setStatus('创建成功，但没有返回可打开的地址', true);
              return;
            }
            window.location.assign(target);
          })
          .catch(function (err) {
            window.__nmSuppressBuildReload = false;
            setStatus(err && err.message ? err.message : '网络错误', true);
          });
      });
    }

    if (delBtn) {
      delBtn.addEventListener('click', function () {
        var msg =
          '确定删除当前笔记吗？源文件将移到工程回收站（.NoteManager/trash），生成的 HTML 会从站点移除。';
        if (window.__nmDirty) {
          msg = '当前有未保存的修改（删除后不会保存到磁盘）。\n\n' + msg;
        }
        if (!window.confirm(msg)) return;
        setStatus('删除中…');
        window.__nmSuppressBuildReload = true;
        fetch('/api/delete_note', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json; charset=utf-8' },
          body: JSON.stringify({
            project: project,
            source_rel_path: cfg.source_rel,
          }),
        })
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
              setStatus(d.error || '删除失败', true);
              return;
            }
            var red = d.redirect_html || 'index.html';
            window.location.href = '/p/' + project + '/' + encodeHtmlRel(red);
          })
          .catch(function (err) {
            window.__nmSuppressBuildReload = false;
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
        uploadPasteFile(f);
        return;
      }
    });
  });
})();

