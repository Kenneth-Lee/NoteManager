"""Structured Web page for editing ~/.NoteManager/config.json.

The form is the primary interface; the raw JSON editor is kept under
"高级" for repair/unknown fields.  Saving never applies the config to the
running daemon; it only writes the file (restart required).
"""

from __future__ import annotations


def render() -> str:
    return r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>配置文件编辑器 - NoteManager</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f5f5f5; color: #333; line-height: 1.6;
}
.wrap { max-width: 980px; margin: 0 auto; padding: 20px 20px 60px; }
.header { display: flex; align-items: baseline; gap: 16px; margin-bottom: 8px; }
.header h1 { font-size: 24px; }
.header a { color: #0066cc; text-decoration: none; font-size: 13px; }
.header a:hover { text-decoration: underline; }
.path { font-family: 'SFMono-Regular', Consolas, monospace; font-size: 12px; color: #888;
        overflow-wrap: anywhere; margin-bottom: 6px; }
.runtime { font-family: 'SFMono-Regular', Consolas, monospace; font-size: 12px; color: #555;
           background: #fff; border: 1px solid #e3e3e3; border-radius: 4px; padding: 6px 10px;
           margin-bottom: 12px; overflow-wrap: anywhere; }
.notice { background: #fff8e1; border: 1px solid #ffe082; color: #6d4c00;
          border-radius: 6px; padding: 10px 12px; font-size: 13px; margin-bottom: 12px; }
.notice code { background: #fff3cd; padding: 1px 4px; border-radius: 3px; }
.pending-config { background: #fff4e5; border: 1px solid #ffcc80; color: #6d4c00;
                  border-radius: 6px; padding: 10px 12px; font-size: 13px; margin-bottom: 12px; }
.pending-config.error { background: #fbe9e9; border-color: #e0a0a0; color: #8f1d1d; }
.card { background: #fff; border-radius: 8px; padding: 16px; margin-bottom: 14px;
        box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.card h2 { font-size: 16px; margin-bottom: 12px; }
.badge { display: inline-block; font-size: 11px; background: #fff3e0; color: #e65100;
         border: 1px solid #ffcc80; border-radius: 8px; padding: 1px 6px; margin-left: 6px;
         font-weight: 500; vertical-align: middle; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px; }
.field { margin-bottom: 12px; }
.field label { display: block; font-size: 12px; color: #666; margin-bottom: 4px; }
.field input[type=text], .field input[type=number], .field select {
    width: 100%; padding: 7px 9px; border: 1px solid #ccc; border-radius: 4px;
    font-size: 13px; font-family: inherit; background: #fff; color: #222;
}
.field input:focus, .field select:focus { outline: none; border-color: #0066cc; }
.hint { font-size: 12px; color: #888; margin-top: 4px; }
.dir-row { display: flex; gap: 8px; margin-bottom: 6px; }
.dir-row input { flex: 1; }
button { padding: 7px 14px; border: 1px solid #0066cc; border-radius: 4px; cursor: pointer;
         font-size: 13px; background: #0066cc; color: #fff; font-family: inherit; }
button:hover { background: #0052a3; }
button.secondary { background: #e3f2fd; color: #1565c0; border-color: #bbdefb; }
button.secondary:hover { background: #bbdefb; }
button.danger { background: #fff; color: #b00020; border-color: #e0a0a0; }
button.danger:hover { background: #fbe9e9; }
button:disabled { opacity: .5; cursor: default; }
.toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
#dirty { font-size: 12px; color: #888; margin-left: auto; }
.status { margin-top: 10px; font-size: 13px; min-height: 1.4em; }
.status.error { color: #b00020; }
.status.ok { color: #1b7f3a; }
.messages { margin-top: 8px; }
.messages .warn { background: #fff4e5; border: 1px solid #ffcc80; color: #7a4b00;
                  border-radius: 4px; padding: 7px 10px; font-size: 13px; margin-top: 6px; }
.messages .info { background: #e8f5e9; border: 1px solid #a5d6a7; color: #1b5e20;
                  border-radius: 4px; padding: 7px 10px; font-size: 13px; margin-top: 6px; }
.messages a { color: #0066cc; }
details.advanced { background: #fff; border-radius: 8px; padding: 12px 16px;
                   box-shadow: 0 1px 3px rgba(0,0,0,.08); }
details.advanced summary { cursor: pointer; font-weight: 600; font-size: 14px; }
details.advanced .body { margin-top: 12px; }
textarea {
    width: 100%; min-height: 42vh; resize: vertical; padding: 12px;
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
    font-size: 13px; line-height: 1.55; border: 1px solid #ddd; border-radius: 4px;
    background: #fcfcfc; color: #222; tab-size: 2; white-space: pre; overflow: auto;
}
@media (max-width: 700px) {
    .grid { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <h1>配置文件编辑器</h1>
    <a href="/">返回控制台</a>
  </div>
  <div class="path" id="config-path">~/.NoteManager/config.json</div>
  <div class="runtime" id="runtime">加载中…</div>
  <div class="notice">
    保存后不会立即生效。执行 <code>nmr start --restart</code> 重启 daemon 后生效；
    重启时显式传入的 <code>--port</code> / <code>--web-port</code> 会覆盖文件中的值。
    如果修改了 Web 端口，重启后请访问新地址。
  </div>
  <div id="pending-config" class="pending-config" style="display:none"></div>

  <div class="card">
    <h2>工程</h2>
    <div class="field">
      <label>监控目录</label>
      <div id="dir-list"></div>
      <button id="add-dir" class="secondary" type="button">添加目录</button>
      <div class="hint">目录会按顺序恢复监控；不存在的目录启动时会被跳过。</div>
    </div>
    <div class="field">
      <label>默认工程</label>
      <select id="default-project"></select>
    </div>
  </div>

  <div class="card">
    <h2>网络<span class="badge">修改后需要重启</span></h2>
    <div class="grid">
      <div class="field">
        <label>TCP 端口</label>
        <input id="port" type="number" min="1" max="65535" placeholder="默认 19832">
      </div>
      <div class="field">
        <label>Web 端口</label>
        <input id="web-port" type="number" min="1" max="65535" placeholder="默认 19833">
      </div>
      <div class="field">
        <label>传输方式</label>
        <select id="transport">
          <option value="">默认（POSIX unix / Windows tcp）</option>
          <option value="unix">unix</option>
          <option value="tcp">tcp</option>
        </select>
      </div>
      <div class="field">
        <label>Socket 路径</label>
        <input id="socket-path" type="text" placeholder="~/.NoteManager/daemon.sock">
        <div class="hint">transport=unix 时使用；transport=tcp 时忽略。</div>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>外部编辑器</h2>
    <div class="grid">
      <div class="field">
        <label>模式</label>
        <select id="editor-mode">
          <option value="default">默认（gvim / MacVim）</option>
          <option value="custom">自定义命令</option>
        </select>
      </div>
      <div class="field" id="editor-custom-wrap">
        <label>命令</label>
        <input id="editor-command" type="text" placeholder="gvim {file}">
        <div class="hint">支持 {file} / {dir}；直接以 argv 执行，不经过 shell。</div>
      </div>
    </div>
  </div>

  <div class="toolbar">
    <button id="form-save" type="button">保存</button>
    <button id="form-reload" class="secondary" type="button">重新加载</button>
    <span id="dirty">已加载</span>
  </div>
  <div id="status" class="status"></div>
  <div id="messages" class="messages"></div>

  <details class="advanced" id="advanced">
    <summary>高级：原始 JSON</summary>
    <div class="body">
      <p class="hint">
        表单保存时会保留未知字段。原始 JSON 与表单不会实时双向同步；
        如需直接编辑完整文件，可在这里修改并保存。
      </p>
      <div class="toolbar">
        <button id="raw-save" type="button">保存原始 JSON</button>
        <button id="raw-reload" class="secondary" type="button">重新加载</button>
        <button id="raw-format" class="secondary" type="button">格式化</button>
        <button id="raw-backup" class="secondary" type="button">加载上一版</button>
      </div>
      <textarea id="raw-editor" spellcheck="false" autocomplete="off" autocapitalize="off"></textarea>
    </div>
  </details>
</div>
<script>
(function () {
  var pathEl = document.getElementById('config-path');
  var pendingEl = document.getElementById('pending-config');
  var runtimeEl = document.getElementById('runtime');
  var dirList = document.getElementById('dir-list');
  var addDirBtn = document.getElementById('add-dir');
  var defaultSel = document.getElementById('default-project');
  var portEl = document.getElementById('port');
  var webPortEl = document.getElementById('web-port');
  var transportSel = document.getElementById('transport');
  var socketEl = document.getElementById('socket-path');
  var editorMode = document.getElementById('editor-mode');
  var editorCommand = document.getElementById('editor-command');
  var editorCustomWrap = document.getElementById('editor-custom-wrap');
  var formSaveBtn = document.getElementById('form-save');
  var formReloadBtn = document.getElementById('form-reload');
  var statusEl = document.getElementById('status');
  var messagesEl = document.getElementById('messages');
  var dirtyEl = document.getElementById('dirty');
  var advanced = document.getElementById('advanced');
  var rawEditor = document.getElementById('raw-editor');
  var rawSaveBtn = document.getElementById('raw-save');
  var rawReloadBtn = document.getElementById('raw-reload');
  var rawFormatBtn = document.getElementById('raw-format');
  var rawBackupBtn = document.getElementById('raw-backup');

  var state = {
    baseSha: null,
    baseData: {},
    formDirty: false,
    rawDirty: false,
    saving: false,
    editorOriginal: null,
    editorInitialText: ''
  };

  function setStatus(msg, kind) {
    statusEl.textContent = msg || '';
    statusEl.className = 'status' + (kind ? ' ' + kind : '');
  }

  function isDirty() {
    return state.formDirty || state.rawDirty;
  }

  function refreshDirtyLabel() {
    dirtyEl.textContent = isDirty() ? '有未保存修改' : '已保存';
  }

  var PENDING_LABELS = {
    port: 'TCP 端口',
    web_port: 'Web 端口',
    transport: '传输方式',
    socket_path: 'Socket 路径',
    directories: '监控目录',
    default_project: '默认工程',
    editor: '外部编辑器'
  };

  function renderPending(d) {
    if (!pendingEl) return;
    if (d && d.pending_error) {
      pendingEl.className = 'pending-config error';
      pendingEl.textContent = '配置文件无法解析：' + d.pending_error + '；请修复后再重启。';
      pendingEl.style.display = '';
      return;
    }
    var fields = (d && d.pending_fields) || [];
    if (!fields.length) {
      pendingEl.style.display = 'none';
      pendingEl.textContent = '';
      return;
    }
    var labels = fields.map(function (f) { return PENDING_LABELS[f] || f; });
    pendingEl.className = 'pending-config';
    pendingEl.textContent = '配置文件与当前运行实例不一致：' + labels.join('、') +
      '。运行实例尚未重启，这些修改不会生效。执行 nmr start --restart 后生效。';
    pendingEl.style.display = '';
  }

  function renderRuntime(rt) {
    rt = rt || {};
    var parts = [];
    if (rt.pid !== undefined) parts.push('PID ' + rt.pid);
    if (rt.port !== undefined) parts.push('TCP ' + rt.port);
    if (rt.web_port !== undefined) parts.push('Web ' + rt.web_port);
    if (rt.transport) parts.push(rt.transport);
    if (rt.socket_path) parts.push(rt.socket_path);
    runtimeEl.textContent = parts.length ? parts.join('  |  ') : '运行时信息不可用';
  }

  function clearMessages() {
    messagesEl.innerHTML = '';
  }

  function addMessage(text, cls) {
    var div = document.createElement('div');
    div.className = cls || 'warn';
    div.textContent = text;
    messagesEl.appendChild(div);
    return div;
  }

  function renderMessages(d) {
    clearMessages();
    var warnings = (d && d.warnings) || [];
    for (var i = 0; i < warnings.length; i++) addMessage(warnings[i], 'warn');
    if (d && d.restart_required) {
      addMessage('已保存。修改需要重启 daemon 后生效：nmr start --restart', 'info');
    }
    if (d && d.saved && d.runtime) {
      var savedWeb = d.saved.web_port;
      var runtimeWeb = d.runtime.web_port;
      if (typeof savedWeb === 'number' && savedWeb !== runtimeWeb) {
        var msg = addMessage('Web 端口已改为 ' + savedWeb + '；重启后请访问：', 'warn');
        var a = document.createElement('a');
        a.href = 'http://localhost:' + savedWeb + '/';
        a.target = '_blank';
        a.rel = 'noopener';
        a.textContent = 'http://localhost:' + savedWeb + '/';
        msg.appendChild(a);
      }
    }
  }

  function shellQuote(value) {
    var s = String(value);
    if (/^[A-Za-z0-9_@%+=:,./-]+$/.test(s)) return s;
    return "'" + s.replace(/'/g, "'\\''") + "'";
  }

  function editorTextFromValue(value) {
    if (Array.isArray(value)) return value.map(shellQuote).join(' ');
    if (typeof value === 'string') return value;
    return '';
  }

  function editorValueFromText(text) {
    if (Array.isArray(state.editorOriginal) && text === state.editorInitialText) {
      return state.editorOriginal;
    }
    return text;
  }

  function getDirs() {
    var out = [];
    var seen = {};
    var inputs = dirList.querySelectorAll('input.dir-input');
    for (var i = 0; i < inputs.length; i++) {
      var v = (inputs[i].value || '').trim();
      if (!v || seen[v]) continue;
      seen[v] = true;
      out.push(v);
    }
    return out;
  }

  function updateDefaultOptions(preferred) {
    var current = preferred !== undefined ? preferred : defaultSel.value;
    var dirs = getDirs();
    defaultSel.innerHTML = '';
    var empty = document.createElement('option');
    empty.value = '';
    empty.textContent = '(未设置)';
    defaultSel.appendChild(empty);
    for (var i = 0; i < dirs.length; i++) {
      var opt = document.createElement('option');
      opt.value = dirs[i];
      opt.textContent = dirs[i];
      defaultSel.appendChild(opt);
    }
    if (current && dirs.indexOf(current) < 0) {
      var extra = document.createElement('option');
      extra.value = current;
      extra.textContent = current + '（不在目录列表中）';
      defaultSel.appendChild(extra);
    }
    defaultSel.value = current || '';
    if (defaultSel.value !== (current || '')) defaultSel.value = '';
  }

  function addDirRow(value) {
    var row = document.createElement('div');
    row.className = 'dir-row';
    var input = document.createElement('input');
    input.type = 'text';
    input.className = 'dir-input';
    input.placeholder = '/path/to/notes';
    input.value = value || '';
    var remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'danger';
    remove.textContent = '移除';
    input.addEventListener('input', function () {
      state.formDirty = true;
      refreshDirtyLabel();
      syncRawFromFormQuiet();
    });
    input.addEventListener('change', function () {
      updateDefaultOptions();
    });
    remove.addEventListener('click', function () {
      row.remove();
      state.formDirty = true;
      refreshDirtyLabel();
      updateDefaultOptions();
      syncRawFromFormQuiet();
    });
    row.appendChild(input);
    row.appendChild(remove);
    dirList.appendChild(row);
  }

  function renderDirs(dirs) {
    dirList.innerHTML = '';
    for (var i = 0; i < (dirs || []).length; i++) addDirRow(dirs[i]);
  }

  function readPort(el, label) {
    var raw = (el.value || '').trim();
    if (!raw) return undefined;
    var n = Number(raw);
    if (!Number.isInteger(n) || n < 1 || n > 65535) {
      throw new Error(label + ' 必须是 1–65535 的整数');
    }
    return n;
  }

  function buildConfig() {
    var data = JSON.parse(JSON.stringify(state.baseData || {}));
    delete data.directories;
    delete data.default_project;
    delete data.port;
    delete data.web_port;
    delete data.transport;
    delete data.socket_path;
    delete data.editor;

    data.directories = getDirs();
    if (defaultSel.value) data.default_project = defaultSel.value;

    var p = readPort(portEl, 'TCP 端口');
    var w = readPort(webPortEl, 'Web 端口');
    if (p !== undefined && w !== undefined && p === w) {
      throw new Error('TCP 端口与 Web 端口不能相同');
    }
    if (p !== undefined) data.port = p;
    if (w !== undefined) data.web_port = w;

    var transport = transportSel.value;
    if (transport) data.transport = transport;
    var socketPath = (socketEl.value || '').trim();
    if (socketPath && transport !== 'tcp') data.socket_path = socketPath;

    if (editorMode.value === 'custom') {
      var cmd = (editorCommand.value || '').trim();
      if (!cmd) throw new Error('自定义编辑器命令不能为空');
      data.editor = editorValueFromText(cmd);
    }
    return data;
  }

  function configTextFromForm() {
    return JSON.stringify(buildConfig(), null, 2) + '\n';
  }

  function syncRawFromFormQuiet() {
    if (state.rawDirty) return;
    try {
      rawEditor.value = configTextFromForm();
    } catch (e) {}
  }

  function populateForm(data) {
    state.baseData = data || {};
    state.editorOriginal = data ? data.editor : null;
    state.editorInitialText = editorTextFromValue(state.editorOriginal);

    renderDirs(Array.isArray(data.directories) ? data.directories : []);
    updateDefaultOptions(data.default_project || '');
    portEl.value = (data.port !== undefined && data.port !== null) ? data.port : '';
    webPortEl.value = (data.web_port !== undefined && data.web_port !== null) ? data.web_port : '';
    transportSel.value = data.transport || '';
    socketEl.value = data.socket_path || '';
    editorCommand.value = state.editorInitialText;
    editorMode.value = (data.editor === undefined || data.editor === null || data.editor === '')
      ? 'default' : 'custom';
    toggleEditorCustom();
    socketEl.disabled = transportSel.value === 'tcp';
  }

  function toggleEditorCustom() {
    editorCustomWrap.style.display = editorMode.value === 'custom' ? '' : 'none';
  }

  function setSavingState() {
    formSaveBtn.disabled = state.saving;
    rawSaveBtn.disabled = state.saving;
  }

  function sendSave(text, force) {
    return fetch('/api/config_file', {
      method: 'POST',
      headers: {'Content-Type': 'application/json; charset=utf-8'},
      body: JSON.stringify({text: text, base_sha256: state.baseSha, force: !!force})
    }).then(function (r) {
      return r.json().then(function (d) { return {status: r.status, data: d}; });
    });
  }

  function saveText(text, onSuccess) {
    if (state.saving) return;
    state.saving = true;
    setSavingState();
    setStatus('保存中…', '');
    sendSave(text, false)
      .then(function (res) {
        var d = res.data || {};
        if (!d.ok && d.conflict) {
          if (window.confirm('配置文件已被外部修改，是否用当前内容覆盖？')) {
            return sendSave(text, true);
          }
          return res;
        }
        return res;
      })
      .then(function (res) {
        var d = res.data || {};
        if (!d.ok) throw new Error(d.error || '保存失败');
        state.baseSha = d.sha256 || state.baseSha;
        renderRuntime(d.runtime || {});
        renderMessages(d);
        renderPending(d);
        onSuccess(d);
        setStatus(d.changed ? '已保存。修改需要重启 daemon 后生效。' : '内容没有变化。', 'ok');
      })
      .catch(function (e) {
        setStatus(e && e.message ? e.message : '保存失败', 'error');
      })
      .then(function () {
        state.saving = false;
        setSavingState();
      }, function () {
        state.saving = false;
        setSavingState();
      });
  }

  function saveForm() {
    if (state.rawDirty && !window.confirm('原始 JSON 有未保存修改，保存表单会覆盖它，继续？')) return;
    var data;
    try {
      data = buildConfig();
    } catch (e) {
      setStatus(e.message || '表单内容无效', 'error');
      return;
    }
    var text = JSON.stringify(data, null, 2) + '\n';
    saveText(text, function () {
      state.baseData = data;
      state.editorOriginal = data.editor;
      state.editorInitialText = editorTextFromValue(data.editor);
      state.formDirty = false;
      state.rawDirty = false;
      rawEditor.value = text;
      refreshDirtyLabel();
    });
  }

  function saveRaw() {
    if (state.formDirty && !window.confirm('表单有未保存修改，保存原始 JSON 会覆盖表单内容，继续？')) return;
    var data;
    try {
      data = JSON.parse(rawEditor.value);
    } catch (e) {
      setStatus('JSON 错误：' + (e && e.message ? e.message : '无法解析'), 'error');
      return;
    }
    if (data === null || Array.isArray(data) || typeof data !== 'object') {
      setStatus('顶层必须是 JSON 对象', 'error');
      return;
    }
    var text = rawEditor.value;
    saveText(text, function () {
      formSaveBtn.disabled = false;
      populateForm(data);
      state.formDirty = false;
      state.rawDirty = false;
      refreshDirtyLabel();
    });
  }

  function load(source) {
    if (isDirty() && !window.confirm('有未保存修改，确定重新加载？')) return;
    setStatus('加载中…', '');
    clearMessages();
    var url = '/api/config_file' + (source === 'backup' ? '?source=backup' : '');
    fetch(url, {cache: 'no-store'})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) throw new Error(d.error || '加载失败');
        renderPending(d);
        if (source === 'backup' && !d.exists) {
          setStatus('没有可用的上一版备份', 'error');
          return;
        }
        pathEl.textContent = d.path || '';
        renderRuntime(d.runtime || {});
        rawEditor.value = d.text || '';
        if (source === 'backup') {
          advanced.open = true;
          state.formDirty = false;
          state.rawDirty = true;
          refreshDirtyLabel();
          setStatus('已加载上一版内容（尚未保存）', '');
          return;
        }
        state.baseSha = d.sha256 || null;
        var data;
        try {
          data = JSON.parse(d.text || '{}');
        } catch (e) {
          formSaveBtn.disabled = true;
          state.baseData = {};
          state.formDirty = false;
          state.rawDirty = true;
          advanced.open = true;
          refreshDirtyLabel();
          setStatus('配置 JSON 无效，请使用高级原始 JSON 修复', 'error');
          return;
        }
        if (data === null || Array.isArray(data) || typeof data !== 'object') {
          formSaveBtn.disabled = true;
          state.baseData = {};
          state.formDirty = false;
          state.rawDirty = true;
          advanced.open = true;
          refreshDirtyLabel();
          setStatus('配置文件顶层必须是 JSON 对象，请使用高级原始 JSON 修复', 'error');
          return;
        }
        formSaveBtn.disabled = false;
        populateForm(data);
        state.formDirty = false;
        state.rawDirty = false;
        refreshDirtyLabel();
        setStatus('已加载', 'ok');
      })
      .catch(function (e) {
        setStatus(e && e.message ? e.message : '加载失败', 'error');
      });
  }

  [portEl, webPortEl, transportSel, socketEl, editorMode, editorCommand].forEach(function (el) {
    el.addEventListener('input', function () {
      state.formDirty = true;
      refreshDirtyLabel();
      syncRawFromFormQuiet();
    });
    el.addEventListener('change', function () {
      state.formDirty = true;
      refreshDirtyLabel();
      if (el === transportSel) {
        socketEl.disabled = transportSel.value === 'tcp';
      }
    });
  });
  defaultSel.addEventListener('change', function () {
    state.formDirty = true;
    refreshDirtyLabel();
    syncRawFromFormQuiet();
  });
  editorMode.addEventListener('change', function () {
    toggleEditorCustom();
    state.formDirty = true;
    refreshDirtyLabel();
    syncRawFromFormQuiet();
  });
  addDirBtn.addEventListener('click', function () {
    addDirRow('');
    state.formDirty = true;
    refreshDirtyLabel();
    updateDefaultOptions();
    syncRawFromFormQuiet();
  });
  formSaveBtn.addEventListener('click', saveForm);
  formReloadBtn.addEventListener('click', function () { load('config'); });
  rawSaveBtn.addEventListener('click', saveRaw);
  rawReloadBtn.addEventListener('click', function () { load('config'); });
  rawBackupBtn.addEventListener('click', function () { load('backup'); });
  rawFormatBtn.addEventListener('click', function () {
    var data;
    try {
      data = JSON.parse(rawEditor.value);
    } catch (e) {
      setStatus('JSON 错误：' + (e && e.message ? e.message : '无法解析'), 'error');
      return;
    }
    if (data === null || Array.isArray(data) || typeof data !== 'object') {
      setStatus('顶层必须是 JSON 对象', 'error');
      return;
    }
    rawEditor.value = JSON.stringify(data, null, 2) + '\n';
    state.rawDirty = true;
    refreshDirtyLabel();
    setStatus('已格式化（尚未保存）', '');
  });
  rawEditor.addEventListener('input', function () {
    state.rawDirty = true;
    refreshDirtyLabel();
  });
  advanced.addEventListener('toggle', function () {
    if (advanced.open && state.formDirty && !state.rawDirty) {
      syncRawFromFormQuiet();
    }
  });
  window.addEventListener('beforeunload', function (e) {
    if (!isDirty()) return;
    e.preventDefault();
    e.returnValue = '';
  });
  // A saved config change may only exist on disk; refresh when the browser
  // restores this page from the back/forward cache.
  window.addEventListener('pageshow', function (e) {
    if (e.persisted) load('config');
  });

  load('config');
})();
</script>
</body>
</html>
"""
