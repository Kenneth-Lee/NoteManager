/* Mermaid code-block editor with live preview, templates and line palette. */
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

  var PALETTE = {
    flow: {label: '流程图', items: [
      {label: '矩形节点', params: [
        {key: 'id', label: '节点 ID', placeholder: 'A', required: true},
        {key: 'text', label: '显示文字', placeholder: '节点'}
      ], build: function (v) {
        return (v.id || 'A') + '[' + (v.text || '节点') + ']';
      }},
      {label: '圆角节点', params: [
        {key: 'id', label: '节点 ID', placeholder: 'A', required: true},
        {key: 'text', label: '显示文字', placeholder: '节点'}
      ], build: function (v) {
        return (v.id || 'A') + '(' + (v.text || '节点') + ')';
      }},
      {label: '判断节点', params: [
        {key: 'id', label: '节点 ID', placeholder: 'A', required: true},
        {key: 'text', label: '显示文字', placeholder: '条件'}
      ], build: function (v) {
        return (v.id || 'A') + '{' + (v.text || '条件') + '}';
      }},
      {label: '圆形节点', params: [
        {key: 'id', label: '节点 ID', placeholder: 'A', required: true},
        {key: 'text', label: '显示文字', placeholder: '节点'}
      ], build: function (v) {
        return (v.id || 'A') + '((' + (v.text || '节点') + '))';
      }},
      {label: '数据库节点', params: [
        {key: 'id', label: '节点 ID', placeholder: 'A', required: true},
        {key: 'text', label: '显示文字', placeholder: '数据库'}
      ], build: function (v) {
        return (v.id || 'A') + '[(' + (v.text || '数据库') + ')]';
      }},
      {label: '子流程', params: [
        {key: 'id', label: '节点 ID', placeholder: 'A', required: true},
        {key: 'text', label: '显示文字', placeholder: '子流程'}
      ], build: function (v) {
        return (v.id || 'A') + '[[' + (v.text || '子流程') + ']]';
      }},
      {label: '箭头', params: [
        {key: 'from', label: '起点', placeholder: 'A', required: true},
        {key: 'to', label: '终点', placeholder: 'B', required: true},
        {key: 'label', label: '箭头文字', placeholder: '可选'},
        {key: 'style', label: '线型', type: 'select', options: [
          {value: '-->', label: '实线箭头'}, {value: '-.->', label: '虚线箭头'},
          {value: '==>', label: '粗箭头'}, {value: '---', label: '无箭头实线'}
        ]}
      ], build: function (v) {
        var label = v.label ? '|' + v.label + '|' : '';
        return (v.from || 'A') + ' ' + (v.style || '-->') + label + ' ' + (v.to || 'B');
      }},
      {label: '子图', params: [
        {key: 'name', label: '子图名称', placeholder: '名称', required: true}
      ], build: function (v) {
        return 'subgraph ' + (v.name || '名称') + '\n    A --> B\nend';
      }},
      {label: '注释', params: [
        {key: 'text', label: '注释内容', placeholder: '说明', required: true}
      ], build: function (v) {
        return '%% ' + (v.text || '说明');
      }}
    ]},
    sequence: {label: '时序图', items: [
      {label: '参与者', params: [
        {key: 'alias', label: '别名', placeholder: 'A', required: true},
        {key: 'name', label: '显示名称', placeholder: '名称'}
      ], build: function (v) {
        return 'participant ' + (v.alias || 'A') + (v.name ? ' as ' + v.name : '');
      }},
      {label: '消息', params: [
        {key: 'from', label: '发送方', placeholder: 'A', required: true},
        {key: 'to', label: '接收方', placeholder: 'B', required: true},
        {key: 'text', label: '消息', placeholder: '消息', required: true},
        {key: 'arrow', label: '线型', type: 'select', options: [
          {value: '->>', label: '实线箭头'}, {value: '-->>', label: '虚线箭头'},
          {value: '-)', label: '开放箭头'}, {value: '--x', label: '叉号'}
        ]}
      ], build: function (v) {
        return (v.from || 'A') + (v.arrow || '->>') + (v.to || 'B') + ': ' + (v.text || '消息');
      }},
      {label: '备注', params: [
        {key: 'position', label: '位置', type: 'select', options: [
          {value: 'over', label: '覆盖'}, {value: 'right of', label: '右侧'},
          {value: 'left of', label: '左侧'}
        ]},
        {key: 'target', label: '对象', placeholder: 'A', required: true},
        {key: 'text', label: '备注', placeholder: '说明', required: true}
      ], build: function (v) {
        return 'Note ' + (v.position || 'over') + ' ' + (v.target || 'A') + ': ' + (v.text || '说明');
      }},
      {label: '循环', params: [
        {key: 'condition', label: '条件', placeholder: '条件', required: true},
        {key: 'from', label: '发送方', placeholder: 'A'},
        {key: 'to', label: '接收方', placeholder: 'B'},
        {key: 'text', label: '消息', placeholder: '消息'}
      ], build: function (v) {
        return 'loop ' + (v.condition || '条件') + '\n    ' +
          (v.from || 'A') + '->>' + (v.to || 'B') + ': ' + (v.text || '消息') + '\nend';
      }},
      {label: '条件', params: [
        {key: 'condition', label: '条件', placeholder: '条件', required: true},
        {key: 'yes', label: '成立时消息', placeholder: '成立'},
        {key: 'no', label: '否则消息', placeholder: '不成立'}
      ], build: function (v) {
        return 'alt ' + (v.condition || '条件') + '\n    A->>B: ' + (v.yes || '成立') +
          '\nelse 否则\n    A->>B: ' + (v.no || '不成立') + '\nend';
      }},
      {label: '激活', params: [
        {key: 'participant', label: '参与者', placeholder: 'A', required: true}
      ], build: function (v) {
        return 'activate ' + (v.participant || 'A');
      }},
      {label: '取消激活', params: [
        {key: 'participant', label: '参与者', placeholder: 'A', required: true}
      ], build: function (v) {
        return 'deactivate ' + (v.participant || 'A');
      }}
    ]},
    'class': {label: '类图', items: [
      {label: '类', insert: 'class Name'},
      {label: '属性', insert: '+String name'},
      {label: '方法', insert: '+method()'},
      {label: '继承', insert: 'A <|-- B'},
      {label: '组合', insert: 'A *-- B'},
      {label: '聚合', insert: 'A o-- B'},
      {label: '关联', insert: 'A --> B'},
      {label: '依赖', insert: 'A ..> B'},
      {label: '注释', insert: 'note for A "说明"'}
    ]},
    state: {label: '状态图', items: [
      {label: '开始', insert: '[*] --> State'},
      {label: '结束', insert: 'State --> [*]'},
      {label: '转移', insert: 'State --> State2 : event'},
      {label: '复合状态', insert: 'state 复合状态 {\n    [*] --> A\n}'},
      {label: '选择', insert: 'state 选择 <<choice>>'},
      {label: '分叉', insert: 'state 分叉 <<fork>>'},
      {label: '汇合', insert: 'state 汇合 <<join>>'},
      {label: '备注', insert: 'note right of State : 说明'}
    ]},
    er: {label: 'ER 图', items: [
      {label: '实体', insert: 'ENTITY'},
      {label: '实体字段', insert: 'ENTITY {\n    string name\n}'},
      {label: '一对多', insert: 'A ||--o{ B : relation'},
      {label: '一对一', insert: 'A ||--|| B : relation'},
      {label: '多对多', insert: 'A }o--o{ B : relation'},
      {label: '主键', insert: 'string id PK'},
      {label: '外键', insert: 'int other_id FK'}
    ]},
    gantt: {label: '甘特图', items: [
      {label: '分区', insert: 'section 名称'},
      {label: '任务', insert: '任务 :a1, 2026-01-01, 7d'},
      {label: '接续任务', insert: '任务 :after a1, 5d'},
      {label: '里程碑', insert: '里程碑 :milestone, m1, 2026-01-10, 0d'},
      {label: '完成状态', insert: '任务 :done, a2, 2026-01-01, 3d'},
      {label: '进行中', insert: '任务 :active, a3, 2026-01-04, 3d'}
    ]},
    pie: {label: '饼图', items: [
      {label: '数据项', insert: '"标签" : 40'}
    ]},
    git: {label: 'Git 图', items: [
      {label: '提交', params: [
        {key: 'id', label: '提交 ID', placeholder: 'abc123'},
        {key: 'tag', label: '标签', placeholder: 'v1.0'},
        {key: 'type', label: '类型', type: 'select', options: [
          {value: '', label: '普通'}, {value: 'HIGHLIGHT', label: '高亮'},
          {value: 'REVERSE', label: '反向'}
        ]}
      ], build: function (v) {
        var out = 'commit';
        if (v.id) out += ' id: "' + String(v.id).replace(/"/g, '\\"') + '"';
        if (v.tag) out += ' tag: "' + String(v.tag).replace(/"/g, '\\"') + '"';
        if (v.type) out += ' type: ' + v.type;
        return out;
      }},
      {label: '分支', params: [
        {key: 'name', label: '分支名', placeholder: 'feature', required: true}
      ], build: function (v) { return 'branch ' + (v.name || 'feature'); }},
      {label: '切换分支', params: [
        {key: 'name', label: '分支名', placeholder: 'feature', required: true}
      ], build: function (v) { return 'checkout ' + (v.name || 'feature'); }},
      {label: '合并', params: [
        {key: 'name', label: '分支名', placeholder: 'feature', required: true},
        {key: 'id', label: '合并提交 ID', placeholder: 'abc123'}
      ], build: function (v) {
        return 'merge ' + (v.name || 'feature') +
          (v.id ? ' id: "' + String(v.id).replace(/"/g, '\\"') + '"' : '');
      }},
      {label: '标签', params: [
        {key: 'name', label: '标签名', placeholder: 'v1.0', required: true}
      ], build: function (v) { return 'tag ' + (v.name || 'v1.0'); }},
      {label: '樱桃采摘', params: [
        {key: 'id', label: '提交 ID', placeholder: 'abc123', required: true}
      ], build: function (v) {
        return 'cherry-pick id: "' + String(v.id || 'abc123').replace(/"/g, '\\"') + '"';
      }}
    ]}
  };

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

  function detectCategory(text) {
    var first = String(text || '').split('\n').map(function (line) { return line.trim(); })
      .filter(Boolean)[0] || '';
    if (/^sequenceDiagram\b/i.test(first)) return 'sequence';
    if (/^classDiagram\b/i.test(first)) return 'class';
    if (/^stateDiagram\b/i.test(first)) return 'state';
    if (/^erDiagram\b/i.test(first)) return 'er';
    if (/^gantt\b/i.test(first)) return 'gantt';
    if (/^pie\b/i.test(first)) return 'pie';
    if (/^gitGraph\b/i.test(first)) return 'git';
    return 'flow';
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

  function makeEditor(textarea, mainCm) {
    if (typeof CodeMirror === 'undefined') {
      return {
        getValue: function () { return textarea.value; },
        setValue: function (v) { textarea.value = v; },
        focus: function () { textarea.focus(); },
        select: function () { textarea.select(); },
        raw: null,
        replaceSelection: function (text) {
          var start = textarea.selectionStart;
          var end = textarea.selectionEnd;
          textarea.setRangeText(text, start, end, 'end');
        },
        insertStandalone: function (text) {
          var pos = textarea.selectionStart;
          var before = textarea.value.slice(0, pos);
          var lineStart = before.lastIndexOf('\n') + 1;
          var lineEnd = textarea.value.indexOf('\n', pos);
          if (lineEnd < 0) lineEnd = textarea.value.length;
          var line = textarea.value.slice(lineStart, lineEnd);
          if (line.trim()) {
            var indent = (line.match(/^[\t ]*/) || [''])[0];
            textarea.setRangeText('\n' + indent + text, lineEnd, lineEnd, 'end');
          } else {
            textarea.setRangeText(text, lineStart, lineEnd, 'end');
          }
        },
        onChange: function (fn) { textarea.addEventListener('input', fn); }
      };
    }
    var vim = mainCm && String(mainCm.getOption('keyMap') || '').indexOf('vim') >= 0;
    var cm = CodeMirror.fromTextArea(textarea, {
      mode: null,
      theme: 'dracula',
      lineNumbers: true,
      lineWrapping: true,
      keyMap: vim ? 'vim' : 'default'
    });
    cm.setSize('100%', '100%');
    return {
      getValue: function () { return cm.getValue(); },
      setValue: function (v) { cm.setValue(v); },
      focus: function () { cm.focus(); },
      select: function () { cm.execCommand('selectAll'); },
      raw: cm,
      replaceSelection: function (text) { cm.replaceSelection(text); },
      insertStandalone: function (text) {
        var cursor = cm.getCursor();
        var line = cm.getLine(cursor.line) || '';
        if (line.trim()) {
          var indent = (line.match(/^[\t ]*/) || [''])[0];
          cm.replaceRange('\n' + indent + text, {line: cursor.line, ch: line.length});
        } else {
          cm.replaceRange(text, {line: cursor.line, ch: 0}, {line: cursor.line, ch: line.length});
        }
      },
      onChange: function (fn) { cm.on('change', fn); }
    };
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
        '<div class="nm-mermaid-palette">' +
          '<div class="nm-mermaid-palette-list" id="nm-mermaid-palette-list"></div>' +
          '<div class="nm-mermaid-properties" id="nm-mermaid-properties"></div>' +
        '</div>' +
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

    var textarea = overlay.querySelector('#nm-mermaid-input');
    var preview = overlay.querySelector('#nm-mermaid-preview');
    var error = overlay.querySelector('#nm-mermaid-error');
    var template = overlay.querySelector('#nm-mermaid-template');
    var editor = makeEditor(textarea, cm);
    var current = block.text || '';
    var timer = null;
    var renderSeq = 0;
    var activeCategory = detectCategory(current);

    if (template) {
      template.innerHTML = TEMPLATES.map(function (item) {
        return '<option value="' + item.key + '">' + item.label + '</option>';
      }).join('');
      template.value = activeCategory;
      if (!current) {
        var initial = TEMPLATES.filter(function (item) { return item.key === activeCategory; })[0] || TEMPLATES[0];
        current = initial.text;
      }
      template.addEventListener('change', function () {
        var found = TEMPLATES.filter(function (item) { return item.key === template.value; })[0];
        if (found) {
          editor.setValue(found.text);
          activeCategory = found.key;
          drawPalette();
          renderPreview();
        }
      });
    }
    editor.setValue(current);

    function showParams(item) {
      var pOverlay = document.createElement('div');
      pOverlay.className = 'nm-note-overlay nm-mermaid-param-overlay';
      var fields = item.params.map(function (param) {
        if (param.type === 'select') {
          return '<label class="nm-mermaid-param-field"><span>' + param.label + '</span>' +
            '<select data-param="' + param.key + '">' +
            param.options.map(function (opt) {
              return '<option value="' + opt.value + '"' +
                (opt.value === (param.default || '') ? ' selected' : '') + '>' + opt.label + '</option>';
            }).join('') + '</select></label>';
        }
        return '<label class="nm-mermaid-param-field"><span>' + param.label +
          (param.required ? ' *' : '') + '</span><input type="text" data-param="' + param.key +
          '" placeholder="' + (param.placeholder || '') + '" value="' +
          (param.default || '').replace(/"/g, '&quot;') + '"></label>';
      }).join('');
      pOverlay.innerHTML =
        '<div class="nm-note-dialog nm-mermaid-param-dialog" role="dialog" aria-label="插入 Mermaid 元素">' +
          '<div class="nm-note-head"><h2>' + item.label + '</h2>' +
            '<button type="button" class="nm-note-close" aria-label="关闭">×</button></div>' +
          '<div class="nm-mermaid-param-fields">' + fields + '</div>' +
          '<div class="nm-note-error" id="nm-mermaid-param-error" hidden></div>' +
          '<div class="nm-mermaid-param-preview-label">生成的 Mermaid：</div>' +
          '<pre class="nm-mermaid-param-preview" id="nm-mermaid-param-preview"></pre>' +
          '<div class="nm-mermaid-param-preview-label">示意图：</div>' +
          '<div class="nm-mermaid-param-diagram" id="nm-mermaid-param-diagram"></div>' +
          '<div class="nm-note-actions"><button type="button" class="nm-note-cancel">取消</button>' +
            '<button type="button" class="nm-note-primary" id="nm-mermaid-param-insert">插入</button></div>' +
        '</div>';
      document.body.appendChild(pOverlay);
      var error = pOverlay.querySelector('#nm-mermaid-param-error');
      var preview = pOverlay.querySelector('#nm-mermaid-param-preview');
      var diagram = pOverlay.querySelector('#nm-mermaid-param-diagram');
      var diagramSeq = 0;
      var diagramTimer = null;

      function collect() {
        var values = {};
        pOverlay.querySelectorAll('[data-param]').forEach(function (el) {
          values[el.getAttribute('data-param')] = el.value.trim();
        });
        return values;
      }

      function generated() {
        try {
          return item.build ? item.build(collect()) : (item.insert || '');
        } catch (e) {
          return '';
        }
      }

      function renderDiagram(code) {
        var seq = ++diagramSeq;
        clearTimeout(diagramTimer);
        diagramTimer = setTimeout(function () {
          if (!diagram || !code.trim()) {
            if (diagram) diagram.textContent = '示意图';
            return;
          }
          if (typeof window.mermaid === 'undefined') {
            diagram.textContent = code;
            return;
          }
          var heads = {
            flow: 'flowchart TD', sequence: 'sequenceDiagram', class: 'classDiagram',
            state: 'stateDiagram-v2', er: 'erDiagram', gantt: 'gantt',
            pie: 'pie', git: 'gitGraph'
          };
          var source = (heads[activeCategory] || 'flowchart TD') + '\n' + code;
          var id = 'nm-mermaid-param-' + Date.now() + '-' + seq;
          try {
            Promise.resolve(window.mermaid.render(id, source)).then(function (result) {
              if (seq !== diagramSeq) return;
              diagram.innerHTML = (result && (result.svg || result)) || '';
            }).catch(function (err) {
              if (seq !== diagramSeq) return;
              diagram.textContent = '示意图不可用：' + ((err && err.message) || 'Mermaid 语法错误');
            });
          } catch (err) {
            diagram.textContent = '示意图不可用：' + ((err && err.message) || 'Mermaid 语法错误');
          }
        }, 220);
      }

      function update() {
        error.hidden = true;
        var code = generated();
        preview.textContent = code;
        renderDiagram(code);
      }

      var close = function () {
        if (pOverlay.parentNode) pOverlay.parentNode.removeChild(pOverlay);
      };

      pOverlay.querySelector('.nm-note-close').addEventListener('click', close);
      pOverlay.querySelector('.nm-note-cancel').addEventListener('click', close);
      pOverlay.addEventListener('mousedown', function (event) {
        if (event.target === pOverlay) close();
      });
      pOverlay.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') close();
      });
      pOverlay.querySelectorAll('[data-param]').forEach(function (el) {
        el.addEventListener('input', update);
        el.addEventListener('change', update);
      });
      pOverlay.querySelector('#nm-mermaid-param-insert').addEventListener('click', function () {
        var values = collect();
        var missing = item.params.filter(function (param) {
          return param.required && !values[param.key];
        });
        if (missing.length) {
          error.textContent = '请填写：' + missing.map(function (param) { return param.label; }).join('、');
          error.hidden = false;
          return;
        }
        var text = generated();
        if (!text) {
          error.textContent = '无法生成 Mermaid 源码';
          error.hidden = false;
          return;
        }
        editor.insertStandalone(text);
        editor.focus();
        renderPreview();
        close();
      });

      update();
      var first = pOverlay.querySelector('[data-param]');
      if (first) first.focus();
    }

    function drawPalette() {
      var list = overlay.querySelector('#nm-mermaid-palette-list');
      var props = overlay.querySelector('#nm-mermaid-properties');
      if (!list) return;
      var group = PALETTE[activeCategory] || PALETTE.flow;
      list.innerHTML = '<span class="nm-mermaid-palette-title">' + group.label + '元素</span>' +
        group.items.map(function (item, index) {
          return '<button type="button" data-mermaid-palette="' + index + '" title="' +
            (item.params && item.params.length ? '填写参数' : String(item.insert).replace(/"/g, '&quot;')) + '">' +
            item.label + (item.params && item.params.length ? ' …' : '') + '</button>';
        }).join('');
      list.querySelectorAll('[data-mermaid-palette]').forEach(function (button) {
        button.addEventListener('click', function () {
          var idx = parseInt(button.getAttribute('data-mermaid-palette'), 10);
          var item = group.items[idx];
          if (!item) return;
          if (item.params && item.params.length) {
            showParams(item);
          } else if (item.insert) {
            editor.insertStandalone(item.insert);
            editor.focus();
            renderPreview();
          }
        });
      });
      if (!props) return;
      props.innerHTML = '';
      if (activeCategory === 'pie') {
        var label = document.createElement('label');
        label.className = 'nm-mermaid-property';
        var cb = document.createElement('input');
        cb.type = 'checkbox';
        var lines = editor.getValue().split('\n');
        cb.checked = /\bshowData\b/.test(lines[0] || '');
        cb.addEventListener('change', function () {
          setPieShowData(cb.checked);
        });
        label.appendChild(cb);
        label.appendChild(document.createTextNode(' 显示数值（showData）'));
        props.appendChild(label);
        var titleLabel = document.createElement('label');
        titleLabel.className = 'nm-mermaid-property';
        titleLabel.appendChild(document.createTextNode('标题 '));
        var titleInput = document.createElement('input');
        titleInput.type = 'text';
        titleInput.value = pieTitle();
        titleInput.placeholder = '可留空';
        titleInput.addEventListener('change', function () {
          setPieTitle(titleInput.value);
        });
        titleLabel.appendChild(titleInput);
        props.appendChild(titleLabel);
      }
    }

    function pieTitle() {
      var lines = editor.getValue().split('\n');
      if (!/^\s*pie\b/i.test(lines[0] || '')) return '';
      var first = lines[0].match(/\s+title\s+(.+)$/i);
      if (first) return first[1].trim();
      for (var i = 1; i < Math.min(lines.length, 5); i++) {
        var m = lines[i].match(/^\s*title\s+(.+)$/i);
        if (m) return m[1].trim();
      }
      return '';
    }

    function setPieTitle(value) {
      var lines = editor.getValue().split('\n');
      if (!/^\s*pie\b/i.test(lines[0] || '')) return;
      lines[0] = lines[0].replace(/\s+title\s+.+$/i, '').trimEnd();
      for (var i = 1; i < Math.min(lines.length, 5); i++) {
        if (/^\s*title\s+/i.test(lines[i])) {
          lines.splice(i, 1);
          i--;
        }
      }
      if (String(value || '').trim()) lines.splice(1, 0, '    title ' + String(value).trim());
      editor.setValue(lines.join('\n'));
      renderPreview();
    }

    function setPieShowData(on) {
      var lines = editor.getValue().split('\n');
      if (!/^\s*pie\b/i.test(lines[0] || '')) return;
      var first = lines[0].replace(/\s+showData\b/gi, '').trimEnd();
      if (on) {
        var titleMatch = first.match(/\s+title\s+(.+)$/i);
        if (titleMatch) {
          first = first.slice(0, titleMatch.index).trimEnd();
          lines.splice(1, 0, '    title ' + titleMatch[1]);
        }
        first = first.replace(/^(\s*)pie\b/i, '$1pie showData');
      }
      lines[0] = first;
      editor.setValue(lines.join('\n'));
      renderPreview();
    }

    function renderPreview() {
      var text = editor.getValue().trim();
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
      if (event.key === 'Escape' && !event.target.closest('.CodeMirror')) close();
    });
    editor.onChange(function () {
      var detected = detectCategory(editor.getValue());
      if (detected !== activeCategory) {
        activeCategory = detected;
        drawPalette();
      }
      clearTimeout(timer);
      timer = setTimeout(renderPreview, 260);
    });

    function apply() {
      var text = editor.getValue().trim();
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
    }

    overlay.querySelector('#nm-mermaid-apply').addEventListener('click', apply);
    if (editor.raw) {
      editor.raw.__nmMermaidEditor = {
        close: close,
        apply: apply,
        message: function (text) {
          error.textContent = text;
          error.hidden = false;
        }
      };
    }

    drawPalette();
    renderPreview();
    editor.focus();
    editor.select();
  }

  document.addEventListener('DOMContentLoaded', ensureButton);
  window.nmMermaidEditor = {
    findBlock: findBlock,
    sourceFor: sourceFor,
    templates: TEMPLATES,
    palette: PALETTE
  };
})();
