/* Image annotation overlay and lightweight SVG editor. */
(function () {
    'use strict';

    var NS = 'http://www.w3.org/2000/svg';
    var match = window.location.pathname.match(/^\/p\/(\d+)\//);
    if (!match) return;
    var project = parseInt(match[1], 10);
    var editor = null;

    function makeSvg(tag, attrs) {
        var el = document.createElementNS(NS, tag);
        Object.keys(attrs || {}).forEach(function (key) {
            el.setAttribute(key, String(attrs[key]));
        });
        return el;
    }

    function parseProjectImage(img) {
        try {
            var u = new URL(img.currentSrc || img.src, window.location.href);
            if (u.origin !== window.location.origin) return null;
            var m = u.pathname.match(/^\/p\/(\d+)\/(.+)$/);
            if (!m || parseInt(m[1], 10) !== project) return null;
            var path;
            try { path = decodeURIComponent(m[2]); } catch (e) { path = m[2]; }
            u.search = '';
            u.hash = '';
            return {url: u.href, rel: path, sidecar: u.href + '.ann.svg'};
        } catch (e) {
            return null;
        }
    }

    function importSvgChildren(target, source) {
        target.innerHTML = '';
        var parser = new DOMParser();
        var doc;
        try {
            doc = parser.parseFromString(source, 'image/svg+xml');
        } catch (e) {
            return false;
        }
        var root = doc.documentElement;
        if (!root || root.nodeName.toLowerCase() !== 'svg') return false;
        var viewBox = root.getAttribute('viewBox');
        if (viewBox) target.setAttribute('viewBox', viewBox);
        var children = root.childNodes;
        for (var i = 0; i < children.length; i++) {
            var node = children[i];
            if (node.nodeName.toLowerCase() === 'script') continue;
            target.appendChild(document.importNode(node, true));
        }
        return true;
    }

    function naturalBox(img, svg) {
        var w = img.naturalWidth || 1000;
        var h = img.naturalHeight || 700;
        if (!svg.getAttribute('viewBox')) {
            svg.setAttribute('viewBox', '0 0 ' + w + ' ' + h);
        }
        return {w: w, h: h};
    }

    var zoomDialog = null;

    function openZoom(img) {
        if (zoomDialog) {
            if (zoomDialog.parentNode) zoomDialog.parentNode.removeChild(zoomDialog);
            zoomDialog = null;
        }
        var src = img.getAttribute('src') || img.currentSrc || img.src;
        var alt = img.getAttribute('alt') || '';
        var natural = img.naturalWidth || 0;
        var current = Math.round(img.getBoundingClientRect().width || natural || 600);
        var maxW = Math.max(2400, natural || 0);
        zoomDialog = document.createElement('div');
        zoomDialog.className = 'nm-zoom-overlay';
        zoomDialog.innerHTML = '<div class="nm-zoom-dialog" role="dialog" aria-label="图片缩放">' +
            '<div class="nm-zoom-head"><h2>图片缩放</h2><button type="button" class="nm-zoom-close" aria-label="关闭">×</button></div>' +
            '<div class="nm-zoom-controls"><label>宽度</label><input type="range" min="40" max="' + maxW + '" step="10" value="' + current + '">' +
            '<span class="nm-zoom-value">' + current + 'px</span></div>' +
            '<div class="nm-zoom-stage"><img class="nm-zoom-image" alt=""></div>' +
            '<div class="nm-zoom-code"><label>Markdown</label><pre class="nm-zoom-md"></pre>' +
            '<button type="button" class="nm-zoom-copy" data-kind="md">复制</button></div>' +
            '<div class="nm-zoom-code"><label>reST</label><pre class="nm-zoom-rst"></pre>' +
            '<button type="button" class="nm-zoom-copy" data-kind="rst">复制</button></div>' +
            '<p class="nm-zoom-note">这里只预览显示宽度，不会修改源文件；把代码复制到源文件后即可固定该尺寸。</p>' +
            '</div>';
        document.body.appendChild(zoomDialog);
        document.body.classList.add('nm-note-open');
        var range = zoomDialog.querySelector('input[type=range]');
        var value = zoomDialog.querySelector('.nm-zoom-value');
        var preview = zoomDialog.querySelector('.nm-zoom-image');
        var mdPre = zoomDialog.querySelector('.nm-zoom-md');
        var rstPre = zoomDialog.querySelector('.nm-zoom-rst');
        preview.src = src;
        function update() {
            var px = parseInt(range.value, 10) || current;
            value.textContent = px + 'px';
            preview.style.width = px + 'px';
            mdPre.textContent = '![' + alt + '](' + src + '){width=' + px + 'px}';
            rstPre.textContent = '.. image:: ' + src + '\n   :width: ' + px + 'px' +
                (alt ? '\n   :alt: ' + alt : '');
        }
        range.addEventListener('input', update);
        update();
        zoomDialog.querySelector('.nm-zoom-close').addEventListener('click', closeZoom);
        zoomDialog.addEventListener('mousedown', function (event) {
            if (event.target === zoomDialog) closeZoom();
        });
        zoomDialog.querySelectorAll('.nm-zoom-copy').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var text = btn.dataset.kind === 'rst' ? rstPre.textContent : mdPre.textContent;
                if (navigator.clipboard) navigator.clipboard.writeText(text).then(function () {
                    btn.textContent = '已复制';
                    setTimeout(function () { btn.textContent = '复制'; }, 1200);
                }).catch(function () {});
            });
        });
        setTimeout(function () { range.focus(); }, 0);
    }

    function closeZoom() {
        if (!zoomDialog) return;
        if (zoomDialog.parentNode) zoomDialog.parentNode.removeChild(zoomDialog);
        zoomDialog = null;
        if (!editor) document.body.classList.remove('nm-note-open');
    }

    function attach(img) {
        if (!img || img.getAttribute('data-nm-annotation-skip')) return;
        if (img.closest('.nm-annotated-media')) return;
        var info = parseProjectImage(img);
        if (!info) {
            // Exported/static pages may not have /p/N/ URLs, but can still
            // display an existing sidecar annotation layer.
            try {
                var u = new URL(img.currentSrc || img.src, window.location.href);
                if (u.origin !== window.location.origin) return;
                if (u.protocol !== 'http:' && u.protocol !== 'https:') return;
                u.search = '';
                u.hash = '';
                info = {url: u.href, rel: '', sidecar: u.href + '.ann.svg'};
            } catch (e) {
                return;
            }
        }
        var wrap = document.createElement('span');
        wrap.className = 'nm-annotated-media';
        img.parentNode.insertBefore(wrap, img);
        wrap.appendChild(img);
        var overlay = makeSvg('svg', {
            class: 'nm-annotation-layer',
            'aria-hidden': 'true',
            preserveAspectRatio: 'none'
        });
        wrap.appendChild(overlay);
        var actions = document.createElement('span');
        actions.className = 'nm-image-actions';
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'nm-annotate-btn';
        btn.textContent = '标注';
        btn.title = '在图片上添加圈注、箭头、下划线和文字';
        actions.appendChild(btn);
        var zoomBtn = document.createElement('button');
        zoomBtn.type = 'button';
        zoomBtn.className = 'nm-annotate-btn nm-image-zoom-btn';
        zoomBtn.textContent = '缩放';
        zoomBtn.title = '预览图片显示宽度，并查看 Markdown/reST 写法';
        actions.appendChild(zoomBtn);
        wrap.appendChild(actions);

        function ready() {
            naturalBox(img, overlay);
            fetch(info.sidecar, {cache: 'no-store'})
                .then(function (r) {
                    if (!r.ok) throw new Error('no annotation');
                    return r.text();
                })
                .then(function (text) { importSvgChildren(overlay, text); })
                .catch(function () {});
        }
        if (img.complete) ready();
        else img.addEventListener('load', ready, {once: true});

        btn.style.display = info.rel ? '' : 'none';
        zoomBtn.addEventListener('click', function (event) {
            event.preventDefault();
            event.stopPropagation();
            openZoom(img);
        });
        btn.addEventListener('click', function (event) {
            event.preventDefault();
            event.stopPropagation();
            openEditor(img, info, overlay);
        });
    }

    function createToolbar(dialog, state) {
        var tools = [
            ['select', '选择'],
            ['ellipse', '圈'],
            ['rect', '框'],
            ['arrow', '箭头'],
            ['line', '下划线'],
            ['text', '文字']
        ];
        var bar = document.createElement('div');
        bar.className = 'nm-annotate-toolbar';
        tools.forEach(function (pair) {
            var b = document.createElement('button');
            b.type = 'button';
            b.className = 'nm-annotate-tool';
            b.dataset.tool = pair[0];
            b.textContent = pair[1];
            if (pair[0] === 'select') b.classList.add('active');
            b.addEventListener('click', function () {
                state.setTool(pair[0]);
                bar.querySelectorAll('.nm-annotate-tool').forEach(function (x) {
                    x.classList.toggle('active', x === b);
                });
            });
            bar.appendChild(b);
        });
        var color = document.createElement('input');
        color.type = 'color';
        color.value = '#e53935';
        color.className = 'nm-annotate-color';
        color.title = '颜色';
        color.addEventListener('input', function () {
            state.color = color.value;
            if (state.markerPath) state.markerPath.setAttribute('fill', state.color);
            state.applyStyle();
        });
        bar.appendChild(color);

        var width = document.createElement('select');
        width.className = 'nm-annotate-width';
        width.title = '线宽';
        [1, 2, 3, 5, 8, 12, 16, 24, 32].forEach(function (w) {
            var o = document.createElement('option');
            o.value = String(w);
            o.textContent = w + 'px';
            if (w === 5) o.selected = true;
            width.appendChild(o);
        });
        width.addEventListener('change', function () {
            state.width = Number(width.value) || 5;
            state.applyStyle();
        });
        bar.appendChild(width);

        var dash = document.createElement('select');
        dash.className = 'nm-annotate-width';
        dash.title = '线型';
        [['solid', '实线'], ['dashed', '虚线']].forEach(function (pair) {
            var o = document.createElement('option');
            o.value = pair[0];
            o.textContent = pair[1];
            dash.appendChild(o);
        });
        dash.addEventListener('change', function () {
            state.dash = dash.value === 'dashed';
            state.applyStyle();
        });
        bar.appendChild(dash);

        var fontSize = document.createElement('select');
        fontSize.className = 'nm-annotate-width';
        fontSize.title = '文字大小';
        [12, 16, 20, 24, 28, 36, 48, 64, 80, 96].forEach(function (size) {
            var o = document.createElement('option');
            o.value = String(size);
            o.textContent = size + 'px';
            if (size === 28) o.selected = true;
            fontSize.appendChild(o);
        });
        fontSize.addEventListener('change', function () {
            state.fontSize = Number(fontSize.value) || 28;
            state.applyStyle();
        });
        bar.appendChild(fontSize);

        var editText = document.createElement('button');
        editText.type = 'button';
        editText.className = 'nm-annotate-action';
        editText.textContent = '改文字';
        editText.addEventListener('click', function () { state.editText(); });
        bar.appendChild(editText);

        var del = document.createElement('button');
        del.type = 'button';
        del.className = 'nm-annotate-action';
        del.textContent = '删除';
        del.addEventListener('click', function () { state.deleteSelected(); });
        bar.appendChild(del);

        var undo = document.createElement('button');
        undo.type = 'button';
        undo.className = 'nm-annotate-action';
        undo.textContent = '撤销';
        undo.addEventListener('click', function () { state.undo(); });
        bar.appendChild(undo);

        var clear = document.createElement('button');
        clear.type = 'button';
        clear.className = 'nm-annotate-action';
        clear.textContent = '清空';
        clear.addEventListener('click', function () {
            if (!state.shapes.length || window.confirm('清空所有标注？')) state.clear();
        });
        bar.appendChild(clear);
        dialog.insertBefore(bar, dialog.querySelector('.nm-annotate-stage'));
        return bar;
    }

    function openEditor(img, info, originalOverlay) {
        if (editor) closeEditor();
        editor = document.createElement('div');
        editor.className = 'nm-annotate-overlay';
        editor.innerHTML = '<div class="nm-annotate-dialog" role="dialog" aria-label="图片标注">' +
            '<div class="nm-annotate-head"><h2>图片标注</h2>' +
            '<span class="nm-annotate-status" id="nm-annotate-status"></span>' +
            '<button type="button" class="nm-annotate-close" aria-label="关闭">×</button></div>' +
            '<div class="nm-annotate-stage"><div class="nm-annotate-canvas">' +
            '<img class="nm-annotate-image" alt="">' +
            '<svg class="nm-annotate-svg" xmlns="http://www.w3.org/2000/svg"></svg></div></div>' +
            '<div class="nm-annotate-actions"><button type="button" class="nm-annotate-save">保存标注</button>' +
            '<button type="button" class="nm-annotate-cancel">取消</button></div></div>';
        document.body.appendChild(editor);
        document.body.classList.add('nm-note-open');
        var dialog = editor.querySelector('.nm-annotate-dialog');
        var stage = editor.querySelector('.nm-annotate-stage');
        var edImg = editor.querySelector('.nm-annotate-image');
        var svg = editor.querySelector('.nm-annotate-svg');
        var status = editor.querySelector('#nm-annotate-status');
        var state = {
            tool: 'select',
            color: '#e53935',
            width: 5,
            dash: false,
            fontSize: 28,
            drawing: null,
            mode: null,
            start: null,
            original: null,
            selected: null,
            markerPath: null,
            shapes: [],
            undo: function () {
                var node = this.shapes.pop();
                if (node && node.parentNode) node.parentNode.removeChild(node);
                if (node === this.selected) this.selected = null;
                this.renderSelection();
            },
            clear: function () {
                this.shapes.forEach(function (n) { if (n.parentNode) n.parentNode.removeChild(n); });
                this.shapes = [];
                this.selected = null;
                this.renderSelection();
            },
            setTool: function (tool) {
                this.tool = tool;
                this.mode = null;
                this.drawing = null;
                if (tool !== 'select') {
                    this.selected = null;
                    this.renderSelection();
                }
                this.shapes.forEach(function (node) {
                    node.style.pointerEvents = tool === 'select' ? 'all' : 'none';
                });
            },
            applyStyle: function () {
                if (!this.selected) return;
                applyStyleTo(this.selected);
                this.renderSelection();
            },
            editText: function () {
                var node = this.selected;
                if (!node || node.nodeName.toLowerCase() !== 'text') return;
                var text = window.prompt('修改标注文字', node.textContent || '');
                if (text === null) return;
                node.textContent = text;
                this.renderSelection();
            },
            deleteSelected: function () {
                var node = this.selected;
                if (!node) return;
                var index = this.shapes.indexOf(node);
                if (index >= 0) this.shapes.splice(index, 1);
                if (node.parentNode) node.parentNode.removeChild(node);
                this.selected = null;
                this.renderSelection();
            },
            renderSelection: function () {
                renderSelection();
            }
        };
        var selectionGroup = null;

        function applyStyleTo(node) {
            var tag = node.nodeName.toLowerCase();
            if (tag === 'text') {
                node.setAttribute('fill', state.color);
                node.setAttribute('font-size', state.fontSize);
            } else {
                node.setAttribute('stroke', state.color);
                node.setAttribute('stroke-width', state.width);
                node.setAttribute('fill', 'none');
                if (state.dash) node.setAttribute('stroke-dasharray', (state.width * 3) + ' ' + (state.width * 2));
                else node.removeAttribute('stroke-dasharray');
            }
        }

        function selectShape(node) {
            state.selected = node;
            state.mode = null;
            state.drawing = null;
            state.shapes.forEach(function (n) { n.style.pointerEvents = 'all'; });
            renderSelection();
        }

        function deselect() {
            state.selected = null;
            state.mode = null;
            state.drawing = null;
            renderSelection();
        }

        function renderSelection() {
            if (selectionGroup && selectionGroup.parentNode) {
                selectionGroup.parentNode.removeChild(selectionGroup);
            }
            selectionGroup = null;
            var node = state.selected;
            if (!node || state.tool !== 'select' || !node.parentNode) return;
            var box;
            try { box = node.getBBox(); } catch (e) { return; }
            if (!box || (!box.width && !box.height)) return;
            selectionGroup = makeSvg('g', {'data-nm-editor-only': '1'});
            var pad = Math.max(3, Math.min(8, state.width * 1.5));
            var rect = makeSvg('rect', {
                x: box.x - pad, y: box.y - pad,
                width: box.width + pad * 2, height: box.height + pad * 2,
                fill: 'none', stroke: '#1e88e5', 'stroke-width': 1,
                'stroke-dasharray': '4 3', 'pointer-events': 'none'
            });
            selectionGroup.appendChild(rect);
            var size = Math.max(9, Math.min(16, box.width * 0.04 + 7));
            var handle = makeSvg('rect', {
                x: box.x + box.width - size / 2,
                y: box.y + box.height - size / 2,
                width: size, height: size, fill: '#fff', stroke: '#1e88e5',
                'stroke-width': 1.5, 'data-nm-resize': '1',
                style: 'pointer-events:all;cursor:nwse-resize;'
            });
            selectionGroup.appendChild(handle);
            svg.appendChild(selectionGroup);
        }

        edImg.src = img.currentSrc || img.src;
        createToolbar(dialog, state);
        editor.querySelector('.nm-annotate-close').addEventListener('click', closeEditor);
        editor.querySelector('.nm-annotate-cancel').addEventListener('click', closeEditor);

        var defs = makeSvg('defs', {});
        var marker = makeSvg('marker', {
            id: 'nm-annotate-arrowhead', viewBox: '0 0 10 10', refX: '9', refY: '5',
            markerWidth: '5', markerHeight: '5', orient: 'auto-start-reverse'
        });
        var markerPath = makeSvg('path', {d: 'M 0 0 L 10 5 L 0 10 z', fill: state.color});
        marker.appendChild(markerPath);
        state.markerPath = markerPath;
        defs.appendChild(marker);
        svg.appendChild(defs);

        // Pull existing annotations from the current page overlay.
        if (originalOverlay && originalOverlay.childNodes.length) {
            var box = originalOverlay.getAttribute('viewBox');
            if (box) svg.setAttribute('viewBox', box);
            for (var i = 0; i < originalOverlay.childNodes.length; i++) {
                var node = originalOverlay.childNodes[i];
                if (node.nodeName.toLowerCase() === 'defs') continue;
                var copy = node.cloneNode(true);
                if (copy.nodeType === 1) {
                    copy.setAttribute('data-nm-shape', '1');
                    copy.style.pointerEvents = 'all';
                    svg.appendChild(copy);
                    state.shapes.push(copy);
                }
            }
        }

        function setStatus(text) { status.textContent = text || ''; }
        function finishLoad() {
            var box = naturalBox(edImg, svg);
            if (!svg.getAttribute('viewBox')) svg.setAttribute('viewBox', '0 0 ' + box.w + ' ' + box.h);
            svg.setAttribute('preserveAspectRatio', 'none');
        }
        if (edImg.complete) finishLoad();
        else edImg.addEventListener('load', finishLoad, {once: true});

        function point(event) {
            var r = edImg.getBoundingClientRect();
            var vb = (svg.getAttribute('viewBox') || '').split(/\s+/).map(Number);
            var w = vb.length === 4 && vb[2] ? vb[2] : (edImg.naturalWidth || 1000);
            var h = vb.length === 4 && vb[3] ? vb[3] : (edImg.naturalHeight || 700);
            return {
                x: Math.max(0, Math.min(w, (event.clientX - r.left) * w / Math.max(1, r.width))),
                y: Math.max(0, Math.min(h, (event.clientY - r.top) * h / Math.max(1, r.height)))
            };
        }

        function newShape(tool, p) {
            var common = {stroke: state.color, 'stroke-width': state.width, fill: 'none'};
            if (state.dash) common['stroke-dasharray'] = (state.width * 3) + ' ' + (state.width * 2);
            var shape = null;
            if (tool === 'rect') shape = makeSvg('rect', Object.assign({x: p.x, y: p.y, width: 0, height: 0}, common));
            else if (tool === 'ellipse') shape = makeSvg('ellipse', Object.assign({cx: p.x, cy: p.y, rx: 0, ry: 0}, common));
            else if (tool === 'arrow' || tool === 'line') {
                shape = makeSvg('line', Object.assign({x1: p.x, y1: p.y, x2: p.x, y2: p.y}, common));
                if (tool === 'arrow') shape.setAttribute('marker-end', 'url(#nm-annotate-arrowhead)');
            }
            if (shape) shape.setAttribute('data-nm-shape', '1');
            return shape;
            return null;
        }

        function updateShape(shape, tool, a, b) {
            if (!shape) return;
            if (tool === 'rect') {
                shape.setAttribute('x', Math.min(a.x, b.x));
                shape.setAttribute('y', Math.min(a.y, b.y));
                shape.setAttribute('width', Math.abs(b.x - a.x));
                shape.setAttribute('height', Math.abs(b.y - a.y));
            } else if (tool === 'ellipse') {
                shape.setAttribute('cx', (a.x + b.x) / 2);
                shape.setAttribute('cy', (a.y + b.y) / 2);
                shape.setAttribute('rx', Math.abs(b.x - a.x) / 2);
                shape.setAttribute('ry', Math.abs(b.y - a.y) / 2);
            } else {
                shape.setAttribute('x2', b.x);
                shape.setAttribute('y2', b.y);
            }
        }

        function shapeFromTarget(target) {
            while (target && target !== svg) {
                if (target.getAttribute && target.getAttribute('data-nm-shape') === '1') return target;
                target = target.parentNode;
            }
            return null;
        }

        function geometry(node) {
            var tag = node.nodeName.toLowerCase();
            if (tag === 'rect') return {x: +node.getAttribute('x') || 0, y: +node.getAttribute('y') || 0};
            if (tag === 'ellipse') return {cx: +node.getAttribute('cx') || 0, cy: +node.getAttribute('cy') || 0};
            if (tag === 'line') return {
                x1: +node.getAttribute('x1') || 0, y1: +node.getAttribute('y1') || 0,
                x2: +node.getAttribute('x2') || 0, y2: +node.getAttribute('y2') || 0
            };
            if (tag === 'text') return {x: +node.getAttribute('x') || 0, y: +node.getAttribute('y') || 0};
            return {};
        }

        function moveTo(node, orig, dx, dy) {
            var tag = node.nodeName.toLowerCase();
            if (tag === 'rect' || tag === 'text') {
                node.setAttribute('x', Math.max(0, orig.x + dx));
                node.setAttribute('y', Math.max(0, orig.y + dy));
            } else if (tag === 'ellipse') {
                node.setAttribute('cx', Math.max(0, orig.cx + dx));
                node.setAttribute('cy', Math.max(0, orig.cy + dy));
            } else if (tag === 'line') {
                node.setAttribute('x1', orig.x1 + dx);
                node.setAttribute('y1', orig.y1 + dy);
                node.setAttribute('x2', orig.x2 + dx);
                node.setAttribute('y2', orig.y2 + dy);
            }
        }

        function resizeTo(node, p) {
            var tag = node.nodeName.toLowerCase();
            if (tag === 'rect') {
                var x = +node.getAttribute('x') || 0;
                var y = +node.getAttribute('y') || 0;
                node.setAttribute('width', Math.max(5, p.x - x));
                node.setAttribute('height', Math.max(5, p.y - y));
            } else if (tag === 'ellipse') {
                var cx = +node.getAttribute('cx') || 0;
                var cy = +node.getAttribute('cy') || 0;
                node.setAttribute('rx', Math.max(5, Math.abs(p.x - cx)));
                node.setAttribute('ry', Math.max(5, Math.abs(p.y - cy)));
            } else if (tag === 'line') {
                node.setAttribute('x2', p.x);
                node.setAttribute('y2', p.y);
            } else if (tag === 'text') {
                var ty = +node.getAttribute('y') || 0;
                var size = Math.max(8, Math.abs(p.y - ty));
                node.setAttribute('font-size', Math.min(160, size));
            }
        }

        svg.addEventListener('pointerdown', function (event) {
            if (event.button !== 0) return;
            var target = event.target;
            if (target.getAttribute && target.getAttribute('data-nm-resize') === '1') {
                if (!state.selected) return;
                event.preventDefault();
                state.mode = 'resize';
                state.start = point(event);
                svg.setPointerCapture && svg.setPointerCapture(event.pointerId);
                return;
            }
            if (state.tool === 'select') {
                var hit = shapeFromTarget(target);
                if (hit) {
                    event.preventDefault();
                    selectShape(hit);
                    state.mode = 'move';
                    state.start = point(event);
                    state.original = geometry(hit);
                    svg.setPointerCapture && svg.setPointerCapture(event.pointerId);
                } else {
                    deselect();
                }
                return;
            }
            if (state.tool === 'text') {
                event.preventDefault();
                var tp = point(event);
                var text = window.prompt('标注文字');
                if (!text) return;
                var textNode = makeSvg('text', {
                    x: tp.x, y: tp.y, fill: state.color, 'font-size': state.fontSize,
                    'font-family': 'sans-serif', 'data-nm-shape': '1'
                });
                textNode.textContent = text;
                svg.appendChild(textNode);
                state.shapes.push(textNode);
                return;
            }
            event.preventDefault();
            deselect();
            var p = point(event);
            var shape = newShape(state.tool, p);
            if (!shape) return;
            shape.style.pointerEvents = 'none';
            svg.appendChild(shape);
            state.drawing = shape;
            state.start = p;
            svg.setPointerCapture && svg.setPointerCapture(event.pointerId);
        });

        svg.addEventListener('pointermove', function (event) {
            if (state.mode === 'move' && state.selected) {
                event.preventDefault();
                var p = point(event);
                moveTo(state.selected, state.original, p.x - state.start.x, p.y - state.start.y);
                renderSelection();
                return;
            }
            if (state.mode === 'resize' && state.selected) {
                event.preventDefault();
                resizeTo(state.selected, point(event));
                renderSelection();
                return;
            }
            if (!state.drawing) return;
            event.preventDefault();
            updateShape(state.drawing, state.tool, state.start, point(event));
        });

        svg.addEventListener('pointerup', function (event) {
            if (state.mode === 'move' || state.mode === 'resize') {
                state.mode = null;
                state.start = null;
                state.original = null;
                renderSelection();
                return;
            }
            if (!state.drawing) return;
            updateShape(state.drawing, state.tool, state.start, point(event));
            var shape = state.drawing;
            state.drawing = null;
            state.start = null;
            if (state.tool === 'rect' || state.tool === 'ellipse') {
                var w = parseFloat(shape.getAttribute('width') || shape.getAttribute('rx') || '0');
                var h = parseFloat(shape.getAttribute('height') || shape.getAttribute('ry') || '0');
                if (w < 2 || h < 2) {
                    shape.parentNode.removeChild(shape);
                    return;
                }
            }
            shape.style.pointerEvents = state.tool === 'select' ? 'all' : 'none';
            state.shapes.push(shape);
        });

        svg.addEventListener('dblclick', function (event) {
            var hit = shapeFromTarget(event.target);
            if (!hit || hit.nodeName.toLowerCase() !== 'text') return;
            var text = window.prompt('修改标注文字', hit.textContent || '');
            if (text === null) return;
            hit.textContent = text;
            selectShape(hit);
        });
        editor.querySelector('.nm-annotate-save').addEventListener('click', function () {
            var btn = this;
            btn.disabled = true;
            setStatus('保存中…');
            var serializer = svg.cloneNode(true);
            serializer.querySelectorAll('[data-nm-editor-only]').forEach(function (n) {
                if (n.parentNode) n.parentNode.removeChild(n);
            });
            var payload = {
                project: project,
                image_rel_path: info.rel,
                svg: serializer.outerHTML
            };
            fetch('/api/save_annotation', {
                method: 'POST',
                headers: {'Content-Type': 'application/json; charset=utf-8'},
                body: JSON.stringify(payload)
            }).then(function (r) {
                return r.json().then(function (d) {
                    if (!r.ok || !d.ok) throw new Error(d.error || ('HTTP ' + r.status));
                    return d;
                });
            }).then(function () {
                if (originalOverlay) {
                    originalOverlay.setAttribute('viewBox', svg.getAttribute('viewBox') || '');
                    originalOverlay.innerHTML = '';
                    var defsSource = svg.querySelector('defs');
                    svg.childNodes.forEach(function (node) {
                        if (node.nodeName.toLowerCase() === 'defs') return;
                        if (node.nodeType === 1 && node.getAttribute('data-nm-editor-only')) return;
                        originalOverlay.appendChild(node.cloneNode(true));
                    });
                    if (defsSource) originalOverlay.insertBefore(defsSource.cloneNode(true), originalOverlay.firstChild);
                }
                closeEditor();
            }).catch(function (e) {
                btn.disabled = false;
                setStatus(e && e.message ? e.message : '保存失败');
            });
        });

        setTimeout(function () { dialog.scrollIntoView({block: 'center'}); }, 0);
    }

    function closeEditor() {
        if (!editor) return;
        if (editor.parentNode) editor.parentNode.removeChild(editor);
        editor = null;
        document.body.classList.remove('nm-note-open');
    }

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && editor) {
            event.preventDefault();
            closeEditor();
        } else if (event.key === 'Escape' && zoomDialog) {
            event.preventDefault();
            closeZoom();
        }
    });

    function init() {
        var panel = document.getElementById('nm-panel-render') || document.body;
        panel.querySelectorAll('img').forEach(attach);
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
