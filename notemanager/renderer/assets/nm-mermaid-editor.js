/* Mermaid code-block editor with live preview and templates. */
(function () {
  'use strict';

  var TEMPLATES = [
    {key: 'flow', label: '流程图', text: 'flowchart TD\n    A[开始] --> B{判断}\n    B -->|是| C[结束]\n    B -->|否| D[处理]\n    D --> B'},
    {key: 'sequence', label: '时序图', text: 'sequenceDiagram\n    participant A as 客户端\n    participant B as 服务端\n    A->>B: 请求\n    B-->>A: 响应'},
    {key: 'class', label: '类图', text: 'classDiagram\n    class Animal {\n        +String name\n        +move()\n    }\n    Animal <|-- Dog'},
    {key: 'state', label: '状态图', text: 'stateDiagram-v2\n    [*] --> Idle\n    Idle --> Running: start\n    Running --> Idle: stop\n    Idle --> [*]'},
    {key: 'er', label: 'ER 图', text: 'erDiagram\n    USER ||--o{ ORDER : places\n    USER {\n        string name\n        string email\n    }\n    ORDER {\n        int id\n    }'},
    {key: 'gantt', label: '甘特图', text: 'gantt\n    title 项目计划\n    dateFormat YYYY-MM-DD\n    section 设计\n    需求 :a1, 2026-01-01, 7d\n    实现 :after a1, 14d'},
    {key: 'pie', label: '饼图', text: 'pie title 分布\n    "A" : 40\n    "B" : 35\n    "C" : 25'},
    {key: 'git', label: 'Git 图', text: 'gitGraph\n    commit\n    branch feature\n    checkout feature\n    commit\n    checkout main\n    merge feature'}
  ];

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

  function findMarkdown(cm) {
    var lines = cm.getValue().split('\n');
    var cursor = cm.getCursor().line;
    for (var i = 0; i < lines.length; i++) {
      var open = lines[i].match(/^\s*(`{3,}|~{3,})\s*mermaid\b[^\n]*$/i);
      if (!open) continue;
      var fence = open[1];
      var end = -1;
      for (var j = i + 1; j < lines.length; j++) {
        if (new RegExp('^\\s*' + (fence.charAt(0) === '`' ? '`' : '~') + '{3,}\\s*$').test(lines[j])) {
          end = j;
          break;
        }
      }
      if (end < 0) continue;
      if (cursor >= i && cursor <= end) {
        return {
          start: {line: i, ch: 0},
          end: {line: end, ch: lines[end].length},
          text: lines.slice(i + 1, end).join('\n'),
          format: 'markdown'
        };
      }
    }
    return null;
  }

  function findRst(cm) {
    var lines = cm.getValue().split('\n');
    var cursor = cm.getCursor().line;
    for (var i = 0; i < lines.length; i++) {
      if (!/^\s*\.\.\s+(code-block|code)::\s*mermaid\s*$/i.test(lines[i])) continue;
      var body = [];
      var end = i;
      var minIndent = Infinity;
      for (var j = i + 1; j < lines.length; j++) {
        if (!lines[j].trim()) {
          body.push('');
          end = j;
          continue;
        }
        if (!/^\s+/.test(lines[j])) break;
        var indent = lines[j].match(/^\s*/)[0].length;
        minIndent = Math.min(minIndent, indent);
        body.push(lines[j]);
        end = j;
      }
      if (cursor < i || cursor > end) continue;
      if (!isFinite(minIndent)) minIndent = 3;
      var text = body.map(function (line) {
        return line.length >= minIndent ? line.slice(minIndent) : line.trim();
      }).join('\n').replace(/^\n+|\n+$/g, '');
      return {
        start: {line: i, ch: 0},
        end: {line: end, ch: lines[end].length},
        text: text,
        format: 'rst'
      };
    }
    return null;
  }

  function findBlock(cm, ext) {
    return String(ext || '').toLowerCase() === '.rst' ? findRst(cm) : findMarkdown(cm);
  }

  function sourceFor(format, text) {
    if (format === 'rst') {
      return '.. code-block:: mermaid\n\n' +
        text.split('\n').map(function (line) { return '   ' + line; }).join('\n');
    }
    return '```mermaid\n' + text + '\n```';
  }

  function ensureButton() {
    if (document.getElementById('nm-source-mermaid')) return;
    var save = document.getElementById('nm-source-save');
    var toolbar = document.querySelector('.nm-source-toolbar');
    if (!save || !toolbar) return;
    var button = document.createElement('button');
    button.type = 'button';
    button.id = 'nm-source-mermaid';
    button.className = 'nm-source-secondary';
    button.textContent = '编辑/插入 Mermaid';
    button.title = '编辑光标处的 Mermaid 图，或插入新图';
    button.style.display = 'none';
    var anchor = document.getElementById('nm-source-object') ||
      document.getElementById('nm-source-reference') || save;
    anchor.insertAdjacentElement('afterend', button);
    button.addEventListener('click', openEditor);
  }

  function openEditor() {
    var cfg = readConfig();
    var cm = window.__nmCM;
    if (!cfg || !cm) {
      setStatus('Mermaid 编辑器仅在可写的源文件页签可用', true);
      return;
    }
    var ext = String(cfg.source_ext || '').toLowerCase();
    var existing = findBlock(cm, ext);
    showDialog(cm, existing || {
      start: null, end: null, text: '', format: ext === '.rst' ? 'rst' : 'markdown'
    });
  }

  function showDialog(cm, block) {
    var exists = !!block.start;
    var overlay = document.createElement('div');
    overlay.className = 'nm-note-overlay nm-mermaid-overlay';
    overlay.innerHTML =
      '<div class="nm-note-dialog nm-mermaid-dialog" role="dialog" aria-label="编辑 Mermaid">' +
        '<div class="nm-note-head"><h2>编辑 Mermaid</h2>' +
          '<button type="button" class="nm-note-close" aria-label="关闭">×</button></div>' +
        (exists ? '' : '<div class="nm-mermaid-template"><label>模板 <select id="nm-mermaid-template"></select></label></div>') +
        '<div class="nm-mermaid-body">' +
          '<textarea id="nm-mermaid-input" rows="14" spellcheck="false" placeholder="flowchart TD&#10;    A --> B"></textarea>' +
          '<div class="nm-mermaid-preview" id="nm-mermaid-preview"></div>' +
        '</div>' +
        '<div class="nm-note-error" id="nm-mermaid-error" hidden></div>' +
        '<div class="nm-note-actions"><button type="button" class="nm-note-cancel">取消</button>' +
          '<button type="button" class="nm-note-primary" id="nm-mermaid-apply">应用到源文件</button></div>' +
      '</div>';
    document.body.appendChild(overlay);
    document.body.classList.add('nm-note-open');

    var input = overlay.querySelector('#nm-mermaid-input');
    var preview = overlay.querySelector('#nm-mermaid-preview');
    var error = overlay.querySelector('#nm-mermaid-error');
    var template = overlay.querySelector('#nm-mermaid-template');
    var current = block.text || '';
    var timer = null;
    var renderSeq = 0;

    if (template) {
      template.innerHTML = TEMPLATES.map(function (item) {
        return '<option value="' + item.key + '">' + item.label + '</option>';
      }).join('');
      template.value = 'flow';
      if (!current) current = TEMPLATES[0].text;
      template.addEventListener('change', function () {
        var found = TEMPLATES.filter(function (item) { return item.key === template.value; })[0];
        if (found) {
          input.value = found.text;
          renderPreview();
        }
      });
    }
    input.value = current;

    function renderPreview() {
      var text = input.value.trim();
      var seq = ++renderSeq;
      error.hidden = true;
      preview.innerHTML = '';
      if (!text) {
        preview.textContent = 'Mermaid 预览';
        return;
      }
      if (typeof window.mermaid === 'undefined') {
        preview.textContent = text;
        return;
      }
      var id = 'nm-mermaid-preview-' + Date.now() + '-' + seq;
      try {
        Promise.resolve(window.mermaid.render(id, text)).then(function (result) {
          if (seq !== renderSeq) return;
          preview.innerHTML = (result && (result.svg || result)) || '';
        }).catch(function (err) {
          if (seq !== renderSeq) return;
          preview.textContent = '';
          error.textContent = (err && err.message) ? err.message : 'Mermaid 语法错误';
          error.hidden = false;
        });
      } catch (err) {
        error.textContent = (err && err.message) ? err.message : 'Mermaid 语法错误';
        error.hidden = false;
      }
    }

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
    input.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(renderPreview, 260);
    });

    overlay.querySelector('#nm-mermaid-apply').addEventListener('click', function () {
      var text = input.value.trim();
      if (!text) {
        error.textContent = 'Mermaid 源码不能为空';
        error.hidden = false;
        return;
      }
      var source = sourceFor(block.format, text);
      if (exists) {
        cm.replaceRange(source, block.start, block.end);
      } else {
        var cursor = cm.getCursor();
        var line = Math.max(0, Math.min(cursor.line, cm.lineCount() - 1));
        var prefix = (cm.getLine(line) || '').trim() ? '\n' : '';
        cm.replaceRange(prefix + source + '\n', {line: line, ch: 0}, {line: line, ch: 0});
      }
      close();
      cm.focus();
      setStatus('Mermaid 已应用到源文件；请继续编辑文本，完成后保存退出');
    });

    renderPreview();
    input.focus();
    input.select();
  }

  document.addEventListener('DOMContentLoaded', ensureButton);
  window.nmMermaidEditor = {
    findBlock: findBlock,
    sourceFor: sourceFor,
    templates: TEMPLATES
  };
})();
