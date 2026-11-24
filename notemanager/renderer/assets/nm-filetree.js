/* Directory-tree file creation/deletion (safe trash) for the web UI. */
(function () {
    'use strict';

    var match = window.location.pathname.match(/^\/p\/(\d+)(?:\/|$)/);
    if (!match) return;
    var project = parseInt(match[1], 10);
    var base = '/p/' + project + '/';
    var createOverlay = null;
    var deleteOverlay = null;

    function encodeRel(path) {
        return String(path || '').replace(/^\/+/, '').split('/').map(encodeURIComponent).join('/');
    }

    function makeOverlay(kind) {
        var overlay = document.createElement('div');
        overlay.className = 'nm-note-overlay';
        overlay.hidden = true;
        overlay.dataset.kind = kind;
        document.body.appendChild(overlay);
        overlay.addEventListener('mousedown', function (event) {
            if (event.target === overlay) closeOverlay(overlay);
        });
        return overlay;
    }

    function closeOverlay(overlay) {
        if (!overlay) return;
        overlay.hidden = true;
        if (document.body.classList.contains('nm-note-open')) {
            document.body.classList.remove('nm-note-open');
        }
    }

    function errorText(node, text) {
        node.textContent = text || '';
        node.hidden = !text;
    }

    function ensureCreateDialog() {
        if (createOverlay) return;
        createOverlay = makeOverlay('create');
        createOverlay.innerHTML = '<div class="nm-note-dialog" role="dialog" aria-label="新建笔记">' +
            '<div class="nm-note-head"><h2>新建笔记</h2>' +
            '<button type="button" class="nm-note-close" aria-label="关闭">×</button></div>' +
            '<div class="nm-note-field"><label>目录</label><div class="nm-note-dir" id="nm-note-dir"></div></div>' +
            '<div class="nm-note-field"><label for="nm-note-name">文件名</label>' +
            '<input id="nm-note-name" type="text" value="untitled" autocomplete="off" spellcheck="false"></div>' +
            '<div class="nm-note-field"><label for="nm-note-ext">类型</label>' +
            '<select id="nm-note-ext"><option value=".md">Markdown (.md)</option>' +
            '<option value=".rst">reStructuredText (.rst)</option></select></div>' +
            '<div class="nm-note-error" id="nm-note-error" hidden></div>' +
            '<div class="nm-note-actions"><button type="button" class="nm-note-cancel">取消</button>' +
            '<button type="button" class="nm-note-primary" id="nm-note-create">创建并打开</button></div>' +
            '</div>';
        createOverlay.querySelector('.nm-note-close').addEventListener('click', function () {
            closeOverlay(createOverlay);
        });
        createOverlay.querySelector('.nm-note-cancel').addEventListener('click', function () {
            closeOverlay(createOverlay);
        });
        createOverlay.querySelector('#nm-note-name').addEventListener('keydown', function (event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                submitCreate();
            }
        });
        createOverlay.querySelector('#nm-note-create').addEventListener('click', submitCreate);
    }

    function submitCreate() {
        if (!createOverlay) return;
        var dir = createOverlay.dataset.dir || '';
        var nameInput = createOverlay.querySelector('#nm-note-name');
        var extSelect = createOverlay.querySelector('#nm-note-ext');
        var err = createOverlay.querySelector('#nm-note-error');
        var btn = createOverlay.querySelector('#nm-note-create');
        var filename = String(nameInput.value || '').trim();
        if (!filename) {
            errorText(err, '请输入文件名');
            nameInput.focus();
            return;
        }
        btn.disabled = true;
        errorText(err, '');
        fetch('/api/create_note_in_dir', {
            method: 'POST',
            headers: {'Content-Type': 'application/json; charset=utf-8'},
            body: JSON.stringify({
                project: project,
                dir_rel_path: dir,
                filename: filename,
                source_ext: extSelect.value || '.md'
            })
        }).then(function (r) {
            return r.json().then(function (data) {
                if (!r.ok || !data.ok) throw new Error(data.error || ('HTTP ' + r.status));
                return data;
            });
        }).then(function (data) {
            window.location.href = base + encodeRel(data.html_rel_path);
        }).catch(function (e) {
            btn.disabled = false;
            errorText(err, e && e.message ? e.message : '创建失败');
        });
    }

    function openCreate(dir) {
        ensureCreateDialog();
        createOverlay.dataset.dir = String(dir || '');
        createOverlay.querySelector('#nm-note-dir').textContent = dir ? dir : '项目根目录';
        createOverlay.querySelector('#nm-note-name').value = 'untitled';
        var err = createOverlay.querySelector('#nm-note-error');
        errorText(err, '');
        createOverlay.querySelector('#nm-note-create').disabled = false;
        createOverlay.hidden = false;
        document.body.classList.add('nm-note-open');
        var input = createOverlay.querySelector('#nm-note-name');
        setTimeout(function () { input.focus(); input.select(); }, 0);
    }

    function addTextLine(parent, text, className) {
        var div = document.createElement('div');
        div.className = className || '';
        div.textContent = text;
        parent.appendChild(div);
        return div;
    }

    function ensureDeleteDialog() {
        if (deleteOverlay) return;
        deleteOverlay = makeOverlay('delete');
        deleteOverlay.innerHTML = '<div class="nm-note-dialog" role="dialog" aria-label="删除笔记">' +
            '<div class="nm-note-head"><h2>删除笔记</h2>' +
            '<button type="button" class="nm-note-close" aria-label="关闭">×</button></div>' +
            '<div class="nm-note-file" id="nm-note-file"></div>' +
            '<div class="nm-note-impact" id="nm-note-impact">正在检查影响…</div>' +
            '<div class="nm-note-warning">笔记会移到 <code>.NoteManager/trash</code>，' +
            '不会自动删除它引用的图片。确定继续吗？</div>' +
            '<div class="nm-note-error" id="nm-note-delete-error" hidden></div>' +
            '<div class="nm-note-actions"><button type="button" class="nm-note-cancel">取消</button>' +
            '<button type="button" class="nm-note-danger" id="nm-note-delete">移到回收站</button></div>' +
            '</div>';
        deleteOverlay.querySelector('.nm-note-close').addEventListener('click', function () {
            closeOverlay(deleteOverlay);
        });
        deleteOverlay.querySelector('.nm-note-cancel').addEventListener('click', function () {
            closeOverlay(deleteOverlay);
        });
        deleteOverlay.querySelector('#nm-note-delete').addEventListener('click', submitDelete);
    }

    function submitDelete() {
        if (!deleteOverlay) return;
        var source = deleteOverlay.dataset.source || '';
        var btn = deleteOverlay.querySelector('#nm-note-delete');
        var err = deleteOverlay.querySelector('#nm-note-delete-error');
        btn.disabled = true;
        errorText(err, '');
        fetch('/api/delete_note', {
            method: 'POST',
            headers: {'Content-Type': 'application/json; charset=utf-8'},
            body: JSON.stringify({project: project, source_rel_path: source})
        }).then(function (r) {
            return r.json().then(function (data) {
                if (!r.ok || !data.ok) throw new Error(data.error || ('HTTP ' + r.status));
                return data;
            });
        }).then(function () {
            var dir = deleteOverlay.dataset.redirectDir || '';
            window.location.href = dir
                ? base + 'index.html#nm-dir=' + encodeURIComponent(dir)
                : base;
        }).catch(function (e) {
            btn.disabled = false;
            errorText(err, e && e.message ? e.message : '删除失败');
        });
    }

    function parentDir(source) {
        var parts = String(source || '').replace(/\\/g, '/').split('/');
        parts.pop();
        return parts.join('/');
    }

    function openDelete(source, title, redirectDir) {
        ensureDeleteDialog();
        deleteOverlay.dataset.source = source;
        deleteOverlay.dataset.redirectDir = redirectDir || parentDir(source);
        deleteOverlay.querySelector('#nm-note-file').textContent = title || source;
        var impact = deleteOverlay.querySelector('#nm-note-impact');
        impact.textContent = '正在检查影响…';
        var err = deleteOverlay.querySelector('#nm-note-delete-error');
        errorText(err, '');
        deleteOverlay.querySelector('#nm-note-delete').disabled = true;
        deleteOverlay.hidden = false;
        document.body.classList.add('nm-note-open');
        fetch('/api/note_delete_info', {
            method: 'POST',
            headers: {'Content-Type': 'application/json; charset=utf-8'},
            body: JSON.stringify({project: project, source_rel_path: source})
        }).then(function (r) {
            return r.json().then(function (data) {
                if (!r.ok || !data.ok) throw new Error(data.error || ('HTTP ' + r.status));
                return data;
            });
        }).then(function (data) {
            impact.textContent = '';
            addTextLine(impact, '被引用：' + data.backlink_count + ' 篇', 'nm-note-impact-main');
            if (data.backlinks && data.backlinks.length) {
                var list = document.createElement('div');
                list.className = 'nm-note-impact-list';
                data.backlinks.slice(0, 4).forEach(function (item) {
                    addTextLine(list, '· ' + (item.title || item.path), '');
                });
                impact.appendChild(list);
            }
            addTextLine(impact, '文中图片引用：' + data.image_count + ' 张', 'nm-note-impact-main');
            if (data.images && data.images.length) {
                var images = document.createElement('div');
                images.className = 'nm-note-impact-list';
                data.images.slice(0, 4).forEach(function (name) {
                    addTextLine(images, '· ' + name, '');
                });
                impact.appendChild(images);
            }
            deleteOverlay.querySelector('#nm-note-delete').disabled = false;
        }).catch(function (e) {
            impact.textContent = '无法获取影响信息。';
            errorText(err, e && e.message ? e.message : '检查失败');
            deleteOverlay.querySelector('#nm-note-delete').disabled = false;
        });
    }

    var rootBtn = document.getElementById('nm-new-root');
    if (rootBtn) {
        rootBtn.addEventListener('click', function () { openCreate(''); });
    }
    document.querySelectorAll('[data-nm-add-dir]').forEach(function (btn) {
        btn.addEventListener('click', function (event) {
            event.preventDefault();
            event.stopPropagation();
            openCreate(btn.getAttribute('data-nm-add-dir') || '');
        });
    });
    document.querySelectorAll('[data-nm-delete-source]').forEach(function (btn) {
        btn.addEventListener('click', function (event) {
            event.preventDefault();
            event.stopPropagation();
            openDelete(
                btn.getAttribute('data-nm-delete-source') || '',
                btn.getAttribute('data-nm-delete-title') || ''
            );
        });
    });
    var currentDelete = document.getElementById('nm-delete-current');
    if (currentDelete) {
        currentDelete.addEventListener('click', function (event) {
            event.preventDefault();
            if (window.__nmDirty && !window.confirm(
                '当前文档有未保存的修改，删除后不会保存。确定继续吗？'
            )) return;
            var source = currentDelete.getAttribute('data-nm-delete-source') || '';
            openDelete(
                source,
                currentDelete.getAttribute('data-nm-delete-title') || '',
                parentDir(source)
            );
        });
    }
    document.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape') return;
        if (createOverlay && !createOverlay.hidden) {
            event.preventDefault();
            closeOverlay(createOverlay);
        } else if (deleteOverlay && !deleteOverlay.hidden) {
            event.preventDefault();
            closeOverlay(deleteOverlay);
        }
    });
})();
