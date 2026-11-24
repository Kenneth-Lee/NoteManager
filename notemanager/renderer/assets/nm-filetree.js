/* Directory-tree file creation/deletion (safe trash) for the web UI. */
(function () {
    'use strict';

    var match = window.location.pathname.match(/^\/p\/(\d+)(?:\/|$)/);
    if (!match) return;
    var project = parseInt(match[1], 10);
    var base = '/p/' + project + '/';
    var createOverlay = null;
    var deleteOverlay = null;
    var trashOverlay = null;

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
            var rel = data.html_rel_path ||
                String(data.source_rel_path || '').replace(/\.(md|rst|txt)$/i, '.html');
            var target = data.open_url || (rel ? base + encodeRel(rel) : '');
            if (!target) {
                btn.disabled = false;
                errorText(err, '创建成功，但服务端没有返回可打开的地址');
                return;
            }
            btn.textContent = '已创建，正在打开…';
            window.location.assign(target);
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
            '<label class="nm-note-asset-choice" id="nm-note-asset-choice" hidden>' +
            '<input type="checkbox" id="nm-note-delete-assets" checked> ' +
            '<span id="nm-note-asset-text">同时删除仅被本文引用的图片</span></label>' +
            '<div class="nm-note-warning">笔记会移到 <code>.NoteManager/trash</code>。' +
            '共享图片不会删除；独占图片是否一起删除由上面的选项决定。</div>' +
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
            body: JSON.stringify({
                project: project,
                source_rel_path: source,
                delete_assets: (function () {
                    var cb = deleteOverlay.querySelector('#nm-note-delete-assets');
                    var choice = deleteOverlay.querySelector('#nm-note-asset-choice');
                    return !!(cb && cb.checked && choice && !choice.hidden);
                })()
            })
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
        var assetChoice = deleteOverlay.querySelector('#nm-note-asset-choice');
        if (assetChoice) {
            assetChoice.hidden = true;
            var assetCb = deleteOverlay.querySelector('#nm-note-delete-assets');
            if (assetCb) assetCb.checked = true;
        }
        deleteOverlay.querySelector('#nm-note-delete').disabled = true;
        deleteOverlay.hidden = false;
        document.body.classList.add('nm-note-open');
        fetch('/api/note_delete_info', {
            method: 'POST',
            headers: {'Content-Type': 'application/json; charset=utf-8'},
            body: JSON.stringify({
                project: project,
                source_rel_path: source
            })
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
            if (data.image_count) {
                addTextLine(
                    impact,
                    '独占：' + (data.exclusive_image_count || 0) + ' 张 · 共享：' +
                    (data.shared_image_count || 0) + ' 张',
                    'nm-note-impact-list'
                );
            }
            if (data.images && data.images.length) {
                var images = document.createElement('div');
                images.className = 'nm-note-impact-list';
                data.images.slice(0, 4).forEach(function (name) {
                    addTextLine(images, '· ' + name, '');
                });
                impact.appendChild(images);
            }
            var choice = deleteOverlay.querySelector('#nm-note-asset-choice');
            var choiceText = deleteOverlay.querySelector('#nm-note-asset-text');
            if (choice && choiceText && data.exclusive_image_count > 0) {
                choice.hidden = false;
                choiceText.textContent = '同时删除仅被本文引用的图片（' +
                    data.exclusive_image_count + ' 张）';
            }
            deleteOverlay.querySelector('#nm-note-delete').disabled = false;
        }).catch(function (e) {
            impact.textContent = '无法获取影响信息。';
            errorText(err, e && e.message ? e.message : '检查失败');
            deleteOverlay.querySelector('#nm-note-delete').disabled = false;
        });
    }

    function ensureTrashDialog() {
        if (trashOverlay) return;
        trashOverlay = makeOverlay('trash');
        trashOverlay.innerHTML = '<div class="nm-note-dialog" role="dialog" aria-label="回收站">' +
            '<div class="nm-note-head"><h2>回收站</h2>' +
            '<button type="button" class="nm-note-close" aria-label="关闭">×</button></div>' +
            '<div class="nm-trash-list" id="nm-trash-list">正在加载…</div>' +
            '<div class="nm-note-error" id="nm-trash-error" hidden></div>' +
            '<div class="nm-note-actions"><button type="button" class="nm-note-cancel">关闭</button></div>' +
            '</div>';
        trashOverlay.querySelector('.nm-note-close').addEventListener('click', function () {
            closeOverlay(trashOverlay);
        });
        trashOverlay.querySelector('.nm-note-cancel').addEventListener('click', function () {
            closeOverlay(trashOverlay);
        });
    }

    function formatSize(size) {
        var n = Number(size) || 0;
        if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
        if (n >= 1024) return Math.round(n / 1024) + ' KB';
        return n + ' B';
    }

    function renderTrash(data) {
        var list = trashOverlay.querySelector('#nm-trash-list');
        var err = trashOverlay.querySelector('#nm-trash-error');
        errorText(err, '');
        list.textContent = '';
        var items = (data && data.items) || [];
        if (!items.length) {
            addTextLine(list, '回收站是空的。', 'nm-trash-empty');
            return;
        }
        items.forEach(function (item) {
            var row = document.createElement('div');
            row.className = 'nm-trash-item';
            var main = document.createElement('div');
            main.className = 'nm-trash-main';
            addTextLine(main, item.title || item.source_rel_path || '未命名', 'nm-trash-title');
            addTextLine(main, item.source_rel_path || '', 'nm-trash-path');
            var meta = [];
            if (item.created_at) meta.push(String(item.created_at).replace('T', ' '));
            if (item.asset_count) meta.push('图片 ' + item.asset_count + ' 张');
            if (item.size) meta.push(formatSize(item.size));
            if (item.legacy) meta.push('旧格式');
            addTextLine(main, meta.join(' · '), 'nm-trash-meta');
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'nm-trash-restore';
            btn.textContent = '恢复';
            btn.addEventListener('click', function () {
                btn.disabled = true;
                errorText(err, '');
                fetch('/api/trash_restore', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json; charset=utf-8'},
                    body: JSON.stringify({project: project, trash_id: item.trash_id})
                }).then(function (r) {
                    return r.json().then(function (d) {
                        if (!r.ok || !d.ok) throw new Error(d.error || ('HTTP ' + r.status));
                        return d;
                    });
                }).then(function () {
                    window.location.href = base;
                }).catch(function (e) {
                    btn.disabled = false;
                    errorText(err, e && e.message ? e.message : '恢复失败');
                });
            });
            row.appendChild(main);
            row.appendChild(btn);
            list.appendChild(row);
        });
    }

    function openTrash() {
        ensureTrashDialog();
        var list = trashOverlay.querySelector('#nm-trash-list');
        var err = trashOverlay.querySelector('#nm-trash-error');
        errorText(err, '');
        list.textContent = '正在加载…';
        trashOverlay.hidden = false;
        document.body.classList.add('nm-note-open');
        fetch('/api/trash_list', {
            method: 'POST',
            headers: {'Content-Type': 'application/json; charset=utf-8'},
            body: JSON.stringify({project: project})
        }).then(function (r) {
            return r.json().then(function (d) {
                if (!r.ok || !d.ok) throw new Error(d.error || ('HTTP ' + r.status));
                return d;
            });
        }).then(renderTrash).catch(function (e) {
            list.textContent = '无法加载回收站。';
            errorText(err, e && e.message ? e.message : '加载失败');
        });
    }

    var trashBtn = document.getElementById('nm-trash-open');
    if (trashBtn) {
        trashBtn.addEventListener('click', openTrash);
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
        } else if (trashOverlay && !trashOverlay.hidden) {
            event.preventDefault();
            closeOverlay(trashOverlay);
        }
    });
})();
