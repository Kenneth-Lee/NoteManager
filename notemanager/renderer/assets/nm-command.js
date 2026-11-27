/* Ctrl/Cmd+K command palette + saved search views. */
(function () {
    'use strict';

    var body = document.body;
    if (!body) return;
    var project = body.getAttribute('data-nm-project') || '';
    if (!project) return;
    var prefix = window.NM_PREFIX || '';
    var VIEW_KEY = 'nm-views:' + project;
    var TYPE_LABELS = {
        chapter: '章节', label: '标签', index: '索引', dtag: '标记',
        grep: '正文', fname: '文件名'
    };

    var views = loadViews();
    var overlay = null;
    var helpOverlay = null;
    var input = null;
    var list = null;
    var selected = 0;
    var items = [];
    var searchData = null;
    var searchPromise = null;
    var searchTimer = null;
    var queryId = 0;

    function loadViews() {
        try {
            var raw = localStorage.getItem(VIEW_KEY);
            var data = raw ? JSON.parse(raw) : [];
            return Array.isArray(data) ? data.filter(function (v) {
                return v && v.name && v.query;
            }) : [];
        } catch (e) {
            return [];
        }
    }

    function saveViews() {
        try { localStorage.setItem(VIEW_KEY, JSON.stringify(views)); } catch (e) {}
    }

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function parseQuery(query) {
        var match = String(query || '').trim().match(
            /^(chapter|label|index|dtag|fname|grep):\s*(.*)$/i
        );
        if (match) return {type: match[1].toLowerCase(), query: (match[2] || '').trim()};
        return {type: null, query: String(query || '').trim()};
    }

    function fetchIndex() {
        if (searchData) return Promise.resolve(searchData);
        if (searchPromise) return searchPromise;
        searchPromise = fetch(prefix + '_index/search_index.json')
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then(function (d) {
                searchData = d;
                return d;
            })
            .catch(function (e) {
                searchPromise = null;
                throw e;
            });
        return searchPromise;
    }

    function searchDocs(query) {
        if (!searchData || !searchData.documents) return [];
        var parsed = parseQuery(query);
        if (!parsed.query) return [];
        var re;
        try {
            re = new RegExp(parsed.query, 'i');
        } catch (e) {
            re = new RegExp(parsed.query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i');
        }
        var results = [];
        var docs = searchData.documents;
        for (var i = 0; i < docs.length; i++) {
            var doc = docs[i];
            if (!parsed.type && doc.type === 'grep') continue;
            if (parsed.type && doc.type !== parsed.type) continue;
            re.lastIndex = 0;
            if (re.test(doc.title || '') || re.test(doc.text || '') ||
                    re.test(doc.snippet || '')) {
                results.push(doc);
                if (results.length >= 8) break;
            }
        }
        return results;
    }

    function openSearch(query) {
        if (window.nmRunSearch && window.nmRunSearch(query)) {
            closePalette();
            return;
        }
        var url = window.location.pathname + '?q=' + encodeURIComponent(query);
        window.location.href = url;
    }

    function saveView(query) {
        query = String(query || '').trim();
        if (!query) return;
        var name = window.prompt('视图名称', query);
        if (name === null) return;
        name = String(name || '').trim() || query;
        var found = null;
        views.forEach(function (v) { if (v.name === name) found = v; });
        if (found) {
            found.query = query;
        } else {
            views.push({name: name, query: query});
        }
        saveViews();
        renderHomeViews();
        closePalette();
    }

    function deleteView(name) {
        views = views.filter(function (v) { return v.name !== name; });
        saveViews();
        renderHomeViews();
        if (overlay && !overlay.hidden) renderPalette(input.value, false);
    }

    function renderHomeViews() {
        var host = document.getElementById('nm-saved-views');
        if (!host) return;
        host.innerHTML = '';
        var head = document.createElement('div');
        head.className = 'nm-saved-views-heading';
        var h2 = document.createElement('h2');
        h2.textContent = '快捷视图';
        head.appendChild(h2);
        var hint = document.createElement('span');
        hint.className = 'nm-saved-views-hint';
        hint.textContent = 'Ctrl/⌘ K 搜索后可保存';
        head.appendChild(hint);
        host.appendChild(head);
        if (!views.length) {
            var empty = document.createElement('p');
            empty.className = 'nm-saved-views-empty';
            empty.textContent = '还没有保存的搜索视图。';
            host.appendChild(empty);
            return;
        }
        var listEl = document.createElement('div');
        listEl.className = 'nm-saved-view-list';
        views.forEach(function (view) {
            var chip = document.createElement('span');
            chip.className = 'nm-saved-view';
            var a = document.createElement('a');
            a.href = '?q=' + encodeURIComponent(view.query);
            a.textContent = view.name;
            a.title = view.query;
            chip.appendChild(a);
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'nm-saved-view-del';
            btn.textContent = '×';
            btn.title = '删除视图';
            btn.addEventListener('click', function (event) {
                event.preventDefault();
                event.stopPropagation();
                deleteView(view.name);
            });
            chip.appendChild(btn);
            listEl.appendChild(chip);
        });
        host.appendChild(listEl);
    }

    function makeItems(query) {
        var q = String(query || '').trim();
        var out = [];
        if (!q) {
            views.forEach(function (view) {
                out.push({
                    kind: 'view', title: '视图：' + view.name,
                    detail: view.query, view: view
                });
            });
            out.push({kind: 'action', title: '回到项目首页', detail: 'Home', action: 'home'});
            out.push({kind: 'action', title: '返回控制台', detail: '/', action: 'console'});
            out.push({kind: 'action', title: '复制当前页面链接', detail: location.href, action: 'copy'});
            out.push({kind: 'help', title: '快捷键帮助', detail: 'Ctrl/⌘ K · ↑/↓ · Enter · Esc'});
            return out;
        }
        searchDocs(q).forEach(function (doc) {
            out.push({
                kind: 'doc', title: doc.title || doc.path,
                detail: (TYPE_LABELS[doc.type] || doc.type) + ' · ' + (doc.path || ''),
                path: doc.path
            });
        });
        views.forEach(function (view) {
            if (view.name.toLowerCase().indexOf(q.toLowerCase()) >= 0 ||
                    view.query.toLowerCase().indexOf(q.toLowerCase()) >= 0) {
                out.push({
                    kind: 'view', title: '视图：' + view.name,
                    detail: view.query, view: view
                });
            }
        });
        out.push({
            kind: 'save', title: '保存查询为视图',
            detail: '“' + q + '”', query: q
        });
        return out;
    }

    function renderPalette(query, resetSelection) {
        if (!list) return;
        items = makeItems(query);
        if (resetSelection) selected = 0;
        if (selected >= items.length) selected = Math.max(0, items.length - 1);
        list.innerHTML = '';
        if (!items.length) {
            var empty = document.createElement('div');
            empty.className = 'nm-cmd-empty';
            empty.textContent = '没有匹配项';
            list.appendChild(empty);
            return;
        }
        items.forEach(function (item, index) {
            var row = document.createElement('div');
            row.className = 'nm-cmd-item' + (index === selected ? ' selected' : '');
            row.dataset.index = String(index);
            var title = document.createElement('div');
            title.className = 'nm-cmd-title';
            title.textContent = item.title;
            row.appendChild(title);
            var detail = document.createElement('div');
            detail.className = 'nm-cmd-detail';
            detail.textContent = item.detail || '';
            row.appendChild(detail);
            row.addEventListener('mousedown', function (event) {
                event.preventDefault();
                selected = index;
                executeSelected();
            });
            list.appendChild(row);
        });
        var sel = list.querySelector('.nm-cmd-item.selected');
        if (sel) sel.scrollIntoView({block: 'nearest'});
    }

    function executeSelected() {
        var item = items[selected];
        if (!item) return;
        if (item.kind === 'doc') {
            window.location.href = prefix + item.path;
        } else if (item.kind === 'view') {
            openSearch(item.view.query);
        } else if (item.kind === 'save') {
            saveView(item.query);
        } else if (item.kind === 'help') {
            openHelp();
        } else if (item.kind === 'action') {
            if (item.action === 'home') window.location.href = prefix + 'index.html';
            else if (item.action === 'console') window.location.href = '/';
            else if (item.action === 'copy') {
                if (navigator.clipboard) navigator.clipboard.writeText(location.href);
                closePalette();
            }
        }
    }

    function ensureHelp() {
        if (helpOverlay) return;
        helpOverlay = document.createElement('div');
        helpOverlay.className = 'nm-help-overlay';
        helpOverlay.hidden = true;
        helpOverlay.innerHTML = '<div class="nm-help-dialog" role="dialog" aria-label="快捷键帮助">' +
            '<div class="nm-help-head"><h2>键盘快捷键</h2>' +
            '<button type="button" class="nm-help-close" aria-label="关闭">×</button></div>' +
            '<dl class="nm-help-list">' +
            '<div><dt><kbd>Ctrl/⌘ K</kbd></dt><dd>打开/关闭命令面板</dd></div>' +
            '<div><dt><kbd>↑</kbd> <kbd>↓</kbd></dt><dd>在命令面板中选择</dd></div>' +
            '<div><dt><kbd>Enter</kbd></dt><dd>打开选中的文档或执行命令</dd></div>' +
            '<div><dt><kbd>Esc</kbd></dt><dd>关闭命令面板或本帮助</dd></div>' +
            '<div><dt><kbd>Ctrl/⌘+Alt+1/2/3</kbd></dt><dd>切换到渲染 / 源文件 / 诊断</dd></div>' +
            '<div><dt><kbd>Ctrl/⌘+Alt+S</kbd></dt><dd>切换分屏（文档页）</dd></div>' +
            '<div><dt><kbd>Ctrl/⌘+Alt+E</kbd></dt><dd>用外部编辑器打开当前源文件</dd></div>' +
            '<div><dt><kbd>Alt+↑ / Ctrl/⌘+Alt+U</kbd></dt><dd>回到项目主页并定位当前目录</dd></div>' +
            '</dl>' +
            '<p class="nm-help-note">命令面板可以搜索文档、打开页面、保存搜索视图。' +
            '搜索框右侧的 <b>?</b> 可以查看搜索语法。</p>' +
            '</div>';
        document.body.appendChild(helpOverlay);
        helpOverlay.addEventListener('mousedown', function (event) {
            if (event.target === helpOverlay) closeHelp();
        });
        var closeBtn = helpOverlay.querySelector('.nm-help-close');
        closeBtn.addEventListener('click', closeHelp);
    }

    function openHelp() {
        ensureHelp();
        closePalette();
        helpOverlay.hidden = false;
        document.body.classList.add('nm-cmd-open');
    }

    function closeHelp() {
        if (!helpOverlay) return;
        helpOverlay.hidden = true;
        if (!overlay || overlay.hidden) document.body.classList.remove('nm-cmd-open');
    }

    function ensurePalette() {
        if (overlay) return;
        overlay = document.createElement('div');
        overlay.className = 'nm-cmd-overlay';
        overlay.hidden = true;
        overlay.innerHTML = '<div class="nm-cmd-dialog" role="dialog" aria-label="命令面板">' +
            '<input id="nm-cmd-input" type="text" autocomplete="off" spellcheck="false" ' +
            'placeholder="搜索文档，或输入命令…">' +
            '<div id="nm-cmd-list" class="nm-cmd-list"></div>' +
            '<div class="nm-cmd-hint">↑↓ 选择 · Enter 打开 · Esc 关闭 · 搜索后可保存视图</div>' +
            '</div>';
        document.body.appendChild(overlay);
        input = document.getElementById('nm-cmd-input');
        list = document.getElementById('nm-cmd-list');
        overlay.addEventListener('mousedown', function (event) {
            if (event.target === overlay) closePalette();
        });
        input.addEventListener('input', function () {
            var q = input.value;
            clearTimeout(searchTimer);
            var id = ++queryId;
            renderPalette(q, true);
            if (!q.trim()) return;
            searchTimer = setTimeout(function () {
                fetchIndex().then(function () {
                    if (id === queryId) renderPalette(input.value, true);
                }).catch(function () {
                    if (id === queryId) renderPalette(input.value, true);
                });
            }, 80);
        });
        input.addEventListener('keydown', function (event) {
            if (event.key === 'ArrowDown') {
                event.preventDefault();
                if (items.length) {
                    selected = (selected + 1) % items.length;
                    renderPalette(input.value, false);
                }
            } else if (event.key === 'ArrowUp') {
                event.preventDefault();
                if (items.length) {
                    selected = (selected - 1 + items.length) % items.length;
                    renderPalette(input.value, false);
                }
            } else if (event.key === 'Enter') {
                if (event.isComposing) return;
                event.preventDefault();
                executeSelected();
            } else if (event.key === 'Escape') {
                event.preventDefault();
                closePalette();
            }
        });
    }

    function openPalette() {
        ensurePalette();
        closeHelp();
        overlay.hidden = false;
        document.body.classList.add('nm-cmd-open');
        input.value = '';
        selected = 0;
        renderPalette('', true);
        setTimeout(function () { input.focus(); }, 0);
    }

    function closePalette() {
        if (!overlay) return;
        overlay.hidden = true;
        if (!helpOverlay || helpOverlay.hidden) document.body.classList.remove('nm-cmd-open');
    }

    document.addEventListener('keydown', function (event) {
        if ((event.ctrlKey || event.metaKey) && !event.altKey &&
                String(event.key).toLowerCase() === 'k') {
            event.preventDefault();
            if (overlay && !overlay.hidden) closePalette();
            else openPalette();
        }
    });

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && helpOverlay && !helpOverlay.hidden) {
            event.preventDefault();
            closeHelp();
        }
    });

    var commandHint = document.getElementById('nm-command-hint-btn');
    if (commandHint) {
        commandHint.addEventListener('click', openPalette);
    }
    var shortcutsHelpLink = document.getElementById('nm-shortcuts-help-link');
    if (shortcutsHelpLink) {
        shortcutsHelpLink.addEventListener('click', openHelp);
    }

    window.nmOpenCommandPalette = openPalette;
    window.nmOpenShortcutsHelp = openHelp;

    renderHomeViews();
})();
