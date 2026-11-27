/* Radial directory graph: drill-down, breadcrumb, back stack, zoom. */
(function () {
    'use strict';

    var dataEl = document.getElementById('nm-dir-graph-data');
    var graph = document.querySelector('.nm-dir-graph');
    if (!dataEl || !graph) return;
    var wrap = graph.parentElement;

    var data;
    try {
        data = JSON.parse(dataEl.textContent || '{}');
    } catch (e) {
        return;
    }
    if (!data || typeof data !== 'object') return;

    var byPath = Object.create(null);
    var parentPath = Object.create(null);
    (function index(node, parent) {
        if (!node || typeof node !== 'object') return;
        var path = String(node.path || '');
        byPath[path] = node;
        parentPath[path] = parent ? String(parent.path || '') : null;
        (node.children || []).forEach(function (child) {
            index(child, node);
        });
    })(data, null);

    var root = data;
    var stack = [root];
    var current = root;

    var backBtn = document.getElementById('nm-dir-back');
    var rootBtn = document.getElementById('nm-dir-root');
    var crumb = document.getElementById('nm-dir-breadcrumb');
    var sidebarBtn = document.getElementById('nm-dir-open-sidebar');
    var scaleInput = document.getElementById('nm-dir-graph-scale');
    var scaleLabel = document.getElementById('nm-dir-graph-scale-label');
    var fitBtn = document.getElementById('nm-dir-graph-scale-fit');
    var currentScale = 100;
    if (scaleInput) {
        // Keep the control usable even when an old cached page has min=50.
        scaleInput.min = '10';
        scaleInput.step = '5';
    }

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function labelFor(name) {
        name = String(name == null ? '' : name);
        return name.length > 13 ? name.slice(0, 12) + '…' : name;
    }

    function nodeRadius(node, depth, maxDocs) {
        if (depth === 0) return 28;
        var docs = Math.max(1, parseInt(node.doc_count, 10) || 0);
        return 8 + 22 * Math.sqrt(docs / Math.max(1, maxDocs));
    }

    function leafCounts(node) {
        var counts = Object.create(null);
        function walk(n) {
            var children = n.children || [];
            var total = 0;
            if (children.length) {
                children.forEach(function (child) { total += walk(child); });
            } else {
                total = 1;
            }
            counts[String(n.path || '')] = total;
            return total;
        }
        walk(node);
        return counts;
    }

    function layout(node) {
        var leaves = leafCounts(node);
        var depths = Object.create(null);
        var nodes = [];
        (function collect(n, depth) {
            nodes.push({ node: n, depth: depth });
            depths[depth] = (depths[depth] || 0) + 1;
            (n.children || []).forEach(function (child) {
                collect(child, depth + 1);
            });
        })(node, 0);

        var maxDocs = 1;
        nodes.forEach(function (entry) {
            maxDocs = Math.max(maxDocs, parseInt(entry.node.doc_count, 10) || 0);
        });
        var maxRadius = 12;
        nodes.forEach(function (entry) {
            maxRadius = Math.max(maxRadius, nodeRadius(entry.node, entry.depth, maxDocs));
        });
        var maxDepth = 0;
        Object.keys(depths).forEach(function (d) {
            maxDepth = Math.max(maxDepth, parseInt(d, 10) || 0);
        });
        var rings = [0];
        for (var d = 1; d <= maxDepth; d++) {
            var required = (depths[d] || 0) * (2 * maxRadius + 12) / (2 * Math.PI);
            rings.push(d === 1 ? Math.max(140, required) : Math.max(rings[d - 1] + 95, required));
        }
        var maxRing = rings[rings.length - 1];
        var size = 2 * (maxRing + maxRadius + 80);
        var cx = size / 2;
        var cy = size / 2;
        var positions = Object.create(null);

        function place(n, angle, span, depth) {
            var radius = rings[depth];
            var x = cx + radius * Math.cos(angle);
            var y = cy + radius * Math.sin(angle);
            var r = nodeRadius(n, depth, maxDocs);
            positions[String(n.path || '')] = {
                x: x, y: y, node: n, depth: depth, r: r, angle: angle
            };
            var children = n.children || [];
            if (!children.length) return;
            var nChildren = children.length;
            var gap = nChildren > 1 ? Math.min(0.05, span * 0.15 / (nChildren - 1)) : 0;
            var usable = Math.max(span - gap * (nChildren - 1), 0.001);
            var start = depth === 0 ? -Math.PI / 2 : angle - span / 2;
            var total = 0;
            children.forEach(function (child) {
                total += leaves[String(child.path || '')];
            });
            total = total || 1;
            var cursor = start;
            children.forEach(function (child) {
                var childPath = String(child.path || '');
                var childSpan = usable * leaves[childPath] / total;
                place(child, cursor + childSpan / 2, childSpan, depth + 1);
                cursor += childSpan + gap;
            });
        }
        place(node, -Math.PI / 2, 2 * Math.PI, 0);
        return {positions: positions, maxDocs: maxDocs};
    }

    function bounds(layoutData, project) {
        var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        Object.keys(layoutData.positions).forEach(function (key) {
            var p = layoutData.positions[key];
            minX = Math.min(minX, p.x - p.r);
            maxX = Math.max(maxX, p.x + p.r);
            minY = Math.min(minY, p.y - p.r);
            maxY = Math.max(maxY, p.y + p.r);

            var name = p.depth === 0 ? project : String(p.node.name || '');
            var label = labelFor(name);
            var textW = label.length * 11;
            var lx, ly, left, right;
            if (p.depth === 0) {
                lx = p.x;
                ly = p.y + p.r + 15;
                left = lx - textW / 2;
                right = lx + textW / 2;
            } else {
                var cos = Math.cos(p.angle);
                var sin = Math.sin(p.angle);
                lx = p.x + cos * (p.r + 6);
                ly = p.y + sin * (p.r + 6);
                if (cos > 0.25) {
                    left = lx;
                    right = lx + textW;
                } else if (cos < -0.25) {
                    left = lx - textW;
                    right = lx;
                } else {
                    left = lx - textW / 2;
                    right = lx + textW / 2;
                }
            }
            minX = Math.min(minX, left);
            maxX = Math.max(maxX, right);
            minY = Math.min(minY, ly - 6);
            maxY = Math.max(maxY, ly + 6);
        });
        var pad = 16;
        return {
            x: minX - pad,
            y: minY - pad,
            w: (maxX - minX) + 2 * pad,
            h: (maxY - minY) + 2 * pad
        };
    }

    function graphMarkup(node, project) {
        var layoutData = layout(node);
        var box = bounds(layoutData, project);
        var parts = [
            '<svg class="nm-dir-graph" viewBox="' +
            box.x.toFixed(0) + ' ' + box.y.toFixed(0) + ' ' +
            box.w.toFixed(0) + ' ' + box.h.toFixed(0) + '" ' +
            'role="img" aria-label="目录关系图">'
        ];
        Object.keys(layoutData.positions).forEach(function (key) {
            var p = layoutData.positions[key];
            (p.node.children || []).forEach(function (child) {
                var cp = layoutData.positions[String(child.path || '')];
                if (!cp) return;
                var dx = cp.x - p.x;
                var dy = cp.y - p.y;
                var dist = Math.hypot(dx, dy);
                if (dist <= 0) return;
                var ux = dx / dist;
                var uy = dy / dist;
                var x1 = p.x + ux * (p.r + 1);
                var y1 = p.y + uy * (p.r + 1);
                var x2 = cp.x - ux * (cp.r + 1);
                var y2 = cp.y - uy * (cp.r + 1);
                parts.push(
                    '<line class="nm-g-edge" x1="' + x1.toFixed(1) + '" y1="' +
                    y1.toFixed(1) + '" x2="' + x2.toFixed(1) + '" y2="' +
                    y2.toFixed(1) + '"/>'
                );
            });
        });

        Object.keys(layoutData.positions).forEach(function (key) {
            var p = layoutData.positions[key];
            var n = p.node;
            var depth = p.depth;
            var r = p.r;
            var name = depth === 0 ? project : String(n.name || '');
            var title = name + '：' + (parseInt(n.doc_count, 10) || 0) + ' 篇，' +
                (parseInt(n.section_count, 10) || 0) + ' 节';
            if (n.recent_count) title += '；近 30 天 ' + n.recent_count + ' 篇';
            if (n.date) title += '；最近 ' + n.date;
            if (n.inlinks) title += '；被引 ' + n.inlinks;

            var fill = '#ffffff';
            var stroke = '#cfd8dc';
            if (depth === 0) {
                fill = '#eceff1';
                stroke = '#90a4ae';
            } else if (n.heat === 'hot') {
                fill = '#e3f2fd';
                stroke = '#42a5f5';
            } else if (n.heat === 'warm') {
                fill = '#f4f9ff';
                stroke = '#90caf9';
            }

            if (depth === 0 && !n.path) {
                parts.push('<g class="nm-g-node nm-g-root">');
            } else {
                parts.push(
                    '<a class="nm-g-node" href="#' + esc(n.id) + '" data-dir-path="' +
                    esc(n.path) + '">'
                );
            }
            parts.push('<title>' + esc(title) + '</title>');
            parts.push(
                '<circle cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) +
                '" r="' + r.toFixed(1) + '" fill="' + fill + '" stroke="' +
                stroke + '"/>'
            );
            if (r >= 12) {
                parts.push(
                    '<text x="' + p.x.toFixed(1) + '" y="' + (p.y + 3).toFixed(1) +
                    '" font-size="9" text-anchor="middle" font-family="sans-serif" ' +
                    'fill="#546e7a">' + esc(parseInt(n.doc_count, 10) || 0) + '</text>'
                );
            }
            if (n.recent_count) {
                parts.push(
                    '<circle cx="' + (p.x + r * 0.7).toFixed(1) + '" cy="' +
                    (p.y - r * 0.7).toFixed(1) + '" r="3" fill="#43a047"/>'
                );
            }
            var lx, ly, anchor;
            if (depth === 0) {
                lx = p.x;
                ly = p.y + r + 15;
                anchor = 'middle';
            } else {
                var cos = Math.cos(p.angle);
                var sin = Math.sin(p.angle);
                lx = p.x + cos * (r + 6);
                ly = p.y + sin * (r + 6);
                anchor = cos > 0.25 ? 'start' : (cos < -0.25 ? 'end' : 'middle');
            }
            parts.push(
                '<text x="' + lx.toFixed(1) + '" y="' + ly.toFixed(1) +
                '" font-size="11" text-anchor="' + anchor +
                '" dominant-baseline="middle" font-family="sans-serif" ' +
                'fill="#37474f">' + esc(labelFor(name)) + '</text>'
            );
            parts.push(depth === 0 && !n.path ? '</g>' : '</a>');
        });
        parts.push('</svg>');
        return parts.join('');
    }

    function ancestorsOf(node) {
        var chain = [];
        var p = node;
        while (p) {
            chain.unshift(p);
            var parent = parentPath[String(p.path || '')];
            p = parent == null ? null : byPath[parent];
        }
        return chain;
    }

    var fitWidth = 0;
    var fitHeight = 0;

    function computeFitSize() {
        var vb = graph && graph.viewBox && graph.viewBox.baseVal;
        if (!vb || !vb.width || !vb.height) return;
        var style = window.getComputedStyle(wrap);
        var padX = (parseFloat(style.paddingLeft) || 0) +
                   (parseFloat(style.paddingRight) || 0);
        var padY = (parseFloat(style.paddingTop) || 0) +
                   (parseFloat(style.paddingBottom) || 0);
        var availW = Math.max(100, wrap.clientWidth - padX - 12);
        var availH = Math.max(
            180,
            Math.min(wrap.clientHeight || window.innerHeight * 0.8,
                     window.innerHeight * 0.78) - padY - 12
        );
        // Natural size is 1 viewBox unit = 1 CSS px.  Small graphs stay
        // small; large graphs are reduced until the whole graph fits.
        var naturalScale = Math.min(1, availW / vb.width, availH / vb.height);
        fitWidth = Math.max(1, vb.width * naturalScale);
        fitHeight = Math.max(1, vb.height * naturalScale);
    }

    function applyScale(value) {
        if (!graph) return;
        if (!fitWidth || !fitHeight) computeFitSize();
        currentScale = value;
        graph.style.width = Math.round(fitWidth * value / 100) + 'px';
        graph.style.height = 'auto';
        if (scaleLabel) scaleLabel.textContent = value + '%';
        if (scaleInput) scaleInput.value = String(value);
    }

    function resetToFit() {
        fitWidth = 0;
        fitHeight = 0;
        computeFitSize();
        applyScale(100);
    }

    function fitValue() {
        return 100;
    }

    function updateNav() {
        if (backBtn) backBtn.disabled = stack.length <= 1;
        if (rootBtn) rootBtn.disabled = current === root;
        if (sidebarBtn) sidebarBtn.disabled = !current.path;
        if (!crumb) return;
        crumb.innerHTML = '';
        var chain = ancestorsOf(current);
        chain.forEach(function (node, index) {
            if (index) {
                var sep = document.createElement('span');
                sep.className = 'nm-crumb-sep';
                sep.textContent = ' / ';
                crumb.appendChild(sep);
            }
            var el = document.createElement(index === chain.length - 1 ? 'span' : 'button');
            el.className = 'nm-crumb';
            el.textContent = String(node.name || '');
            if (index !== chain.length - 1) {
                el.type = 'button';
                el.addEventListener('click', function () { navigate(node); });
            }
            crumb.appendChild(el);
        });
    }

    function updateSidebarCurrent(node) {
        var label = document.getElementById('nm-sidebar-current');
        if (!label) return;
        var path = String(node && node.path || '');
        label.textContent = path || '首页';
        label.title = path ? ('当前目录 ' + path) : '项目首页';
    }

    function render(node) {
        current = node;
        updateSidebarCurrent(node);
        // Replace the whole SVG, not its innerHTML.  Assigning an <svg> string
        // as innerHTML of an existing <svg> creates a nested SVG and breaks
        // the viewBox/coordinate system.
        wrap.innerHTML = graphMarkup(node, String(node.name || ''));
        graph = wrap.querySelector('.nm-dir-graph');
        graph.querySelectorAll('a.nm-g-node').forEach(function (link) {
            link.addEventListener('click', function (event) {
                event.preventDefault();
                var path = link.getAttribute('data-dir-path');
                var target = byPath[path];
                if (!target) return;
                // Always keep the sidebar in sync with the selected node,
                // including the current root node (whose graph node may be
                // clicked without causing a new drill-down).
                if (window.nmRevealSidebarDir) window.nmRevealSidebarDir(target.id);
                if (event.shiftKey || event.altKey) return;
                if (target !== current) {
                    // A leaf directory is still a directory the user wants to
                    // focus; don't make its click a no-op.
                    navigate(target);
                }
            });
        });
        resetToFit();
        updateNav();
        if (node.path && window.nmRevealSidebarDir) {
            window.nmRevealSidebarDir(node.id);
        }
    }

    function navigate(node) {
        if (!node || node === current) return;
        stack.push(node);
        current = node;
        try {
            history.pushState(
                {nmDirPath: String(node.path || '')},
                '',
                '#nm-dir=' + encodeURIComponent(String(node.path || ''))
            );
        } catch (e) {}
        render(node);
    }

    function pathFromLocation() {
        var hash = String(location.hash || '');
        var marker = '#nm-dir=';
        if (hash.indexOf(marker) !== 0) return '';
        try {
            return decodeURIComponent(hash.slice(marker.length));
        } catch (e) {
            return '';
        }
    }

    function popToPath(path) {
        var node = byPath[path] || root;
        stack = ancestorsOf(node);
        current = node;
        render(node);
    }

    if (backBtn) {
        backBtn.addEventListener('click', function () {
            if (stack.length > 1) history.back();
        });
    }
    if (rootBtn) {
        rootBtn.addEventListener('click', function () {
            if (current !== root) navigate(root);
        });
    }
    if (sidebarBtn) {
        sidebarBtn.addEventListener('click', function () {
            if (current.path && window.nmRevealSidebarDir) {
                window.nmRevealSidebarDir(current.id);
            }
        });
    }
    if (scaleInput) {
        scaleInput.addEventListener('input', function () {
            var value = parseInt(scaleInput.value, 10) || 100;
            applyScale(value);
        });
    }
    if (fitBtn) {
        fitBtn.addEventListener('click', function () {
            applyScale(fitValue());
        });
    }
    window.addEventListener('popstate', function (event) {
        var path = (event.state && event.state.nmDirPath != null)
            ? String(event.state.nmDirPath)
            : pathFromLocation();
        popToPath(path);
    });

    var initialPath = pathFromLocation();
    var initialNode = byPath[initialPath] || root;
    stack = ancestorsOf(initialNode);
    current = initialNode;
    try {
        history.replaceState(
            {nmDirPath: String(initialNode.path || '')},
            '',
            initialPath ? '#nm-dir=' + encodeURIComponent(initialPath) : location.pathname + location.search
        );
    } catch (e) {}
    render(initialNode);

    // Always start a view at its fitted size.  A saved zoom from another
    // directory would make the first render overflow and bring back scrollbars.
    applyScale(100);
})();
