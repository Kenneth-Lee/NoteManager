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
        btn.title = '在图片上添加圈注、箭头、直线和文字';
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
            ['line', '直线'],
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

        var fillColor = document.createElement('input');
        fillColor.type = 'color';
        fillColor.value = '#ffeb3b';
        fillColor.className = 'nm-annotate-color';
        fillColor.title = '填充颜色';
        fillColor.addEventListener('input', function () {
            state.fillColor = fillColor.value;
            state.fillEnabled = true;
            fillToggle.checked = true;
            state.applyStyle();
        });
        bar.appendChild(fillColor);

        var fillToggleLabel = document.createElement('label');
        fillToggleLabel.className = 'nm-annotate-check';
        var fillToggle = document.createElement('input');
        fillToggle.type = 'checkbox';
        fillToggle.addEventListener('change', function () {
            state.fillEnabled = fillToggle.checked;
            state.applyStyle();
        });
        fillToggleLabel.appendChild(fillToggle);
        fillToggleLabel.appendChild(document.createTextNode('填充'));
        bar.appendChild(fillToggleLabel);

        var fillOpacity = document.createElement('input');
        fillOpacity.type = 'range';
        fillOpacity.min = '0';
        fillOpacity.max = '100';
        fillOpacity.step = '5';
        fillOpacity.value = '30';
        fillOpacity.className = 'nm-annotate-opacity';
        fillOpacity.title = '填充透明度';
        var fillOpacityValue = document.createElement('span');
        fillOpacityValue.className = 'nm-annotate-opacity-value';
        fillOpacityValue.textContent = '30%';
        fillOpacity.addEventListener('input', function () {
            state.fillOpacity = Number(fillOpacity.value) || 0;
            state.fillEnabled = state.fillOpacity > 0;
            fillToggle.checked = state.fillEnabled;
            fillOpacityValue.textContent = state.fillOpacity + '%';
            state.applyStyle();
        });
        var fillOpacityWrap = document.createElement('span');
        fillOpacityWrap.className = 'nm-annotate-opacity-wrap';
        fillOpacityWrap.appendChild(fillOpacity);
        fillOpacityWrap.appendChild(fillOpacityValue);
        bar.appendChild(fillOpacityWrap);

        var info = document.createElement('span');
        info.className = 'nm-annotate-info';
        bar.appendChild(info);

        state.controls = {
            color: color, width: width, dash: dash, fontSize: fontSize,
            fillColor: fillColor, fillToggle: fillToggle, fillOpacity: fillOpacity,
            fillOpacityValue: fillOpacityValue, info: info
        };

        function ensureOption(select, value, label) {
            var text = String(value);
            if (!select.querySelector('option[value="' + text + '"]')) {
                var o = document.createElement('option');
                o.value = text;
                o.textContent = label;
                select.appendChild(o);
            }
        }

        function updateInfo() {
            if (state.selection.length > 1) {
                info.textContent = '已选：' + state.selection.length + ' 个图元';
                return;
            }
            var node = state.selection.length === 1 ? state.selected : null;
            if (!node) {
                info.textContent = '';
                return;
            }
            var tag = node.nodeName.toLowerCase();
            var name = {ellipse: '圈', rect: '框', arrow: '箭头', line: '线', text: '文字'}[tag] || tag;
            var box;
            try { box = node.getBBox(); } catch (e) { box = null; }
            var dims = box ? ' ' + Math.round(box.width) + '×' + Math.round(box.height) : '';
            info.textContent = '已选：' + name + dims;
        }

        state.updateInfo = updateInfo;
        state.syncControls = function () {
            var node = state.selected;
            if (!node) {
                width.disabled = false;
                dash.disabled = false;
                fontSize.disabled = false;
                fillColor.disabled = false;
                fillToggle.disabled = false;
                fillOpacity.disabled = false;
                updateInfo();
                return;
            }
            var tag = node.nodeName.toLowerCase();
            state.color = node.getAttribute('stroke') || state.color;
            state.width = Number(node.getAttribute('stroke-width')) || state.width;
            state.dash = node.getAttribute('stroke-dasharray') ? true : false;
            state.fontSize = Number(node.getAttribute('font-size')) || state.fontSize;
            color.value = state.color;
            ensureOption(width, state.width, state.width + 'px');
            width.value = String(state.width);
            dash.value = state.dash ? 'dashed' : 'solid';
            ensureOption(fontSize, state.fontSize, state.fontSize + 'px');
            fontSize.value = String(state.fontSize);
            var closed = tag === 'rect' || tag === 'ellipse';
            var fill = node.getAttribute('fill') || 'none';
            state.fillEnabled = closed && fill !== 'none';
            state.fillColor = fill !== 'none' ? fill : state.fillColor;
            if (fill !== 'none' && node.getAttribute('fill-opacity') !== null) {
                state.fillOpacity = Math.round((Number(node.getAttribute('fill-opacity')) || 0) * 100);
            } else if (!state.fillOpacity) {
                state.fillOpacity = 30;
            }
            fillColor.disabled = !closed;
            fillToggle.disabled = !closed;
            fillOpacity.disabled = !closed;
            width.disabled = tag === 'text';
            dash.disabled = tag === 'text';
            fontSize.disabled = tag !== 'text';
            fillToggle.checked = state.fillEnabled;
            fillColor.value = state.fillColor;
            fillOpacity.value = String(state.fillOpacity);
            fillOpacityValue.textContent = state.fillOpacity + '%';
            updateInfo();
        };

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
            '<div class="nm-annotate-actions">' +
            '<button type="button" class="nm-annotate-export-btn nm-annotate-action">导出 SVG</button>' +
            '<button type="button" class="nm-annotate-save">保存标注</button>' +
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
            fillColor: '#ffeb3b',
            fillOpacity: 30,
            fillEnabled: false,
            drawing: null,
            mode: null,
            resizePart: null,
            start: null,
            original: null,
            selected: null,
            selection: [],
            moveOriginals: [],
            marquee: null,
            marqueeStart: null,
            marqueeAdd: false,
            defs: null,
            arrowSeq: 0,
            shapes: [],
            clear: function () {
                this.shapes.forEach(function (n) { if (n.parentNode) n.parentNode.removeChild(n); if (n.style) n.style.filter = ''; });
                this.shapes = [];
                this.selected = null;
                this.selection = [];
                this.renderSelection();
                if (this.syncControls) this.syncControls();
            },
            setTool: function (tool) {
                this.tool = tool;
                this.mode = null;
                this.drawing = null;
                if (tool !== 'select') {
                    this.selected = null;
                    this.selection = [];
                    this.shapes.forEach(function (n) { if (n.style) n.style.filter = ''; });
                    this.renderSelection();
                    if (this.syncControls) this.syncControls();
                }
                this.shapes.forEach(function (node) {
                    node.style.pointerEvents = tool === 'select' ? 'all' : 'none';
                    node.style.cursor = tool === 'select' ? 'move' : 'crosshair';
                });
                svg.style.cursor = tool === 'select' ? 'default' : (tool === 'text' ? 'text' : 'crosshair');
            },
            applyStyle: function () {
                if (!this.selection.length) return;
                this.selection.forEach(function (node) { applyStyleTo(node); });
                this.renderSelection();
            },
            editText: function () {
                var node = this.selection.length === 1 ? this.selected : null;
                if (!node || node.nodeName.toLowerCase() !== 'text') return;
                var text = window.prompt('修改标注文字', node.textContent || '');
                if (text === null) return;
                node.textContent = text;
                this.renderSelection();
            },
            deleteSelected: function () {
                if (!this.selection.length) return;
                var self = this;
                this.selection.forEach(function (node) {
                    var index = self.shapes.indexOf(node);
                    if (index >= 0) self.shapes.splice(index, 1);
                    if (node.style) node.style.filter = '';
                    if (node.parentNode) node.parentNode.removeChild(node);
                });
                this.selected = null;
                this.selection = [];
                this.renderSelection();
                if (this.syncControls) this.syncControls();
            },
            selectAll: function () {
                selectShapes(this.shapes.slice(), false);
            },
            nudge: function (dx, dy) {
                this.selection.forEach(function (node) {
                    var g = geometry(node);
                    moveTo(node, g, dx, dy);
                });
                renderSelection();
            },
            duplicateSelection: function () {
                if (!this.selection.length) return;
                var self = this;
                var copies = [];
                this.selection.forEach(function (node) {
                    var copy = node.cloneNode(true);
                    copy.setAttribute('data-nm-shape', '1');
                    copy.style.filter = '';
                    copy.style.pointerEvents = 'all';
                    copy.style.cursor = 'move';
                    var g = geometry(node);
                    moveTo(copy, g, 16, 16);
                    svg.appendChild(copy);
                    self.shapes.push(copy);
                    copies.push(copy);
                });
                this.selection = copies;
                this.selected = copies[copies.length - 1] || null;
                applySelectionFilters();
                if (this.syncControls) this.syncControls();
                renderSelection();
            },
            renderSelection: function () {
                renderSelection();
            }
        };
        editor._nmState = state;
        var selectionGroup = null;

        function applyStyleTo(node) {
            var tag = node.nodeName.toLowerCase();
            if (tag === 'text') {
                node.setAttribute('fill', state.color);
                node.setAttribute('font-size', state.fontSize);
            } else {
                var tagName = node.nodeName.toLowerCase();
                node.setAttribute('stroke', state.color);
                node.setAttribute('stroke-width', state.width);
                if (tagName === 'rect' || tagName === 'ellipse') {
                    node.setAttribute('fill', state.fillEnabled ? state.fillColor : 'none');
                    if (state.fillEnabled) node.setAttribute('fill-opacity', state.fillOpacity / 100);
                    else node.removeAttribute('fill-opacity');
                } else {
                    node.setAttribute('fill', 'none');
                    node.removeAttribute('fill-opacity');
                }
                if (state.dash) node.setAttribute('stroke-dasharray', (state.width * 3) + ' ' + (state.width * 2));
                else node.removeAttribute('stroke-dasharray');
                if (tagName === 'line' && node.getAttribute('marker-end')) {
                    ensureArrowMarker(node, state.color);
                }
            }
        }

        function ensureArrowMarker(line, color) {
            if (!state.defs) return;
            var id = line.getAttribute('data-nm-marker-id');
            if (!id) {
                id = 'nm-arrow-' + (++state.arrowSeq) + '-' + Date.now().toString(36);
                line.setAttribute('data-nm-marker-id', id);
            }
            var marker = state.defs.querySelector('marker[id="' + id + '"]');
            if (!marker) {
                marker = makeSvg('marker', {
                    id: id, viewBox: '0 0 10 10', refX: '9', refY: '5',
                    markerWidth: '5', markerHeight: '5', orient: 'auto-start-reverse'
                });
                marker.appendChild(makeSvg('path', {d: 'M 0 0 L 10 5 L 0 10 z', fill: color}));
                state.defs.appendChild(marker);
            }
            var path = marker.querySelector('path');
            if (path) path.setAttribute('fill', color);
            line.setAttribute('marker-end', 'url(#' + id + ')');
        }

        function applySelectionFilters() {
            state.shapes.forEach(function (node) {
                if (!node.style) return;
                node.style.filter = state.selection.indexOf(node) >= 0
                    ? 'drop-shadow(0 0 5px #1e88e5)' : '';
            });
        }

        function selectShape(node, additive) {
            if (additive) {
                var idx = state.selection.indexOf(node);
                if (idx >= 0) {
                    state.selection.splice(idx, 1);
                    if (state.selected === node) {
                        state.selected = state.selection[state.selection.length - 1] || null;
                    }
                } else {
                    state.selection.push(node);
                    state.selected = node;
                }
            } else {
                state.selection = [node];
                state.selected = node;
            }
            state.mode = null;
            state.drawing = null;
            state.shapes.forEach(function (n) { n.style.pointerEvents = 'all'; });
            applySelectionFilters();
            if (state.syncControls) state.syncControls();
            renderSelection();
        }

        function selectShapes(nodes, additive) {
            if (!additive) state.selection = [];
            nodes.forEach(function (node) {
                if (state.selection.indexOf(node) < 0) state.selection.push(node);
            });
            state.selected = state.selection[state.selection.length - 1] || null;
            state.mode = null;
            state.drawing = null;
            state.shapes.forEach(function (n) { n.style.pointerEvents = 'all'; });
            applySelectionFilters();
            if (state.syncControls) state.syncControls();
            renderSelection();
        }

        function deselect() {
            state.selection = [];
            state.selected = null;
            state.mode = null;
            state.drawing = null;
            state.shapes.forEach(function (n) { if (n.style) n.style.filter = ''; });
            renderSelection();
            if (state.syncControls) state.syncControls();
        }

        function renderSelection() {
            if (selectionGroup && selectionGroup.parentNode) {
                selectionGroup.parentNode.removeChild(selectionGroup);
            }
            selectionGroup = null;
            if (state.tool !== 'select' || !state.selection.length) return;
            selectionGroup = makeSvg('g', {'data-nm-editor-only': '1'});
            if (state.selection.length > 1) {
                var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
                state.selection.forEach(function (node) {
                    var b;
                    try { b = node.getBBox(); } catch (e) { return; }
                    minX = Math.min(minX, b.x);
                    minY = Math.min(minY, b.y);
                    maxX = Math.max(maxX, b.x + b.width);
                    maxY = Math.max(maxY, b.y + b.height);
                });
                if (!isFinite(minX) || !isFinite(minY)) return;
                var pad2 = 6;
                selectionGroup.appendChild(makeSvg('rect', {
                    x: minX - pad2, y: minY - pad2,
                    width: Math.max(1, maxX - minX + pad2 * 2),
                    height: Math.max(1, maxY - minY + pad2 * 2),
                    fill: 'rgba(30,136,229,.06)', stroke: '#1565c0', 'stroke-width': 2,
                    'stroke-dasharray': '7 4', 'pointer-events': 'none'
                }));
                svg.appendChild(selectionGroup);
                if (state.updateInfo) state.updateInfo();
                return;
            }
            var node = state.selected;
            if (!node || !node.parentNode) return;
            var tag = node.nodeName.toLowerCase();
            var box;
            try { box = node.getBBox(); } catch (e) { return; }
            if (!box) return;
            var isLine = tag === 'line';
            if (!isLine && (!box.width && !box.height)) return;
            var pad = Math.max(3, Math.min(8, state.width * 1.5));
            var rx = box.x - pad;
            var ry = box.y - pad;
            var rw = box.width + pad * 2;
            var rh = box.height + pad * 2;
            if (isLine && !box.width && !box.height) {
                rx = box.x - 5; ry = box.y - 5; rw = 10; rh = 10;
            }
            var rect = makeSvg('rect', {
                x: rx, y: ry, width: rw, height: rh,
                fill: 'rgba(30,136,229,.06)', stroke: '#1565c0', 'stroke-width': 2,
                'stroke-dasharray': '5 3', 'pointer-events': 'none'
            });
            selectionGroup.appendChild(rect);
            var size = Math.max(9, Math.min(16, box.width * 0.04 + 7));
            function addHandle(hx, hy, part, cursor) {
                var handle = makeSvg('rect', {
                    x: hx - size / 2, y: hy - size / 2,
                    width: size, height: size, fill: '#fff', stroke: '#1565c0',
                    'stroke-width': 1.5, 'data-nm-resize': part,
                    style: 'pointer-events:all;cursor:' + (cursor || 'nwse-resize') + ';'
                });
                selectionGroup.appendChild(handle);
            }
            if (isLine) {
                addHandle(+node.getAttribute('x1') || 0, +node.getAttribute('y1') || 0, 'start', 'nwse-resize');
                addHandle(+node.getAttribute('x2') || 0, +node.getAttribute('y2') || 0, 'end', 'nwse-resize');
            } else {
                addHandle(box.x + box.width, box.y + box.height, 'end', 'nwse-resize');
            }
            svg.appendChild(selectionGroup);
            if (state.updateInfo) state.updateInfo();
        }

        edImg.src = img.currentSrc || img.src;
        createToolbar(dialog, state);
        editor.querySelector('.nm-annotate-close').addEventListener('click', closeEditor);
        editor.querySelector('.nm-annotate-cancel').addEventListener('click', closeEditor);

        var defs = makeSvg('defs', {});
        state.defs = defs;
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
                    copy.style.cursor = 'move';
                    svg.appendChild(copy);
                    if (copy.nodeName.toLowerCase() === 'line' && copy.getAttribute('marker-end')) {
                        ensureArrowMarker(copy, copy.getAttribute('stroke') || state.color);
                    }
                    state.shapes.push(copy);
                }
            }
        }

        function importSidecar(text) {
            if (state.shapes.length) return;
            var temp = makeSvg('svg', {});
            if (!importSvgChildren(temp, text)) return;
            var vb = temp.getAttribute('viewBox');
            if (vb) svg.setAttribute('viewBox', vb);
            for (var j = 0; j < temp.childNodes.length; j++) {
                var child = temp.childNodes[j];
                if (child.nodeType !== 1) continue;
                if (child.nodeName.toLowerCase() === 'defs') continue;
                var copy = child.cloneNode(true);
                copy.setAttribute('data-nm-shape', '1');
                copy.style.pointerEvents = 'all';
                copy.style.cursor = 'move';
                svg.appendChild(copy);
                if (copy.nodeName.toLowerCase() === 'line' && copy.getAttribute('marker-end')) {
                    ensureArrowMarker(copy, copy.getAttribute('stroke') || state.color);
                }
                state.shapes.push(copy);
            }
            if (state.syncControls) state.syncControls();
        }

        if (!state.shapes.length && info.sidecar) {
            fetch(info.sidecar, {cache: 'no-store'})
                .then(function (r) { return r.ok ? r.text() : ''; })
                .then(function (text) { if (text) importSidecar(text); })
                .catch(function () {});
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

        function normalizeBox(a, b) {
            return {
                x1: Math.min(a.x, b.x), y1: Math.min(a.y, b.y),
                x2: Math.max(a.x, b.x), y2: Math.max(a.y, b.y)
            };
        }

        function updateMarquee(a, b) {
            var box = normalizeBox(a, b);
            state.marquee.setAttribute('x', box.x1);
            state.marquee.setAttribute('y', box.y1);
            state.marquee.setAttribute('width', Math.max(0, box.x2 - box.x1));
            state.marquee.setAttribute('height', Math.max(0, box.y2 - box.y1));
        }

        function newShape(tool, p) {
            var common = {stroke: state.color, 'stroke-width': state.width, fill: 'none'};
            if (state.dash) common['stroke-dasharray'] = (state.width * 3) + ' ' + (state.width * 2);
            if ((tool === 'rect' || tool === 'ellipse') && state.fillEnabled) {
                common.fill = state.fillColor;
                common['fill-opacity'] = state.fillOpacity / 100;
            }
            var shape = null;
            if (tool === 'rect') shape = makeSvg('rect', Object.assign({x: p.x, y: p.y, width: 0, height: 0}, common));
            else if (tool === 'ellipse') shape = makeSvg('ellipse', Object.assign({cx: p.x, cy: p.y, rx: 0, ry: 0}, common));
            else if (tool === 'arrow' || tool === 'line') {
                shape = makeSvg('line', Object.assign({x1: p.x, y1: p.y, x2: p.x, y2: p.y}, common));
            }
            if (shape) {
                shape.setAttribute('data-nm-shape', '1');
                shape.style.cursor = state.tool === 'select' ? 'move' : 'crosshair';
                if (tool === 'arrow') ensureArrowMarker(shape, state.color);
            }
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

        function distanceToSegment(p, a, b) {
            var vx = b.x - a.x;
            var vy = b.y - a.y;
            var wx = p.x - a.x;
            var wy = p.y - a.y;
            var len2 = vx * vx + vy * vy;
            var t = len2 ? Math.max(0, Math.min(1, (wx * vx + wy * vy) / len2)) : 0;
            var dx = p.x - (a.x + t * vx);
            var dy = p.y - (a.y + t * vy);
            return Math.sqrt(dx * dx + dy * dy);
        }

        function nearestLine(p, tolerance) {
            var best = null;
            var bestDist = tolerance;
            state.shapes.forEach(function (node) {
                if (node.nodeName.toLowerCase() !== 'line') return;
                var a = {x: +node.getAttribute('x1') || 0, y: +node.getAttribute('y1') || 0};
                var b = {x: +node.getAttribute('x2') || 0, y: +node.getAttribute('y2') || 0};
                var dist = distanceToSegment(p, a, b);
                if (dist <= bestDist) {
                    best = node;
                    bestDist = dist;
                }
            });
            return best;
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
                if (state.resizePart === 'start') {
                    node.setAttribute('x1', p.x);
                    node.setAttribute('y1', p.y);
                } else {
                    node.setAttribute('x2', p.x);
                    node.setAttribute('y2', p.y);
                }
            } else if (tag === 'text') {
                var orig = state.original || {};
                var box = orig.box || node.getBBox();
                var fontSize = orig.fontSize || Number(node.getAttribute('font-size')) || 28;
                var left = box.x || 0;
                var width = Math.max(1, box.width || 1);
                var scaleX = (p.x - left) / width;
                var scaleY = (p.y - (box.y || 0)) / Math.max(1, box.height || 1);
                var scale = Math.max(scaleX, scaleY);
                var size = Math.max(8, Math.min(160, fontSize * scale));
                var oldTop = box.y;
                node.setAttribute('font-size', size);
                var newBox = node.getBBox();
                var y = +node.getAttribute('y') || 0;
                node.setAttribute('y', y + (oldTop - newBox.y));
            }
        }

        svg.addEventListener('pointerdown', function (event) {
            if (event.button !== 0) return;
            var target = event.target;
            var resizePart = target.getAttribute && target.getAttribute('data-nm-resize');
            if (resizePart) {
                if (!state.selected) return;
                event.preventDefault();
                state.mode = 'resize';
                state.resizePart = resizePart;
                state.start = point(event);
                if (state.selected && state.selected.nodeName.toLowerCase() === 'text') {
                    state.original = {
                        box: state.selected.getBBox(),
                        fontSize: Number(state.selected.getAttribute('font-size')) || 28
                    };
                } else {
                    state.original = null;
                }
                svg.setPointerCapture && svg.setPointerCapture(event.pointerId);
                return;
            }
            if (state.tool === 'select') {
                var hit = shapeFromTarget(target);
                var selectPoint = point(event);
                if (!hit) {
                    var imageRect = edImg.getBoundingClientRect();
                    var vb = (svg.getAttribute('viewBox') || '').split(/\s+/).map(Number);
                    var vbWidth = vb.length === 4 && vb[2] ? vb[2] : (edImg.naturalWidth || 1000);
                    var tolerance = 24 * vbWidth / Math.max(1, imageRect.width);
                    hit = nearestLine(selectPoint, tolerance);
                }
                var shift = event.shiftKey || event.metaKey || event.ctrlKey;
                if (hit) {
                    event.preventDefault();
                    if (shift) {
                        selectShape(hit, true);
                        return;
                    }
                    if (state.selection.indexOf(hit) < 0) {
                        selectShape(hit, false);
                    }
                    state.mode = 'move';
                    state.start = selectPoint;
                    state.moveOriginals = state.selection.map(function (n) {
                        return {node: n, geom: geometry(n)};
                    });
                    svg.setPointerCapture && svg.setPointerCapture(event.pointerId);
                    return;
                }
                event.preventDefault();
                state.mode = 'marquee';
                state.marqueeAdd = shift;
                state.marqueeStart = selectPoint;
                state.start = selectPoint;
                state.marquee = makeSvg('rect', {
                    x: selectPoint.x, y: selectPoint.y, width: 0, height: 0,
                    fill: 'rgba(30,136,229,.10)', stroke: '#1565c0',
                    'stroke-width': 1.5, 'stroke-dasharray': '4 3',
                    'pointer-events': 'none', 'data-nm-editor-only': '1'
                });
                svg.appendChild(state.marquee);
                svg.setPointerCapture && svg.setPointerCapture(event.pointerId);
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
            if (state.mode === 'move' && state.moveOriginals.length) {
                event.preventDefault();
                var p = point(event);
                state.moveOriginals.forEach(function (item) {
                    moveTo(item.node, item.geom, p.x - state.start.x, p.y - state.start.y);
                });
                renderSelection();
                return;
            }
            if (state.mode === 'marquee' && state.marquee) {
                event.preventDefault();
                var mp = point(event);
                updateMarquee(state.marqueeStart, mp);
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
            if (state.mode === 'marquee') {
                var endPoint = point(event);
                var startPoint = state.marqueeStart || endPoint;
                var box = normalizeBox(startPoint, endPoint);
                if (state.marquee.parentNode) state.marquee.parentNode.removeChild(state.marquee);
                var hits = state.shapes.filter(function (node) {
                    var b;
                    try { b = node.getBBox(); } catch (e) { return false; }
                    var nx1 = b.x, ny1 = b.y;
                    var nx2 = b.x + b.width, ny2 = b.y + b.height;
                    return nx1 <= box.x2 && nx2 >= box.x1 && ny1 <= box.y2 && ny2 >= box.y1;
                });
                state.mode = null;
                state.marquee = null;
                state.marqueeStart = null;
                state.start = null;
                if (hits.length) selectShapes(hits, state.marqueeAdd);
                else if (!state.marqueeAdd) deselect();
                state.marqueeAdd = false;
                return;
            }
            if (state.mode === 'move' || state.mode === 'resize') {
                state.mode = null;
                state.resizePart = null;
                state.start = null;
                state.original = null;
                state.moveOriginals = [];
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
        function serializedSvg() {
            var serializer = svg.cloneNode(true);
            serializer.querySelectorAll('[data-nm-editor-only]').forEach(function (n) {
                if (n.parentNode) n.parentNode.removeChild(n);
            });
            return serializer.outerHTML;
        }

        function showExportResult(result) {
            var overlay = document.createElement('div');
            overlay.className = 'nm-zoom-overlay nm-annotate-export';
            overlay.innerHTML = '<div class="nm-zoom-dialog" role="dialog" aria-label="导出 SVG">' +
                '<div class="nm-zoom-head"><h2>已导出带标注 SVG</h2>' +
                '<button type="button" class="nm-zoom-close" aria-label="关闭">×</button></div>' +
                '<p class="nm-zoom-note">可以直接把这个 SVG 文件当作普通图片引用；原图不再需要同时引用。</p>' +
                '<p class="nm-annotate-export-path"></p>' +
                '<div class="nm-zoom-code"><label>Markdown</label><pre class="nm-export-md"></pre>' +
                '<button type="button" class="nm-zoom-copy" data-kind="md">复制</button></div>' +
                '<div class="nm-zoom-code"><label>reST</label><pre class="nm-export-rst"></pre>' +
                '<button type="button" class="nm-zoom-copy" data-kind="rst">复制</button></div>' +
                '<div class="nm-export-undo-row"><button type="button" class="nm-export-undo">' +
                '撤销导出（删除刚生成的文件）</button></div>' +
                '</div>';
            document.body.appendChild(overlay);
            var path = overlay.querySelector('.nm-annotate-export-path');
            var md = overlay.querySelector('.nm-export-md');
            var rst = overlay.querySelector('.nm-export-rst');
            function docRelative(target) {
                try {
                    var page = decodeURIComponent(window.location.pathname)
                        .replace(/^\/p\/\d+\//, '');
                    var dir = page.indexOf('/') >= 0
                        ? page.slice(0, page.lastIndexOf('/') + 1) : '';
                    var from = dir.split('/').filter(Boolean);
                    var to = String(target || '').split('/').filter(Boolean);
                    var common = 0;
                    while (common < from.length && common < to.length &&
                           from[common] === to[common]) common++;
                    var out = [];
                    for (var i = common; i < from.length; i++) out.push('..');
                    out = out.concat(to.slice(common));
                    return out.join('/') || target;
                } catch (e) {
                    return target;
                }
            }
            var sourcePath = docRelative(result.path);
            path.textContent = (result.path || '') +
                (sourcePath !== result.path ? '  →  ' + sourcePath : '');
            md.textContent = '![](' + sourcePath + ')';
            rst.textContent = '.. image:: ' + sourcePath;
            overlay.querySelector('.nm-zoom-close').addEventListener('click', function () {
                overlay.parentNode.removeChild(overlay);
            });
            overlay.addEventListener('mousedown', function (event) {
                if (event.target === overlay) overlay.parentNode.removeChild(overlay);
            });
            overlay.querySelectorAll('.nm-zoom-copy').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    var text = btn.dataset.kind === 'rst' ? rst.textContent : md.textContent;
                    if (navigator.clipboard) navigator.clipboard.writeText(text).then(function () {
                        btn.textContent = '已复制';
                        setTimeout(function () { btn.textContent = '复制'; }, 1200);
                    }).catch(function () {});
                });
            });
            var undo = overlay.querySelector('.nm-export-undo');
            undo.addEventListener('click', function () {
                if (!window.confirm('删除刚导出的 SVG 文件？')) return;
                undo.disabled = true;
                fetch('/api/delete_exported_svg', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json; charset=utf-8'},
                    body: JSON.stringify({project: project, path: result.path})
                }).then(function (r) {
                    return r.json().then(function (d) {
                        if (!r.ok || !d.ok) throw new Error(d.error || ('HTTP ' + r.status));
                        return d;
                    });
                }).then(function () {
                    if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
                    setStatus('已删除刚导出的 SVG');
                }).catch(function (e) {
                    undo.disabled = false;
                    setStatus(e && e.message ? e.message : '删除失败');
                });
            });
        }

        editor.querySelector('.nm-annotate-export-btn').addEventListener('click', function () {
            var btn = this;
            btn.disabled = true;
            setStatus('正在导出 SVG…');
            fetch('/api/export_annotated_svg', {
                method: 'POST',
                headers: {'Content-Type': 'application/json; charset=utf-8'},
                body: JSON.stringify({
                    project: project,
                    image_rel_path: info.rel,
                    svg: serializedSvg()
                })
            }).then(function (r) {
                return r.json().then(function (d) {
                    if (!r.ok || !d.ok) throw new Error(d.error || ('HTTP ' + r.status));
                    return d;
                });
            }).then(function (result) {
                setStatus('');
                showExportResult(result);
            }).catch(function (e) {
                setStatus(e && e.message ? e.message : '导出失败');
            }).finally(function () {
                btn.disabled = false;
            });
        });

        editor.querySelector('.nm-annotate-save').addEventListener('click', function () {
            var btn = this;
            btn.disabled = true;
            setStatus('保存中…');
            var payload = {
                project: project,
                image_rel_path: info.rel,
                svg: serializedSvg()
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
            return;
        }
        if (event.key === 'Escape' && zoomDialog) {
            event.preventDefault();
            closeZoom();
            return;
        }
        if (!editor || !editor._nmState) return;
        var target = event.target;
        var tag = target && target.tagName ? target.tagName.toLowerCase() : '';
        if (tag === 'input' || tag === 'select' || tag === 'textarea') return;
        var state = editor._nmState;
        if (event.key === 'Delete' || event.key === 'Backspace') {
            event.preventDefault();
            if (state.selection.length) state.deleteSelected();
            return;
        }
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
            event.preventDefault();
            if (state.shapes.length) state.selectAll();
            return;
        }
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'd') {
            if (state.selection.length) {
                event.preventDefault();
                state.duplicateSelection();
            }
            return;
        }
        if (event.key.indexOf('Arrow') === 0 && state.selection.length) {
            event.preventDefault();
            var step = event.shiftKey ? 10 : 1;
            var dx = event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0;
            var dy = event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0;
            state.nudge(dx, dy);
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
