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
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'nm-annotate-btn';
        btn.textContent = '标注';
        btn.title = '在图片上添加圈注、箭头、下划线和文字';
        wrap.appendChild(btn);

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
        btn.addEventListener('click', function (event) {
            event.preventDefault();
            event.stopPropagation();
            openEditor(img, info, overlay);
        });
    }

    function createToolbar(dialog, state) {
        var tools = [
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
            if (pair[0] === 'ellipse') b.classList.add('active');
            b.addEventListener('click', function () {
                state.tool = pair[0];
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
        color.addEventListener('input', function () { state.color = color.value; });
        bar.appendChild(color);

        var width = document.createElement('select');
        width.className = 'nm-annotate-width';
        [2, 3, 5, 8].forEach(function (w) {
            var o = document.createElement('option');
            o.value = String(w);
            o.textContent = w + 'px';
            if (w === 3) o.selected = true;
            width.appendChild(o);
        });
        width.addEventListener('change', function () { state.width = Number(width.value) || 3; });
        bar.appendChild(width);

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
            tool: 'ellipse',
            color: '#e53935',
            width: 3,
            drawing: null,
            start: null,
            shapes: [],
            undo: function () {
                var node = this.shapes.pop();
                if (node && node.parentNode) node.parentNode.removeChild(node);
            },
            clear: function () {
                this.shapes.forEach(function (n) { if (n.parentNode) n.parentNode.removeChild(n); });
                this.shapes = [];
            }
        };
        edImg.src = img.currentSrc || img.src;
        createToolbar(dialog, state);
        editor.querySelector('.nm-annotate-close').addEventListener('click', closeEditor);
        editor.querySelector('.nm-annotate-cancel').addEventListener('click', closeEditor);

        var defs = makeSvg('defs', {});
        var marker = makeSvg('marker', {
            id: 'nm-annotate-arrowhead', viewBox: '0 0 10 10', refX: '9', refY: '5',
            markerWidth: '5', markerHeight: '5', orient: 'auto-start-reverse'
        });
        marker.appendChild(makeSvg('path', {d: 'M 0 0 L 10 5 L 0 10 z', fill: state.color}));
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
                svg.appendChild(copy);
                if (copy.nodeType === 1) state.shapes.push(copy);
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
            if (tool === 'rect') return makeSvg('rect', Object.assign({x: p.x, y: p.y, width: 0, height: 0}, common));
            if (tool === 'ellipse') return makeSvg('ellipse', Object.assign({cx: p.x, cy: p.y, rx: 0, ry: 0}, common));
            if (tool === 'arrow' || tool === 'line') {
                var line = makeSvg('line', Object.assign({x1: p.x, y1: p.y, x2: p.x, y2: p.y}, common));
                if (tool === 'arrow') line.setAttribute('marker-end', 'url(#nm-annotate-arrowhead)');
                return line;
            }
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

        svg.addEventListener('pointerdown', function (event) {
            if (event.button !== 0) return;
            event.preventDefault();
            var p = point(event);
            if (state.tool === 'text') {
                var text = window.prompt('标注文字');
                if (!text) return;
                var node = makeSvg('text', {
                    x: p.x, y: p.y, fill: state.color, 'font-size': 24,
                    'font-family': 'sans-serif'
                });
                node.textContent = text;
                svg.appendChild(node);
                state.shapes.push(node);
                return;
            }
            var shape = newShape(state.tool, p);
            if (!shape) return;
            svg.appendChild(shape);
            state.drawing = shape;
            state.start = p;
            svg.setPointerCapture && svg.setPointerCapture(event.pointerId);
        });

        svg.addEventListener('pointermove', function (event) {
            if (!state.drawing) return;
            event.preventDefault();
            updateShape(state.drawing, state.tool, state.start, point(event));
        });

        svg.addEventListener('pointerup', function (event) {
            if (!state.drawing) return;
            updateShape(state.drawing, state.tool, state.start, point(event));
            var shape = state.drawing;
            state.drawing = null;
            state.start = null;
            if (shape.getAttribute('width') === '0' || shape.getAttribute('height') === '0') {
                // text/line etc. still useful; only discard empty rect/ellipse
                if (state.tool === 'rect' || state.tool === 'ellipse') {
                    shape.parentNode.removeChild(shape);
                    return;
                }
            }
            state.shapes.push(shape);
        });

        editor.querySelector('.nm-annotate-save').addEventListener('click', function () {
            var btn = this;
            btn.disabled = true;
            setStatus('保存中…');
            var payload = {
                project: project,
                image_rel_path: info.rel,
                svg: svg.outerHTML
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
