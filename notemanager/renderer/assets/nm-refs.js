/* Dynamic keyword references and duplicate-label choices. */
(function () {
  'use strict';

  var prefix = window.NM_PREFIX || '';
  var indexPromise = null;

  function openChooser(link, candidates, message) {
    var overlay = document.createElement('div');
    overlay.className = 'nm-note-overlay';
    overlay.dataset.kind = 'ref-choice';
    overlay.innerHTML = '<div class="nm-note-dialog" role="dialog" aria-label="选择引用目标">' +
      '<div class="nm-note-head"><h2>选择引用目标</h2>' +
      '<button type="button" class="nm-note-close" aria-label="关闭">×</button></div>' +
      '<p class="nm-import-hint"></p>' +
      '<div class="nm-ref-choice-list"></div>' +
      '<div class="nm-note-actions"><button type="button" class="nm-note-cancel">取消</button></div>' +
      '</div>';
    overlay.querySelector('.nm-import-hint').textContent = message ||
      '这个标签在多个文档中存在，请选择要打开的目标。';
    document.body.appendChild(overlay);
    document.body.classList.add('nm-note-open');
    var list = overlay.querySelector('.nm-ref-choice-list');
    candidates.forEach(function (item) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'nm-ref-choice-btn';
      btn.textContent = item.label || item.path;
      btn.title = item.path;
      btn.addEventListener('click', function () {
        window.location.href = item.path;
      });
      list.appendChild(btn);
    });
    function close() {
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      document.body.classList.remove('nm-note-open');
    }
    overlay.querySelector('.nm-note-close').addEventListener('click', close);
    overlay.querySelector('.nm-note-cancel').addEventListener('click', close);
    overlay.addEventListener('mousedown', function (event) {
      if (event.target === overlay) close();
    });
  }

  function loadSearchIndex() {
    if (!indexPromise) {
      indexPromise = fetch(prefix + '_index/search_index.json').then(function (response) {
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.json();
      }).then(function (data) {
        return (data && data.documents) || [];
      });
    }
    return indexPromise;
  }

  function keywordCandidates(query) {
    var colon = query.indexOf(':');
    if (colon < 0) return Promise.resolve([]);
    var kind = query.slice(0, colon).toLowerCase();
    var term = query.slice(colon + 1).trim().toLowerCase();
    if (!term || (kind !== 'dtag' && kind !== 'index')) return Promise.resolve([]);
    return loadSearchIndex().then(function (docs) {
      var seen = Object.create(null);
      var out = [];
      docs.forEach(function (doc) {
        if (String(doc.type || '').toLowerCase() !== kind) return;
        var value = String(doc.title || doc.text || '').trim().toLowerCase();
        if (value !== term) return;
        var path = String(doc.path || '');
        if (!path || seen[path]) return;
        seen[path] = true;
        out.push({
          path: prefix + path,
          label: String(doc.title || doc.text || path) + ' — ' + path
        });
      });
      return out;
    });
  }

  document.addEventListener('click', function (event) {
    var link = event.target.closest &&
      event.target.closest('a[data-nm-ref-candidates], a[data-nm-keyword-ref]');
    if (!link) return;

    if (link.hasAttribute('data-nm-keyword-ref')) {
      event.preventDefault();
      var query = link.getAttribute('data-nm-keyword-ref') || '';
      keywordCandidates(query).then(function (candidates) {
        if (candidates.length === 1) {
          window.location.href = candidates[0].path;
        } else if (candidates.length > 1) {
          openChooser(link, candidates, '关键字 “' + query + '” 匹配多个文档，请选择要打开的目标。');
        } else {
          openChooser(link, [], '没有找到关键字 “' + query + '” 对应的文档。');
        }
      }).catch(function (err) {
        openChooser(link, [], '无法加载搜索索引：' + (err && err.message ? err.message : err));
      });
      return;
    }

    var raw = link.getAttribute('data-nm-ref-candidates');
    var candidates;
    try {
      candidates = JSON.parse(raw || '[]');
    } catch (e) {
      return;
    }
    if (!Array.isArray(candidates) || candidates.length < 2) return;
    event.preventDefault();
    openChooser(link, candidates);
  });
})();

/* Copyable source-path badge. */
(function () {
  'use strict';
  function copyText(value) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(value);
    }
    return new Promise(function (resolve, reject) {
      var ta = document.createElement('textarea');
      ta.value = value;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy');
        resolve();
      } catch (e) {
        reject(e);
      }
      document.body.removeChild(ta);
    });
  }

  document.querySelectorAll('[data-nm-copy]').forEach(function (el) {
    function copy() {
      var value = el.getAttribute('data-nm-copy') || '';
      if (!value) return;
      copyText(value).then(function () {
        el.classList.add('copied');
        el.textContent = '已复制: ' + value;
        setTimeout(function () {
          el.textContent = el.getAttribute('data-nm-copy-label') || value;
          el.classList.remove('copied');
        }, 900);
      }).catch(function () {});
    }
    el.addEventListener('click', copy);
    el.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        copy();
      }
    });
  });
})();

/* Keep wide tables readable without stretching small tables across the page. */
(function () {
  'use strict';
  document.querySelectorAll('.nm-tab-panel table').forEach(function (table) {
    if (table.closest('.nm-table-shell')) return;
    if (table.classList.contains('nm-table-edit-grid')) return;
    var shell = document.createElement('div');
    shell.className = 'nm-table-shell';
    table.parentNode.insertBefore(shell, table);
    shell.appendChild(table);
  });
})();
