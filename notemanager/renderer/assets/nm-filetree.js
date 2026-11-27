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
    var assetsOverlay = null;

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
            '<option value=".rst">reStructuredText (.rst)</option>' +
            '<option value=".txt">纯文本 (.txt)</option></select></div>' +
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
        window.__nmSuppressBuildReload = true;
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
                window.__nmSuppressBuildReload = false;
                btn.disabled = false;
                errorText(err, '创建成功，但服务端没有返回可打开的地址');
                return;
            }
            btn.textContent = '已创建，正在打开…';
            window.location.assign(target);
        }).catch(function (e) {
            window.__nmSuppressBuildReload = false;
            btn.disabled = false;
            errorText(err, e && e.message ? e.message : '创建失败');
        });
    }

    function currentSourceDir() {
        var meta = document.getElementById('nm-page-meta');
        if (meta) {
            try {
                var cfg = JSON.parse(meta.textContent || '{}');
                var rel = String(cfg.source_rel || '').replace(/\\/g, '/');
                var slash = rel.lastIndexOf('/');
                return slash >= 0 ? rel.slice(0, slash) : '';
            } catch (e) {
                // Fall through to the project-home hash.
            }
        }
        // On the project home, the radial directory graph and Alt+Up encode
        // the current directory in #nm-dir=<path>.  Use it for "new note".
        var hash = String(window.location.hash || '');
        var marker = '#nm-dir=';
        if (hash.indexOf(marker) === 0) {
            try {
                return decodeURIComponent(hash.slice(marker.length));
            } catch (e) {
                return '';
            }
        }
        return '';
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
        window.__nmSuppressBuildReload = true;
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
            // Close and remove the row immediately.  Navigation may be delayed
            // or blocked by a beforeunload prompt; the user must still see that
            // the note has already moved to trash.
            closeOverlay(deleteOverlay);
            document.querySelectorAll('.nm-file').forEach(function (row) {
                if (row.getAttribute('data-nm-source') === source) row.remove();
            });
            var dir = deleteOverlay.dataset.redirectDir || '';
            setTimeout(function () {
                window.__nmSuppressBuildReload = false;
            }, 1200);
            window.location.href = dir
                ? base + 'index.html#nm-dir=' + encodeURIComponent(dir)
                : base;
        }).catch(function (e) {
            window.__nmSuppressBuildReload = false;
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

    function ensureAssetsDialog() {
        if (assetsOverlay) return;
        assetsOverlay = makeOverlay('assets');
        assetsOverlay.innerHTML = '<div class="nm-note-dialog nm-assets-dialog" role="dialog" aria-label="资源清理">' +
            '<div class="nm-note-head"><h2>资源清理</h2>' +
            '<button type="button" class="nm-note-close" aria-label="关闭">×</button></div>' +
            '<div class="nm-assets-summary" id="nm-assets-summary"></div>' +
            '<div class="nm-assets-toolbar"><label class="nm-assets-all"><input type="checkbox" id="nm-assets-all"> 全选未引用图片</label>' +
            '<button type="button" class="nm-note-danger" id="nm-assets-trash" disabled>移到回收站</button></div>' +
            '<div class="nm-assets-list" id="nm-assets-list">正在加载…</div>' +
            '<div class="nm-assets-missing" id="nm-assets-missing"></div>' +
            '<div class="nm-note-error" id="nm-assets-error" hidden></div>' +
            '<div class="nm-note-actions"><button type="button" class="nm-note-cancel">关闭</button></div>' +
            '</div>';
        assetsOverlay.querySelector('.nm-note-close').addEventListener('click', function () {
            closeOverlay(assetsOverlay);
        });
        assetsOverlay.querySelector('.nm-note-cancel').addEventListener('click', function () {
            closeOverlay(assetsOverlay);
        });
        assetsOverlay.querySelector('#nm-assets-all').addEventListener('change', function () {
            var checked = this.checked;
            assetsOverlay.querySelectorAll('.nm-asset-check').forEach(function (cb) {
                cb.checked = checked;
            });
            updateAssetTrashButton();
        });
        assetsOverlay.querySelector('#nm-assets-trash').addEventListener('click', trashSelectedAssets);
        assetsOverlay.addEventListener('change', function (event) {
            if (event.target && event.target.classList.contains('nm-asset-check')) {
                updateAssetTrashButton();
            }
        });
    }

    function updateAssetTrashButton() {
        if (!assetsOverlay) return;
        var n = assetsOverlay.querySelectorAll('.nm-asset-check:checked').length;
        var btn = assetsOverlay.querySelector('#nm-assets-trash');
        btn.disabled = n === 0;
        btn.textContent = n ? '移到回收站（' + n + '）' : '移到回收站';
    }

    function renderAssets(data) {
        var list = assetsOverlay.querySelector('#nm-assets-list');
        var summary = assetsOverlay.querySelector('#nm-assets-summary');
        var missingBox = assetsOverlay.querySelector('#nm-assets-missing');
        var err = assetsOverlay.querySelector('#nm-assets-error');
        var items = (data && data.items) || [];
        var missing = (data && data.missing) || [];
        errorText(err, '');
        summary.textContent = '未引用图片 ' + items.length + ' 张 · 缺失引用 ' + missing.length + ' 条';
        list.textContent = '';
        missingBox.textContent = '';
        if (!items.length) {
            addTextLine(list, '没有未引用图片。', 'nm-assets-empty');
        }
        items.forEach(function (item) {
            var row = document.createElement('label');
            row.className = 'nm-asset-item';
            var cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.className = 'nm-asset-check';
            cb.value = item.path;
            var img = document.createElement('img');
            img.loading = 'lazy';
            img.alt = '';
            img.src = base + encodeRel(item.path);
            img.addEventListener('error', function () { img.style.display = 'none'; });
            var main = document.createElement('div');
            main.className = 'nm-asset-main';
            addTextLine(main, item.path, 'nm-asset-path');
            addTextLine(main, formatSize(item.size), 'nm-asset-size');
            row.appendChild(cb);
            row.appendChild(img);
            row.appendChild(main);
            list.appendChild(row);
        });
        if (missing.length) {
            addTextLine(missingBox, '缺失引用：', 'nm-assets-missing-title');
            missing.slice(0, 8).forEach(function (p) {
                addTextLine(missingBox, '· ' + p, 'nm-asset-missing');
            });
        }
        assetsOverlay.querySelector('#nm-assets-all').checked = false;
        updateAssetTrashButton();
    }

    function loadAssets() {
        var list = assetsOverlay.querySelector('#nm-assets-list');
        var err = assetsOverlay.querySelector('#nm-assets-error');
        list.textContent = '正在加载…';
        errorText(err, '');
        fetch('/api/orphan_assets', {
            method: 'POST',
            headers: {'Content-Type': 'application/json; charset=utf-8'},
            body: JSON.stringify({project: project})
        }).then(function (r) {
            return r.json().then(function (d) {
                if (!r.ok || !d.ok) throw new Error(d.error || ('HTTP ' + r.status));
                return d;
            });
        }).then(renderAssets).catch(function (e) {
            list.textContent = '无法加载资源。';
            errorText(err, e && e.message ? e.message : '加载失败');
        });
    }

    function openAssets() {
        ensureAssetsDialog();
        assetsOverlay.hidden = false;
        document.body.classList.add('nm-note-open');
        loadAssets();
    }

    function trashSelectedAssets() {
        if (!assetsOverlay) return;
        var paths = [];
        assetsOverlay.querySelectorAll('.nm-asset-check:checked').forEach(function (cb) {
            paths.push(cb.value);
        });
        if (!paths.length) return;
        var btn = assetsOverlay.querySelector('#nm-assets-trash');
        var err = assetsOverlay.querySelector('#nm-assets-error');
        btn.disabled = true;
        fetch('/api/trash_orphans', {
            method: 'POST',
            headers: {'Content-Type': 'application/json; charset=utf-8'},
            body: JSON.stringify({project: project, paths: paths})
        }).then(function (r) {
            return r.json().then(function (d) {
                if (!r.ok || !d.ok) throw new Error(d.error || ('HTTP ' + r.status));
                return d;
            });
        }).then(function (d) {
            errorText(err, '');
            loadAssets();
        }).catch(function (e) {
            btn.disabled = false;
            errorText(err, e && e.message ? e.message : '清理失败');
        });
    }

    var assetsBtn = document.getElementById('nm-assets-open');
    if (assetsBtn) {
        assetsBtn.addEventListener('click', openAssets);
    }

    var trashBtn = document.getElementById('nm-trash-open');
    if (trashBtn) {
        trashBtn.addEventListener('click', openTrash);
    }

    var rootBtn = document.getElementById('nm-new-root');
    if (rootBtn) {
        rootBtn.title = '在当前目录新建笔记';
        rootBtn.addEventListener('click', function () { openCreate(currentSourceDir()); });
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
        } else if (assetsOverlay && !assetsOverlay.hidden) {
            event.preventDefault();
            closeOverlay(assetsOverlay);
        }
    });
})();
