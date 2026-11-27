/* Formula editor: detect a formula at the source cursor, preview it with KaTeX, write Markdown/reST back. */
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

  function posAt(content, offset) {
    var before = content.slice(0, Math.max(0, offset)).split('\n');
    return {line: before.length - 1, ch: before[before.length - 1].length};
  }

  function offsetAt(cm, pos) {
    var line = Math.max(0, Math.min(pos.line, cm.lineCount() - 1));
    var total = 0;
    for (var i = 0; i < line; i++) total += cm.getLine(i).length + 1;
    return total + Math.min(pos.ch, cm.getLine(line).length);
  }

  function cursorOffset(content, cursor) {
    var lines = content.split('\n');
    var total = 0;
    for (var i = 0; i < cursor.line && i < lines.length; i++) {
      total += lines[i].length + 1;
    }
    return total + cursor.ch;
  }

  function formulaFromMatch(match, content, display, format) {
    var start = posAt(content, match.index);
    var endOffset = match.index + match[0].length;
    return {
      start: start,
      end: posAt(content, endOffset),
      text: match[1],
      display: display,
      format: format
    };
  }

  function findMarkdown(content, cursor) {
    var offset = cursorOffset(content, cursor);
    var displayRe = /\$\$([\s\S]*?)\$\$/g;
    var m;
    while ((m = displayRe.exec(content))) {
      var begin = m.index;
      var finish = m.index + m[0].length;
      if (offset >= begin && offset <= finish) {
        return formulaFromMatch(m, content, true, 'markdown');
      }
    }
    var bracketRe = /\\\[([\s\S]*?)\\\]/g;
    while ((m = bracketRe.exec(content))) {
      var b = m.index;
      var f = m.index + m[0].length;
      if (offset >= b && offset <= f) {
        return formulaFromMatch(m, content, true, 'markdown');
      }
    }
    var lines = content.split('\n');
    var lineText = lines[cursor.line] || '';
    var ch = cursor.ch;

    // \(...\) and $...$ on the cursor line.
    var paren = /\\\(([\s\S]*?)\\\)/g;
    while ((m = paren.exec(lineText))) {
      if (ch >= m.index && ch <= m.index + m[0].length) {
        return {
          start: {line: cursor.line, ch: m.index},
          end: {line: cursor.line, ch: m.index + m[0].length},
          text: m[1], display: false, format: 'markdown'
        };
      }
    }
    var dollars = [];
    for (var i = 0; i < lineText.length; i++) {
      if (lineText.charAt(i) !== '$') continue;
      if (i > 0 && lineText.charAt(i - 1) === '\\') continue;
      if (lineText.charAt(i + 1) === '$' || (i > 0 && lineText.charAt(i - 1) === '$')) continue;
      dollars.push(i);
    }
    for (var k = 0; k + 1 < dollars.length; k += 2) {
      var a = dollars[k], z = dollars[k + 1];
      if (ch >= a && ch <= z + 1) {
        return {
          start: {line: cursor.line, ch: a},
          end: {line: cursor.line, ch: z + 1},
          text: lineText.slice(a + 1, z),
          display: false, format: 'markdown'
        };
      }
    }
    return null;
  }

  function findRst(content, cursor) {
    var lines = content.split('\n');
    for (var i = 0; i < lines.length; i++) {
      var m = lines[i].match(/^\s*\.\.\s+math::\s*(.*)$/);
      if (!m) continue;
      var rest = (m[1] || '').trim();
      if (rest) {
        if (cursor.line === i) {
          return {
            start: {line: i, ch: 0},
            end: {line: i, ch: lines[i].length},
            text: rest, display: true, format: 'rst'
          };
        }
        continue;
      }
      var end = i;
      var body = [];
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
      if (cursor.line >= i && cursor.line <= end) {
        if (!isFinite(minIndent)) minIndent = 3;
        var text = body.map(function (line) {
          return line.length >= minIndent ? line.slice(minIndent) : line.trim();
        }).join('\n').replace(/^\n+|\n+$/g, '');
        return {
          start: {line: i, ch: 0},
          end: {line: end, ch: lines[end].length},
          text: text, display: true, format: 'rst'
        };
      }
    }

    var lineText = lines[cursor.line] || '';
    var inline = /:math:`([^`]*)`/g;
    var m2;
    while ((m2 = inline.exec(lineText))) {
      if (cursor.ch >= m2.index && cursor.ch <= m2.index + m2[0].length) {
        return {
          start: {line: cursor.line, ch: m2.index},
          end: {line: cursor.line, ch: m2.index + m2[0].length},
          text: m2[1], display: false, format: 'rst'
        };
      }
    }
    return null;
  }

  function findFormula(cm, ext) {
    var content = cm.getValue();
    var cursor = cm.getCursor();
    return ext === '.rst' ? findRst(content, cursor) : findMarkdown(content, cursor);
  }

  function sourceFor(format, display, text) {
    if (format === 'markdown') {
      return display ? '$$\n' + text + '\n$$' : '$' + text + '$';
    }
    if (display) {
      var indented = text.split('\n').map(function (line) { return '   ' + line; }).join('\n');
      return '.. math::\n\n' + indented;
    }
    return ':math:`' + text + '`';
  }

  function ensureButton() {
    if (document.getElementById('nm-source-math')) return;
    var tb = document.querySelector('.nm-source-toolbar');
    var save = document.getElementById('nm-source-save');
    if (!tb || !save) return;
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.id = 'nm-source-math';
    btn.className = 'nm-source-secondary';
    btn.textContent = '编辑/插入公式';
    btn.title = '编辑光标处的数学公式，或插入新公式';
    var anchor = document.getElementById('nm-source-table') ||
      document.getElementById('nm-source-reference') || save;
    anchor.insertAdjacentElement('afterend', btn);
    btn.addEventListener('click', openEditor);
  }

  function openEditor() {
    var cfg = readConfig();
    var cm = window.__nmCM;
    if (!cfg || !cm) {
      setStatus('公式编辑器仅在可写的源文件页签可用', true);
      return;
    }
    var ext = String(cfg.source_ext || '').toLowerCase();
    var format = ext === '.rst' ? 'rst' : 'markdown';
    var existing = findFormula(cm, ext);
    showDialog(cm, existing || {
      start: null, end: null, text: '', display: false, format: format
    });
  }

  function showDialog(cm, block) {
    var existing = !!block.start;
    var overlay = document.createElement('div');
    overlay.className = 'nm-note-overlay nm-math-overlay';
    overlay.innerHTML =
      '<div class="nm-note-dialog nm-math-dialog" role="dialog" aria-label="编辑数学公式">' +
        '<div class="nm-note-head"><h2>编辑数学公式</h2>' +
          '<button type="button" class="nm-note-close" aria-label="关闭">×</button></div>' +
        '<div class="nm-math-mode"><label>显示方式 ' +
          '<select id="nm-math-display">' +
            '<option value="inline">行内公式</option>' +
            '<option value="display">行间公式</option>' +
          '</select></label>' +
          '<span class="nm-math-format">' + (block.format === 'rst' ? 'reST' : 'Markdown') + '</span>' +
        '</div>' +
        '<textarea id="nm-math-input" rows="6" spellcheck="false" placeholder="例如：\\frac{a}{b} + \\sqrt{x}"></textarea>' +
        '<div class="nm-math-preview" id="nm-math-preview" aria-live="polite"></div>' +
        '<div class="nm-note-error" id="nm-math-error" hidden></div>' +
        '<div class="nm-note-actions"><button type="button" class="nm-note-cancel">取消</button>' +
          '<button type="button" class="nm-note-primary" id="nm-math-apply">应用公式</button></div>' +
      '</div>';
    document.body.appendChild(overlay);
    document.body.classList.add('nm-note-open');
    var input = overlay.querySelector('#nm-math-input');
    var display = overlay.querySelector('#nm-math-display');
    var preview = overlay.querySelector('#nm-math-preview');
    var error = overlay.querySelector('#nm-math-error');
    var status = overlay.querySelector('#nm-math-error');

    input.value = block.text || '';
    display.value = block.display ? 'display' : 'inline';

    function renderPreview() {
      var text = input.value.trim();
      var displayMode = display.value === 'display';
      error.hidden = true;
      preview.textContent = '';
      if (!text) {
        preview.textContent = '公式预览';
        return;
      }
      if (typeof window.katex === 'undefined') {
        preview.textContent = text;
        return;
      }
      try {
        window.katex.render(text, preview, {
          displayMode: displayMode,
          throwOnError: true,
          strict: 'ignore'
        });
      } catch (e) {
        preview.textContent = '';
        error.textContent = e && e.message ? e.message : '公式语法错误';
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
    input.addEventListener('input', renderPreview);
    display.addEventListener('change', renderPreview);

    overlay.querySelector('#nm-math-apply').addEventListener('click', function () {
      var text = input.value.trim();
      if (!text) {
        error.textContent = '公式不能为空';
        error.hidden = false;
        return;
      }
      var displayMode = display.value === 'display';
      var source = sourceFor(block.format, displayMode, text);
      if (existing) {
        cm.replaceRange(source, block.start, block.end);
      } else {
        var cursor = cm.getCursor();
        if (displayMode) {
          var line = Math.max(0, Math.min(cursor.line, cm.lineCount() - 1));
          var prefix = (cm.getLine(line) || '').trim() ? '\n' : '';
          cm.replaceRange(prefix + source + '\n', {line: line, ch: 0}, {line: line, ch: 0});
          cm.setCursor({line: line + (prefix ? 1 : 0), ch: 0});
        } else {
          cm.replaceRange(source, cursor, cursor);
          cm.setCursor({line: cursor.line, ch: cursor.ch + source.length});
        }
      }
      close();
      cm.focus();
      setStatus('公式已应用到源文件；请继续编辑文本，完成后保存并刷新渲染');
    });

    renderPreview();
    input.focus();
    input.select();
  }

  document.addEventListener('DOMContentLoaded', ensureButton);
  window.nmMathEditor = {findFormula: findFormula, sourceFor: sourceFor};
})();
