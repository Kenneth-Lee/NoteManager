/* One toolbar entry for the object under the cursor: table, formula, or new object. */
(function () {
  'use strict';

  function readConfig() {
    var el = document.getElementById('nm-page-meta');
    if (!el) return null;
    try {
      var cfg = JSON.parse(el.textContent || '{}');
      return cfg && cfg.editable ? cfg : null;
    } catch (e) {
      return null;
    }
  }

  function setStatus(text, isError) {
    var el = document.getElementById('nm-source-status');
    if (!el) return;
    el.textContent = text || '';
    el.style.color = isError ? '#f88' : '#98c379';
  }

  function clickButton(id) {
    var button = document.getElementById(id);
    if (!button) {
      setStatus('当前编辑器缺少对应按钮: ' + id, true);
      return;
    }
    button.click();
  }

  function tableExists(cfg, cm) {
    return fetch('/api/table_parse', {
      method: 'POST',
      headers: {'Content-Type': 'application/json; charset=utf-8'},
      body: JSON.stringify({
        content: cm.getValue(),
        line: cm.getCursor().line,
        ext: cfg.source_ext || ''
      })
    }).then(function (response) {
      return response.json();
    }).then(function (data) {
      return !!(data && data.ok);
    }).catch(function () {
      return false;
    });
  }

  function showCreateChooser() {
    var overlay = document.createElement('div');
    overlay.className = 'nm-note-overlay';
    overlay.innerHTML =
      '<div class="nm-note-dialog" role="dialog" aria-label="新建对象">' +
        '<div class="nm-note-head"><h2>新建对象</h2>' +
          '<button type="button" class="nm-note-close" aria-label="关闭">×</button></div>' +
        '<p class="nm-import-hint">光标处没有识别到可编辑的表格或公式，请选择新建类型。</p>' +
        '<div class="nm-object-create-choices">' +
          '<button type="button" class="nm-object-create" data-object="table">' +
            '<strong>新建表格</strong><span>两行两列，可增删行列</span></button>' +
          '<button type="button" class="nm-object-create" data-object="math">' +
            '<strong>新建公式</strong><span>行内或行间，KaTeX 实时预览</span></button>' +
          '<button type="button" class="nm-object-create" data-object="mermaid">' +
            '<strong>新建 Mermaid 图</strong><span>流程图、时序图、类图等模板</span></button>' +
        '</div>' +
        '<div class="nm-note-actions"><button type="button" class="nm-note-cancel">取消</button></div>' +
      '</div>';
    document.body.appendChild(overlay);
    document.body.classList.add('nm-note-open');
    function close() {
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      document.body.classList.remove('nm-note-open');
    }
    overlay.querySelector('.nm-note-close').addEventListener('click', close);
    overlay.querySelector('.nm-note-cancel').addEventListener('click', close);
    overlay.addEventListener('mousedown', function (event) {
      if (event.target === overlay) close();
    });
    overlay.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') close();
    });
    overlay.querySelectorAll('[data-object]').forEach(function (button) {
      button.addEventListener('click', function () {
        var kind = button.getAttribute('data-object');
        close();
        var target = kind === 'table' ? 'nm-source-table'
          : (kind === 'mermaid' ? 'nm-source-mermaid' : 'nm-source-math');
        clickButton(target);
      });
    });
    var first = overlay.querySelector('[data-object]');
    if (first) first.focus();
  }

  function openObjectEditor() {
    var cfg = readConfig();
    var cm = window.__nmCM;
    if (!cfg || !cm) {
      setStatus('对象编辑器仅在可写的源文件页签可用', true);
      return;
    }
    var mermaid = window.nmMermaidEditor &&
      window.nmMermaidEditor.findBlock(cm, cfg.source_ext || '');
    if (mermaid) {
      setStatus('');
      clickButton('nm-source-mermaid');
      return;
    }
    setStatus('识别光标处的表格或公式…');
    tableExists(cfg, cm).then(function (isTable) {
      if (isTable) {
        setStatus('');
        clickButton('nm-source-table');
        return;
      }
      var formula = window.nmMathEditor &&
        window.nmMathEditor.findFormula(cm, cfg.source_ext || '');
      setStatus('');
      if (formula) {
        clickButton('nm-source-math');
      } else {
        showCreateChooser();
      }
    });
  }

  function ensureButton() {
    var tableBtn = document.getElementById('nm-source-table');
    var mathBtn = document.getElementById('nm-source-math');
    var mermaidBtn = document.getElementById('nm-source-mermaid');
    if (tableBtn) tableBtn.style.display = 'none';
    if (mathBtn) mathBtn.style.display = 'none';
    if (mermaidBtn) mermaidBtn.style.display = 'none';
    if (document.getElementById('nm-source-object')) return;
    var toolbar = document.querySelector('.nm-source-toolbar');
    var anchor = document.getElementById('nm-source-reference') ||
      document.getElementById('nm-source-save');
    if (!toolbar || !anchor) return;
    var button = document.createElement('button');
    button.type = 'button';
    button.id = 'nm-source-object';
    button.className = 'nm-source-secondary';
    button.textContent = '编辑/插入对象';
    button.title = '自动识别光标处的 Mermaid/表格/公式；都不是则选择新建';
    anchor.insertAdjacentElement('afterend', button);
    button.addEventListener('click', openObjectEditor);
  }

  document.addEventListener('DOMContentLoaded', ensureButton);
})();
