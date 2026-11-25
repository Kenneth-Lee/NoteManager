/* Choose a target when a reST label has multiple definitions. */
(function () {
  'use strict';

  function openChooser(link, candidates) {
    var overlay = document.createElement('div');
    overlay.className = 'nm-note-overlay';
    overlay.dataset.kind = 'ref-choice';
    overlay.innerHTML = '<div class="nm-note-dialog" role="dialog" aria-label="选择引用目标">' +
      '<div class="nm-note-head"><h2>选择引用目标</h2>' +
      '<button type="button" class="nm-note-close" aria-label="关闭">×</button></div>' +
      '<p class="nm-import-hint">这个标签在多个文档中存在，请选择要打开的目标。</p>' +
      '<div class="nm-ref-choice-list"></div>' +
      '<div class="nm-note-actions"><button type="button" class="nm-note-cancel">取消</button></div>' +
      '</div>';
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

  document.addEventListener('click', function (event) {
    var link = event.target.closest && event.target.closest('a[data-nm-ref-candidates]');
    if (!link) return;
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
