/* Per-browser reading state: read/unread, stars, recent, last visit. */
(function () {
    'use strict';

    var body = document.body;
    if (!body) return;
    var project = body.getAttribute('data-nm-project') || '';
    var page = body.getAttribute('data-nm-page-path') || '';
    if (!project || !page) return;

    var STORAGE_KEY = 'nm-reading:' + project;
    var state = loadState();
    var autoReadTimer = null;

    function now() { return Date.now() / 1000; }

    function loadState() {
        var s = {read: {}, star: {}, recent: [], lastVisit: 0};
        try {
            var raw = localStorage.getItem(STORAGE_KEY);
            var parsed = raw ? JSON.parse(raw) : null;
            if (parsed && typeof parsed === 'object') {
                s.read = parsed.read && typeof parsed.read === 'object' ? parsed.read : {};
                s.star = parsed.star && typeof parsed.star === 'object' ? parsed.star : {};
                s.recent = Array.isArray(parsed.recent) ? parsed.recent : [];
                s.lastVisit = Number(parsed.lastVisit) || 0;
            }
        } catch (e) {}
        return s;
    }

    function saveState() {
        try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch (e) {}
    }

    function markRecent(path) {
        state.recent = [path].concat(state.recent.filter(function (p) { return p !== path; })).slice(0, 10);
        saveState();
    }

    function isDocumentPage() {
        return !!page && page !== 'index.html';
    }

    function renderControls() {
        var host = document.getElementById('nm-reading-controls');
        if (!host || !isDocumentPage()) return;
        markRecent(page);
        host.innerHTML = '';

        var readBtn = document.createElement('button');
        readBtn.type = 'button';
        readBtn.className = 'nm-reading-btn';
        var starBtn = document.createElement('button');
        starBtn.type = 'button';
        starBtn.className = 'nm-reading-btn';

        function refresh() {
            var read = !!state.read[page];
            var star = !!state.star[page];
            readBtn.textContent = read ? '已读 ✓' : '标记已读';
            readBtn.title = read ? '点击标记为未读' : '标记这篇文档为已读';
            readBtn.classList.toggle('active', read);
            starBtn.textContent = star ? '★ 已星标' : '☆ 星标';
            starBtn.title = star ? '取消星标' : '加入星标';
            starBtn.classList.toggle('active', star);
        }

        readBtn.addEventListener('click', function () {
            if (autoReadTimer) {
                clearTimeout(autoReadTimer);
                autoReadTimer = null;
            }
            if (state.read[page]) delete state.read[page];
            else state.read[page] = now();
            saveState();
            refresh();
        });
        starBtn.addEventListener('click', function () {
            if (state.star[page]) delete state.star[page];
            else state.star[page] = now();
            saveState();
            refresh();
        });
        host.appendChild(readBtn);
        host.appendChild(starBtn);
        refresh();

        // Opening a document counts as reading it after a short dwell time;
        // the explicit button can still mark it unread again.
        autoReadTimer = setTimeout(function () {
            if (!state.read[page]) {
                state.read[page] = now();
                saveState();
                refresh();
            }
        }, 2000);
    }

    function makeItem(doc, showDate) {
        var li = document.createElement('li');
        var a = document.createElement('a');
        a.href = doc.path;
        a.textContent = doc.title || doc.path;
        li.appendChild(a);
        if (showDate) {
            var date = document.createElement('span');
            date.className = 'nm-reading-date';
            date.textContent = doc.date || '';
            li.appendChild(date);
        }
        return li;
    }

    function renderHome(data) {
        var home = document.getElementById('nm-reading-home');
        if (!home) return;
        var docs = Array.isArray(data.docs) ? data.docs : [];
        if (!docs.length) return;
        var byPath = Object.create(null);
        docs.forEach(function (d) { byPath[d.path] = d; });

        var firstVisit = !state.lastVisit;
        var changed = firstVisit
            ? docs.slice(0, 6)
            : docs.filter(function (d) { return d.mtime > state.lastVisit + 1; }).slice(0, 6);
        var unread = docs.filter(function (d) { return !state.read[d.path]; });
        var starred = docs.filter(function (d) { return state.star[d.path]; });
        var recent = state.recent.map(function (p) { return byPath[p]; }).filter(Boolean);
        var readCount = docs.length - unread.length;

        home.innerHTML = '';
        var panel = document.createElement('section');
        panel.className = 'nm-reading-panel';

        var head = document.createElement('div');
        head.className = 'nm-reading-heading';
        var h2 = document.createElement('h2');
        h2.textContent = '我的阅读';
        head.appendChild(h2);
        var stats = document.createElement('span');
        stats.className = 'nm-reading-stats';
        stats.textContent = '已读 ' + readCount + ' / ' + docs.length +
            ' · 星标 ' + starred.length;
        head.appendChild(stats);
        panel.appendChild(head);

        var grid = document.createElement('div');
        grid.className = 'nm-reading-grid';

        function card(title, items, emptyText, showDate) {
            var box = document.createElement('div');
            box.className = 'nm-reading-card';
            var h = document.createElement('h3');
            h.textContent = title;
            box.appendChild(h);
            if (!items.length) {
                var p = document.createElement('p');
                p.className = 'nm-reading-empty';
                p.textContent = emptyText;
                box.appendChild(p);
            } else {
                var ul = document.createElement('ul');
                items.forEach(function (doc) { ul.appendChild(makeItem(doc, showDate)); });
                box.appendChild(ul);
            }
            return box;
        }

        grid.appendChild(card(
            firstVisit ? '最近更新' : '自上次访问以来（' + changed.length + '）',
            changed,
            firstVisit ? '还没有文档' : '没有新的变化',
            true
        ));
        grid.appendChild(card(
            '待读（' + unread.length + '）',
            unread.slice(0, 4),
            '全部已读',
            true
        ));
        grid.appendChild(card(
            '星标（' + starred.length + '）',
            starred.slice(0, 4),
            '还没有星标文档',
            true
        ));
        grid.appendChild(card(
            '最近打开',
            recent.slice(0, 4),
            '还没有打开记录',
            false
        ));
        panel.appendChild(grid);
        home.appendChild(panel);

        // Update only after rendering: the next homepage visit is a new baseline.
        state.lastVisit = now();
        saveState();
    }

    function loadHome() {
        var home = document.getElementById('nm-reading-home');
        if (!home) return;
        var src = home.getAttribute('data-nm-activity-src');
        if (!src) return;
        fetch(src, {cache: 'no-cache'}).then(function (r) {
            if (!r.ok) throw new Error('activity fetch failed');
            return r.json();
        }).then(renderHome).catch(function () {
            home.hidden = true;
        });
    }

    renderControls();
    loadHome();

    // Returning to a cached homepage should reflect the latest read/star state.
    window.addEventListener('pageshow', function (event) {
        if (event.persisted) {
            state = loadState();
            renderControls();
            loadHome();
        }
    });
})();
