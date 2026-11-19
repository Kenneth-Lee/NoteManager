"""Shared browser bootstrap JS for NoteManager pages.

Each browser tab keeps a stable client id in ``window.name``.  The id survives
same-tab navigation/reload but is not shared with tabs opened from the page,
which is what the daemon uses to distinguish the main page from secondary
pages.
"""

CLIENT_BOOTSTRAP_JS = r"""
(function () {
    var ID_RE = /^nm-[A-Za-z0-9_-]{4,40}$/;
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
                }
            } catch (err) {}
        });
    }
})();
"""
