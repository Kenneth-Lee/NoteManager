/* Deterministic backlinks / related documents panel. */
(function () {
    'use strict';
    var el = document.getElementById('nm-related');
    if (!el) return;
    var src = el.getAttribute('data-nm-related-src');
    var path = el.getAttribute('data-nm-related-path');
    var prefix = el.getAttribute('data-nm-related-prefix') || '';
    if (!src || !path) return;

    function directoryHref() {
        var slash = path.lastIndexOf('/');
        var dir = slash >= 0 ? path.slice(0, slash) : '';
        return prefix + 'index.html' +
            (dir ? '#nm-dir=' + encodeURIComponent(dir) : '');
    }

    function goDirectory() {
        window.location.href = directoryHref();
    }

    document.addEventListener('keydown', function (event) {
        var altUp = event.altKey && !event.ctrlKey && !event.metaKey &&
            !event.shiftKey && event.key === 'ArrowUp';
        var ctrlAltU = (event.ctrlKey || event.metaKey) && event.altKey &&
            !event.shiftKey && String(event.code || '') === 'KeyU';
        if (!altUp && !ctrlAltU) return;
        var target = event.target;
        if (target && (target.isContentEditable ||
                /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName || ''))) return;
        event.preventDefault();
        goDirectory();
    });

    function makeLink(item) {
        var a = document.createElement('a');
        a.href = prefix + item.path;
        a.textContent = item.title || item.path;
        return a;
    }

    function renderList(title, items, emptyText, total, titleHref) {
        var box = document.createElement('div');
        box.className = 'nm-related-col';
        var h = document.createElement('h3');
        var count = typeof total === 'number' ? total : items.length;
        var label = title + '（' + count + '）';
        if (titleHref) {
            var titleLink = document.createElement('a');
            titleLink.href = titleHref;
            titleLink.textContent = label;
            titleLink.title = '回到项目主页并定位到当前目录（Alt+↑ 或 Ctrl/⌘+Alt+U）';
            h.appendChild(titleLink);
        } else {
            h.textContent = label;
        }
        box.appendChild(h);
        if (!items.length) {
            var p = document.createElement('p');
            p.className = 'nm-related-empty';
            p.textContent = emptyText;
            box.appendChild(p);
            return box;
        }
        var ul = document.createElement('ul');
        items.forEach(function (item) {
            var li = document.createElement('li');
            li.appendChild(makeLink(item));
            if (item.snippet) {
                var snip = document.createElement('div');
                snip.className = 'nm-related-snippet';
                snip.textContent = item.snippet;
                li.appendChild(snip);
            }
            ul.appendChild(li);
        });
        box.appendChild(ul);
        return box;
    }

    fetch(src, {cache: 'no-store'}).then(function (r) {
        if (!r.ok) throw new Error('relations fetch failed');
        return r.json();
    }).then(function (data) {
        var doc = data && data.docs && data.docs[path];
        if (!doc) return;
        var hasAny = (doc.same_dir || []).length ||
            (doc.backlinks || []).length || (doc.outgoing || []).length ||
            (doc.related || []).length;
        if (!hasAny && !doc.orphan) return;

        var panel = document.createElement('section');
        panel.className = 'nm-related-panel';
        var heading = document.createElement('div');
        heading.className = 'nm-related-heading';
        var h2 = document.createElement('h2');
        h2.textContent = '文档关系';
        heading.appendChild(h2);
        if (doc.orphan) {
            var badge = document.createElement('span');
            badge.className = 'nm-related-orphan';
            badge.textContent = '孤立文档';
            badge.title = '没有入链，也没有出链';
            heading.appendChild(badge);
        }
        panel.appendChild(heading);
        var grid = document.createElement('div');
        grid.className = 'nm-related-grid';
        grid.appendChild(renderList(
            '同目录', doc.same_dir || [], '这个目录没有其他笔记',
            doc.same_dir_count, directoryHref()
        ));
        grid.appendChild(renderList('被引用', doc.backlinks || [], '还没有其他文档链接到这里', doc.backlink_count));
        grid.appendChild(renderList('引用', doc.outgoing || [], '这篇文档没有内部链接', doc.outgoing_count));
        grid.appendChild(renderList('相关文档', doc.related || [], '没有找到明显相关的文档', doc.related_count));
        panel.appendChild(grid);
        el.appendChild(panel);
    }).catch(function () {
        // Relations are optional; never break the document page.
        el.hidden = true;
    });
})();
