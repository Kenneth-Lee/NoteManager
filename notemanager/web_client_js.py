"""Shared browser bootstrap JS for NoteManager pages.

Each browser tab keeps a stable client id in ``window.name``.  The id survives
same-tab navigation/reload but is not shared with tabs opened from the page,
which is what the daemon uses to distinguish the main page from secondary
pages.
"""

CLIENT_BOOTSTRAP_JS = r"""
(function () {
    var ID_RE = /^[A-Za-z0-9_-]{1,40}$/;
    var id = '';
    try {
        var params = new URLSearchParams(window.location.search);
        var boot = params.get('nm_client_id');
        if (boot && ID_RE.test(boot)) {
            id = boot;
            params.delete('nm_client_id');
            var qs = params.toString();
            window.history.replaceState(
                null, '',
                window.location.pathname + (qs ? '?' + qs : '') + window.location.hash
            );
        }
    } catch (e) {}
    if (!id) {
        try {
            var name = String(window.name || '');
            if (ID_RE.test(name)) id = name;
        } catch (e) {}
    }
    if (!id) {
        var rnd = '';
        try {
            if (window.crypto && window.crypto.randomUUID) {
                rnd = window.crypto.randomUUID().replace(/-/g, '').slice(0, 12);
            }
        } catch (e) {}
        if (!rnd) rnd = Math.random().toString(16).slice(2) + Date.now().toString(16);
        id = 'nm-' + rnd;
    }
    try { window.name = id; } catch (e) {}
    window.NM_CLIENT_ID = id;

    function updatePageIdBadge(value) {
        var badge = document.getElementById('nm-page-id');
        if (!badge) return;
        badge.setAttribute('data-page-id', value);
        badge.textContent = '页面: ' + value;
        badge.title = '页面 ID: ' + value + '（点击复制，可用于 nmr view/search --id）';
    }
    updatePageIdBadge(id);

    var pageIdBadge = document.getElementById('nm-page-id');
    if (pageIdBadge) {
        var copyPageId = function () {
            var value = pageIdBadge.getAttribute('data-page-id') || window.NM_CLIENT_ID || '';
            if (!value) return;
            var done = function () {
                pageIdBadge.textContent = '已复制: ' + value;
                pageIdBadge.classList.add('copied');
                setTimeout(function () {
                    pageIdBadge.textContent = '页面: ' + value;
                    pageIdBadge.classList.remove('copied');
                }, 900);
            };
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(value).then(done).catch(function () {});
            } else {
                var ta = document.createElement('textarea');
                ta.value = value;
                ta.style.position = 'fixed';
                ta.style.opacity = '0';
                document.body.appendChild(ta);
                ta.select();
                try { document.execCommand('copy'); done(); } catch (e) {}
                document.body.removeChild(ta);
            }
        };
        pageIdBadge.addEventListener('click', copyPageId);
        pageIdBadge.addEventListener('keydown', function (event) {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                copyPageId();
            }
        });
    }

    window.NMPingActive = function () {
        try {
            fetch('/api/client_active', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({client_id: window.NM_CLIENT_ID}),
                keepalive: true
            }).catch(function () {});
        } catch (e) {}
    };
    window.addEventListener('focus', window.NMPingActive);
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) window.NMPingActive();
    });
    window.NMPingActive();

    var es = null;
    try {
        es = new EventSource(
            '/api/events?client_id=' + encodeURIComponent(window.NM_CLIENT_ID) +
            '&path=' + encodeURIComponent(window.location.pathname)
        );
    } catch (e) {}
    window.NM_EVENT_SOURCE = es;
    if (es) {
        es.addEventListener('init', function (e) {
            try {
                var data = JSON.parse(e.data || '{}');
                if (data && data.client_id && data.client_id !== window.NM_CLIENT_ID) {
                    window.NM_CLIENT_ID = data.client_id;
                    try { window.name = data.client_id; } catch (err) {}
                    updatePageIdBadge(data.client_id);
                }
            } catch (err) {}
        });
    }
})();
"""
