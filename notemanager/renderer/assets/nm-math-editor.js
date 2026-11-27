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

  var MATH_PALETTE = [
    {key: 'basic', label: '结构', items: [
      {tex: String.raw`\frac{a}{b}`, title: '分数'},
      {tex: String.raw`\sqrt{x}`, title: '平方根'},
      {tex: String.raw`\sqrt[n]{x}`, title: 'n 次根'},
      {tex: String.raw`x^{n}`, title: '上标'},
      {tex: String.raw`x_{i}`, title: '下标'},
      {tex: String.raw`\left( x \right)`, title: '括号'},
      {tex: String.raw`\left| x \right|`, title: '绝对值'},
      {tex: String.raw`\lceil x \rceil`, title: '上取整'},
      {tex: String.raw`\lfloor x \rfloor`, title: '下取整'},
      {tex: String.raw`\overline{x}`, title: '上划线'},
      {tex: String.raw`\underline{x}`, title: '下划线'}
    ]},
    {key: 'derive', label: '推导', items: [
      {tex: String.raw`\Rightarrow`, title: '推出'},
      {tex: String.raw`\Leftarrow`, title: '反向推出'},
      {tex: String.raw`\Leftrightarrow`, title: '等价'},
      {tex: String.raw`\implies`, title: '蕴含'},
      {tex: String.raw`\iff`, title: '当且仅当'},
      {tex: String.raw`\therefore`, title: '所以'},
      {tex: String.raw`\because`, title: '因为'},
      {tex: String.raw`\vdash`, title: '可证明'},
      {tex: String.raw`\models`, title: '满足'},
      {tex: String.raw`\Vdash`, title: '强制'},
      {tex: String.raw`\equiv`, title: '恒等'},
      {tex: String.raw`\approx`, title: '约等于'},
      {tex: String.raw`\sim`, title: '相似'},
      {tex: String.raw`\propto`, title: '正比'},
      {tex: String.raw`\ne`, title: '不等于'},
      {tex: String.raw`\le`, title: '小于等于'},
      {tex: String.raw`\ge`, title: '大于等于'},
      {tex: String.raw`\forall`, title: '任意'},
      {tex: String.raw`\exists`, title: '存在'},
      {tex: String.raw`\neg`, title: '非'},
      {tex: String.raw`\land`, title: '且'},
      {tex: String.raw`\lor`, title: '或'}
    ]},
    {key: 'sets', label: '集合', items: [
      {tex: String.raw`\cup`, title: '并集'},
      {tex: String.raw`\cap`, title: '交集'},
      {tex: String.raw`\setminus`, title: '差集'},
      {tex: String.raw`\subseteq`, title: '子集等于'},
      {tex: String.raw`\subset`, title: '真子集'},
      {tex: String.raw`\supseteq`, title: '超集等于'},
      {tex: String.raw`\supset`, title: '真超集'},
      {tex: String.raw`\in`, title: '属于'},
      {tex: String.raw`\notin`, title: '不属于'},
      {tex: String.raw`\emptyset`, title: '空集'},
      {tex: String.raw`\mathbb{R}`, title: '实数集'},
      {tex: String.raw`\mathbb{N}`, title: '自然数集'},
      {tex: String.raw`\{x \mid P(x)\}`, title: '集合构造式'},
      {tex: String.raw`\complement`, title: '补集'},
      {tex: String.raw`A \times B`, title: '笛卡尔积'}
    ]},
    {key: 'calculus', label: '微积分', items: [
      {tex: String.raw`\sum_{i=1}^{n}`, title: '求和'},
      {tex: String.raw`\prod_{i=1}^{n}`, title: '连乘'},
      {tex: String.raw`\int_{a}^{b}`, title: '定积分'},
      {tex: String.raw`\iint`, title: '二重积分'},
      {tex: String.raw`\oint`, title: '环路积分'},
      {tex: String.raw`\lim_{x \to 0}`, title: '极限'},
      {tex: String.raw`\frac{d}{dx}`, title: '导数'},
      {tex: String.raw`\frac{\partial f}{\partial x}`, title: '偏导'},
      {tex: String.raw`\nabla`, title: '梯度'},
      {tex: String.raw`\infty`, title: '无穷'},
      {tex: String.raw`\Delta`, title: '增量'},
      {tex: String.raw`\varepsilon`, title: '任意小量'}
    ]},
    {key: 'linear', label: '线性代数', items: [
      {tex: String.raw`\begin{matrix} a & b \\ c & d \end{matrix}`, title: '矩阵'},
      {tex: String.raw`\begin{pmatrix} a & b \\ c & d \end{pmatrix}`, title: '圆括号矩阵'},
      {tex: String.raw`\begin{bmatrix} a & b \\ c & d \end{bmatrix}`, title: '方括号矩阵'},
      {tex: String.raw`\begin{vmatrix} a & b \\ c & d \end{vmatrix}`, title: '行列式'},
      {tex: String.raw`A^{T}`, title: '转置'},
      {tex: String.raw`\vec{v}`, title: '向量'},
      {tex: String.raw`\lVert x \rVert`, title: '范数'},
      {tex: String.raw`\langle x, y \rangle`, title: '内积'},
      {tex: String.raw`\mathbb{R}^{n}`, title: 'n 维实空间'},
      {tex: String.raw`\operatorname{rank}`, title: '秩'},
      {tex: String.raw`\dim`, title: '维数'},
      {tex: String.raw`\ker`, title: '核'}
    ]},
    {key: 'greek', label: '希腊字母', items: [
      {tex: String.raw`\alpha`, title: 'alpha'}, {tex: String.raw`\beta`, title: 'beta'},
      {tex: String.raw`\gamma`, title: 'gamma'}, {tex: String.raw`\delta`, title: 'delta'},
      {tex: String.raw`\epsilon`, title: 'epsilon'}, {tex: String.raw`\zeta`, title: 'zeta'},
      {tex: String.raw`\eta`, title: 'eta'}, {tex: String.raw`\theta`, title: 'theta'},
      {tex: String.raw`\lambda`, title: 'lambda'}, {tex: String.raw`\mu`, title: 'mu'},
      {tex: String.raw`\nu`, title: 'nu'}, {tex: String.raw`\xi`, title: 'xi'},
      {tex: String.raw`\pi`, title: 'pi'}, {tex: String.raw`\rho`, title: 'rho'},
      {tex: String.raw`\sigma`, title: 'sigma'}, {tex: String.raw`\tau`, title: 'tau'},
      {tex: String.raw`\phi`, title: 'phi'}, {tex: String.raw`\chi`, title: 'chi'},
      {tex: String.raw`\psi`, title: 'psi'}, {tex: String.raw`\omega`, title: 'omega'},
      {tex: String.raw`\Gamma`, title: 'Gamma'}, {tex: String.raw`\Delta`, title: 'Delta'},
      {tex: String.raw`\Theta`, title: 'Theta'}, {tex: String.raw`\Lambda`, title: 'Lambda'},
      {tex: String.raw`\Sigma`, title: 'Sigma'}, {tex: String.raw`\Phi`, title: 'Phi'},
      {tex: String.raw`\Omega`, title: 'Omega'}
    ]},
    {key: 'arrows', label: '箭头', items: [
      {tex: String.raw`\to`, title: '到'}, {tex: String.raw`\mapsto`, title: '映射到'},
      {tex: String.raw`\longrightarrow`, title: '长右箭头'},
      {tex: String.raw`\uparrow`, title: '上箭头'}, {tex: String.raw`\downarrow`, title: '下箭头'},
      {tex: String.raw`\rightleftharpoons`, title: '可逆反应'},
      {tex: String.raw`\nearrow`, title: '右上箭头'}, {tex: String.raw`\searrow`, title: '右下箭头'}
    ]}
  ];

  function escAttr(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/"/g, '&quot;')
      .replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function renderPalette(overlay, input, renderPreview) {
    var tabs = overlay.querySelector('#nm-math-palette-tabs');
    var list = overlay.querySelector('#nm-math-palette-list');
    if (!tabs || !list) return;
    var active = MATH_PALETTE[0].key;
    function draw() {
      tabs.innerHTML = MATH_PALETTE.map(function (cat) {
        return '<button type="button" data-math-cat="' + cat.key + '"' +
          (cat.key === active ? ' class="active"' : '') + '>' + escAttr(cat.label) + '</button>';
      }).join('');
      var cat = MATH_PALETTE.filter(function (c) { return c.key === active; })[0] || MATH_PALETTE[0];
      list.innerHTML = cat.items.map(function (item) {
        return '<button type="button" class="nm-math-symbol-btn" title="' +
          escAttr(item.title) + '" data-math-tex="' + escAttr(item.tex) + '">' +
          '<span class="nm-math-icon" data-math-icon="' + escAttr(item.tex) + '"></span></button>';
      }).join('');
      list.querySelectorAll('.nm-math-icon').forEach(function (span) {
        var tex = span.getAttribute('data-math-icon') || '';
        if (typeof window.katex !== 'undefined') {
          try {
            window.katex.render(tex, span, {throwOnError: false, displayMode: false, strict: 'ignore'});
          } catch (e) {
            span.textContent = tex;
          }
        } else {
          span.textContent = tex;
        }
      });
      tabs.querySelectorAll('[data-math-cat]').forEach(function (button) {
        button.addEventListener('click', function () {
          active = button.getAttribute('data-math-cat');
          draw();
        });
      });
      list.querySelectorAll('[data-math-tex]').forEach(function (button) {
        button.addEventListener('click', function () {
          insertMathSnippet(input, button.getAttribute('data-math-tex') || '', renderPreview);
        });
      });
    }
    draw();
  }

  function insertMathSnippet(input, snippet, renderPreview) {
    var start = input.selectionStart;
    var end = input.selectionEnd;
    input.setRangeText(snippet, start, end, 'end');
    var brace = snippet.indexOf('{}');
    if (brace >= 0) {
      input.setSelectionRange(start + brace + 1, start + brace + 1);
    }
    input.focus();
    renderPreview();
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
    var envRe = /\\begin\{(align\*?|equation\*?|gather\*?|matrix|pmatrix|bmatrix|vmatrix|cases|split|array|aligned|alignedat)\}[\s\S]*?\\end\{\1\}/g;
    while ((m = envRe.exec(content))) {
      var envBegin = m.index;
      var envEnd = m.index + m[0].length;
      if (offset >= envBegin && offset <= envEnd) {
        return formulaFromMatch(m, content, true, 'markdown');
      }
    }
    var parenDisplayRe = /\\\(([\s\S]*?)\\\)/g;
    while ((m = parenDisplayRe.exec(content))) {
      var pBegin = m.index;
      var pEnd = m.index + m[0].length;
      if (offset >= pBegin && offset <= pEnd) {
        return formulaFromMatch(m, content, false, 'markdown');
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
        '<div class="nm-math-palette" aria-label="公式模板和符号">' +
          '<div class="nm-math-palette-tabs" id="nm-math-palette-tabs"></div>' +
          '<div class="nm-math-palette-list" id="nm-math-palette-list"></div>' +
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
    renderPalette(overlay, input, renderPreview);
    renderPreview();

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
      setStatus('公式已应用到源文件；请继续编辑文本，完成后保存退出');
    });

    renderPreview();
    input.focus();
    input.select();
  }

  document.addEventListener('DOMContentLoaded', ensureButton);
  window.nmMathEditor = {findFormula: findFormula, sourceFor: sourceFor};
})();
