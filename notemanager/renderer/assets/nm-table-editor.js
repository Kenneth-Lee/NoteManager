/* Graphical editor for Markdown pipe tables and reST grid/simple tables. */
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

  function api(path, payload) {
    return fetch(path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json; charset=utf-8'},
      body: JSON.stringify(payload)
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok || !data.ok) {
          throw new Error((data && data.error) || ('HTTP ' + response.status));
        }
        return data;
      });
    });
  }

  function ensureButton() {
    if (document.getElementById('nm-source-table')) return;
    var tb = document.querySelector('.nm-source-toolbar');
    var save = document.getElementById('nm-source-save');
    if (!tb || !save) return;
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.id = 'nm-source-table';
    btn.className = 'nm-source-secondary';
    btn.textContent = '编辑/插入表格';
    btn.title = '编辑光标所在的 Markdown/reST 表格；光标不在表格内时插入新表格';
    var anchor = document.getElementById('nm-source-reference') || save;
    anchor.insertAdjacentElement('afterend', btn);
    btn.addEventListener('click', openEditor);
  }

  function openEditor() {
    var cfg = readConfig();
    var cm = window.__nmCM;
    if (!cfg || !cm) {
      setStatus('表格编辑器仅在可写的源文件页签可用', true);
      return;
    }
    setStatus('读取表格…');
    api('/api/table_parse', {
      content: cm.getValue(),
      line: cm.getCursor().line,
      ext: cfg.source_ext || ''
    }).then(function (data) {
      setStatus('');
      showDialog(cm, data.table, false);
    }).catch(function (err) {
      var message = err.message || String(err);
      if (message.indexOf('光标不在') >= 0 || message.indexOf('行号超出') >= 0 ||
          message.indexOf('超出文件范围') >= 0 || message.indexOf('源文件为空') >= 0) {
        setStatus('');
        var ext = String(cfg.source_ext || '').toLowerCase();
        var isMd = ext === '.md' || ext === '.markdown' || ext === '.mdown';
        var format = isMd ? 'markdown' : 'rst-grid';
        showDialog(cm, {
          format: format,
          header: true,
          align: ['default', 'default'],
          rows: [['列 1', '列 2'], ['', '']]
        }, true);
        return;
      }
      setStatus(message, true);
    });
  }

  function showDialog(cm, block, insertOnly) {
    var model = {
      format: block.format,
      header: !!block.header,
      align: (block.align || []).slice(),
      rows: (block.rows || []).map(function (row) { return row.slice(); })
    };
    var insertLine = Math.max(0, Math.min(cm.getCursor().line, cm.lineCount() - 1));
    var overlay = document.createElement('div');
    overlay.className = 'nm-note-overlay nm-table-overlay';
    overlay.innerHTML =
      '<div class="nm-note-dialog nm-table-dialog" role="dialog" aria-label="编辑表格">' +
        '<div class="nm-note-head"><h2>编辑表格</h2>' +
          '<button type="button" class="nm-note-close" aria-label="关闭">×</button></div>' +
        '<div class="nm-table-toolbar">' +
          '<label><input type="checkbox" id="nm-table-header"> 第一行作为表头</label>' +
          '<button type="button" data-table-act="add-row">＋ 行</button>' +
          '<button type="button" data-table-act="add-col">＋ 列</button>' +
          '<button type="button" data-table-act="del-row">－ 末行</button>' +
          '<button type="button" data-table-act="del-col">－ 末列</button>' +
          '<span id="nm-table-status"></span>' +
        '</div>' +
        '<div class="nm-table-scroll"><table class="nm-table-edit-grid"></table></div>' +
        '<div class="nm-note-actions">' +
          '<button type="button" class="nm-note-cancel">取消</button>' +
          '<button type="button" class="nm-note-primary" id="nm-table-apply">应用到源文件</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);
    document.body.classList.add('nm-note-open');

    var table = overlay.querySelector('.nm-table-edit-grid');
    var headerCb = overlay.querySelector('#nm-table-header');
    var status = overlay.querySelector('#nm-table-status');

    headerCb.checked = model.header;
    if (model.format !== 'markdown') {
      headerCb.title = 'reST 表格可以没有表头';
    }

    function readInputs() {
      table.querySelectorAll('input[data-r]').forEach(function (input) {
        var r = parseInt(input.getAttribute('data-r'), 10);
        var c = parseInt(input.getAttribute('data-c'), 10);
        if (model.rows[r] && model.rows[r][c] !== undefined) {
          model.rows[r][c] = input.value;
        }
      });
      table.querySelectorAll('select[data-align-c]').forEach(function (sel) {
        var c = parseInt(sel.getAttribute('data-align-c'), 10);
        if (model.align[c] !== undefined) model.align[c] = sel.value;
      });
      model.header = !!headerCb.checked;
    }

    function blankRow(cols) {
      var row = [];
      for (var i = 0; i < cols; i++) row.push('');
      return row;
    }

    function render() {
      var cols = Math.max(1, model.rows[0] ? model.rows[0].length : model.align.length);
      model.rows.forEach(function (row) {
        while (row.length < cols) row.push('');
        row.length = cols;
      });
      while (model.align.length < cols) model.align.push('default');
      model.align.length = cols;

      var html = '<tr class="nm-table-col-actions"><td class="nm-table-gutter"></td>';
      for (var c = 0; c < cols; c++) {
        html += '<td><button type="button" class="nm-table-del" data-del-col="' + c +
          '" title="删除这一列">×</button></td>';
      }
      html += '</tr>';
      if (model.format === 'markdown') {
        html += '<tr class="nm-table-align-row"><td class="nm-table-gutter"></td>';
        for (var ac = 0; ac < cols; ac++) {
          html += '<td><select data-align-c="' + ac + '">' +
            '<option value="default">默认</option>' +
            '<option value="left">左对齐</option>' +
            '<option value="center">居中</option>' +
            '<option value="right">右对齐</option>' +
            '</select></td>';
        }
        html += '</tr>';
      }
      model.rows.forEach(function (row, r) {
        var cls = model.header && r === 0 ? ' class="nm-table-header-row"' : '';
        html += '<tr' + cls + '><td class="nm-table-gutter">' + (r + 1) + '</td>';
        row.forEach(function (value, c) {
          html += '<td><input type="text" data-r="' + r + '" data-c="' + c +
            '" value="' + escapeAttr(value) + '"></td>';
        });
        html += '<td class="nm-table-row-actions"><button type="button" class="nm-table-del" data-del-row="' +
          r + '" title="删除这一行">×</button></td></tr>';
      });
      table.innerHTML = html;
      headerCb.checked = model.header;
      overlay.querySelectorAll('select[data-align-c]').forEach(function (sel) {
        var c = parseInt(sel.getAttribute('data-align-c'), 10);
        sel.value = model.align[c] || 'default';
      });
    }

    function escapeAttr(value) {
      return String(value == null ? '' : value)
        .replace(/&/g, '&amp;').replace(/"/g, '&quot;')
        .replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    headerCb.addEventListener('change', function () {
      readInputs();
      render();
    });

    overlay.querySelectorAll('[data-table-act]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        readInputs();
        var act = btn.getAttribute('data-table-act');
        if (act === 'add-row') {
          var cols = Math.max(1, model.rows[0] ? model.rows[0].length : 1);
          model.rows.push(blankRow(cols));
        } else if (act === 'add-col') {
          model.rows.forEach(function (row) { row.push(''); });
          model.align.push('default');
        } else if (act === 'del-row') {
          if (model.rows.length > 1) model.rows.pop();
        } else if (act === 'del-col') {
          var width = model.rows[0] ? model.rows[0].length : 1;
          if (width > 1) {
            model.rows.forEach(function (row) { row.pop(); });
            model.align.pop();
          }
        }
        render();
      });
    });

    table.addEventListener('click', function (event) {
      var button = event.target.closest && event.target.closest('[data-del-row], [data-del-col]');
      if (!button) return;
      readInputs();
      if (button.hasAttribute('data-del-row')) {
        var row = parseInt(button.getAttribute('data-del-row'), 10);
        if (model.rows.length > 1 && row >= 0 && row < model.rows.length) {
          model.rows.splice(row, 1);
          render();
        }
      } else {
        var col = parseInt(button.getAttribute('data-del-col'), 10);
        var width = model.rows[0] ? model.rows[0].length : 1;
        if (width > 1 && col >= 0 && col < width) {
          model.rows.forEach(function (r) { r.splice(col, 1); });
          model.align.splice(col, 1);
          render();
        }
      }
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
    overlay.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') close();
    });

    function applyTable() {
      readInputs();
      status.textContent = '生成源码…';
      status.style.color = '';
      return api('/api/table_serialize', {
        format: model.format,
        header: model.header,
        align: model.align,
        rows: model.rows
      }).then(function (data) {
        var startLine = insertOnly ? insertLine : block.start_line;
        var start = {line: startLine, ch: 0};
        var end;
        if (insertOnly) {
          end = {line: startLine, ch: 0};
        } else {
          var endLine = block.end_line + 1;
          end = endLine < cm.lineCount()
            ? {line: endLine, ch: 0}
            : {line: block.end_line, ch: cm.getLine(block.end_line).length};
        }
        cm.replaceRange(data.text, start, end);
        var lines = data.text.replace(/\n$/, '').split('\n');
        block.start_line = startLine;
        block.end_line = startLine + Math.max(0, lines.length - 1);
        insertOnly = false;
        cm.setCursor({line: startLine, ch: 0});
        close();
        cm.focus();
        setStatus('表格已应用到源文件；请继续编辑文本，完成后保存并刷新渲染');
      }).catch(function (err) {
        status.textContent = err.message || String(err);
        status.style.color = '#f88';
      });
    }

    overlay.querySelector('#nm-table-apply').addEventListener('click', function () {
      applyTable();
    });

    render();
    var first = table.querySelector('input[data-r]');
    if (first) first.focus();
  }

  document.addEventListener('DOMContentLoaded', ensureButton);
})();
