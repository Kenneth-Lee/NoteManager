"""Web UI server for NoteManager daemon.

Serves a control panel, project HTML files, and SSE event stream.
"""

import json
import os
import urllib.parse
import weakref
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import threading
import time


class EventBroadcaster:
    """Manages SSE client connections and broadcasts events."""

    def __init__(self):
        self._clients = []  # list of writable file-like objects
        self._lock = threading.Lock()

    def add_client(self, wfile) -> None:
        with self._lock:
            self._clients.append(wfile)

    def remove_client(self, wfile) -> None:
        with self._lock:
            try:
                self._clients.remove(wfile)
            except ValueError:
                pass

    def broadcast(self, event_type: str, data: dict = None) -> None:
        """Send an SSE event to all connected clients."""
        payload = json.dumps(data or {}, ensure_ascii=False)
        message = f"event: {event_type}\ndata: {payload}\n\n"
        encoded = message.encode("utf-8")
        dead = []
        with self._lock:
            for wfile in self._clients:
                try:
                    wfile.write(encoded)
                    wfile.flush()
                except Exception:
                    dead.append(wfile)
            for wfile in dead:
                self._clients.remove(wfile)


# Global broadcaster instance
broadcaster = EventBroadcaster()


class WebHandler(BaseHTTPRequestHandler):
    """HTTP handler for control panel, project files, and SSE."""

    daemon_ref = None  # Set by daemon at startup

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/" or path == "":
            self._serve_panel()
        elif path == "/api/events":
            self._handle_sse()
        elif path.startswith("/api/"):
            self._handle_api_get(path)
        elif path.startswith("/p/"):
            self._serve_project_file(path)
        else:
            self._send_error(404, "Not Found")

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/"):
            self._handle_api_post(path)
        else:
            self._send_error(404, "Not Found")

    def _handle_sse(self):
        """Handle SSE connection: keep alive and push events."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        # Send initial state
        status = self.daemon_ref.get_status()
        self._sse_send("init", status.get("data", {}))

        # Register as client
        broadcaster.add_client(self.wfile)
        try:
            # Keep connection alive with heartbeat
            while not self.close_connection:
                time.sleep(1)
                try:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                except Exception:
                    break
        finally:
            broadcaster.remove_client(self.wfile)

    def _sse_send(self, event_type: str, data: dict):
        """Send a single SSE event."""
        payload = json.dumps(data, ensure_ascii=False)
        msg = f"event: {event_type}\ndata: {payload}\n\n"
        self.wfile.write(msg.encode("utf-8"))
        self.wfile.flush()

    def _serve_panel(self):
        """Serve the control panel HTML."""
        html = _render_panel(self.daemon_ref)
        self._send_html(html)

    def _handle_api_get(self, path):
        if path == "/api/status":
            resp = self.daemon_ref.get_status()
        elif path == "/api/list":
            resp = self.daemon_ref.list_directories()
        else:
            self._send_error(404, "Unknown API endpoint")
            return
        self._send_json(resp)

    def _handle_api_post(self, path):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(body) if body else {}
        except (json.JSONDecodeError, ValueError):
            data = {}

        if path == "/api/add":
            directory = data.get("directory", "")
            resp = self.daemon_ref.add_directory(directory)
        elif path == "/api/remove":
            directory = data.get("directory", "")
            resp = self.daemon_ref.remove_directory(directory)
        elif path == "/api/stop":
            resp = self.daemon_ref.stop()
        elif path == "/api/rebuild":
            directory = data.get("directory", "")
            resp = self.daemon_ref.rebuild_directory(directory)
        else:
            self._send_error(404, "Unknown API endpoint")
            return
        self._send_json(resp)

    def _serve_project_file(self, path):
        parts = path[3:].split("/", 1)
        if len(parts) < 2 or (parts[0] == "" and len(parts) == 1):
            idx_str = parts[0]
            file_path = "index.html"
        elif len(parts) < 2:
            idx_str = parts[0].rstrip("/")
            file_path = "index.html"
        else:
            idx_str, file_path = parts

        if not file_path:
            file_path = "index.html"

        try:
            idx = int(idx_str)
        except ValueError:
            self._send_error(400, "Invalid project index")
            return

        with self.daemon_ref._lock:
            dirs = list(self.daemon_ref._observers.keys())

        if idx < 0 or idx >= len(dirs):
            self._send_error(404, "Project not found")
            return

        project_dir = Path(dirs[idx])
        html_dir = project_dir / ".NoteManager" / "html"
        file_path = urllib.parse.unquote(file_path)
        target = (html_dir / file_path).resolve()

        if not str(target).startswith(str(html_dir.resolve())):
            self._send_error(403, "Forbidden")
            return

        if not target.is_file():
            self._send_error(404, "File not found")
            return

        self._send_file(target)

    def _send_html(self, html):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _send_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _send_file(self, path):
        ext = path.suffix.lower()
        mime_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }
        mime = mime_types.get(ext, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        size = path.stat().st_size
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with open(path, "rb") as f:
            self.wfile.write(f.read())

    def _send_error(self, code, message):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(message.encode("utf-8"))


def notify_event(event_type: str, data: dict = None):
    """Public function for daemon to broadcast events to all web clients."""
    broadcaster.broadcast(event_type, data)


def start_web_server(daemon_ref, port: int) -> HTTPServer:
    """Start the HTTP server in a thread."""
    WebHandler.daemon_ref = daemon_ref
    server = HTTPServer(("127.0.0.1", port), WebHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _render_panel(daemon_ref) -> str:
    """Render the control panel HTML."""
    with daemon_ref._lock:
        dirs = list(daemon_ref._observers.keys())

    projects_html = ""
    for i, d in enumerate(dirs):
        nm_dir = Path(d) / ".NoteManager" / "status.json"
        detail = {}
        if nm_dir.exists():
            try:
                detail = json.loads(nm_dir.read_text(encoding="utf-8"))
            except Exception:
                detail = {"error": "无法读取状态"}

        name = Path(d).name
        files = detail.get("file_count", "?")
        labels = detail.get("label_count", "?")
        last = detail.get("last_build", "?")
        err = detail.get("error")
        if err:
            status_text = f'<span class="error">{err}</span>'
        else:
            status_text = f'{files} 文件, {labels} 标签, {last[:19]}'

        projects_html += f"""\
<tr data-dir="{d}">
  <td><a href="/p/{i}/">{name}</a></td>
  <td class="path">{d}</td>
  <td>{status_text}</td>
  <td>
    <button onclick="rebuildProject({i},'{d}')">重建</button>
    <button class="danger" onclick="removeProject({i},'{d}')">移除</button>
  </td>
</tr>
"""

    return f"""\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NoteManager</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f5f5f5;
    color: #333;
    line-height: 1.6;
}}
.container {{ max-width: 960px; margin: 0 auto; padding: 20px; }}
h1 {{ margin-bottom: 20px; font-size: 24px; }}
.card {{
    background: white;
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}}
.card h2 {{ font-size: 16px; color: #666; margin-bottom: 12px; }}
.add-form {{ display: flex; gap: 8px; }}
.add-form input {{
    flex: 1;
    padding: 8px 12px;
    border: 1px solid #ddd;
    border-radius: 4px;
    font-size: 14px;
}}
button {{
    padding: 8px 16px;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 14px;
    background: #0066cc;
    color: white;
}}
button:hover {{ background: #0052a3; }}
button.danger {{ background: #cc3300; }}
button.danger:hover {{ background: #a32800; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ text-align: left; padding: 10px 8px; border-bottom: 1px solid #eee; }}
th {{ color: #666; font-weight: 600; font-size: 13px; }}
td.path {{ font-family: monospace; font-size: 12px; color: #888; max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
td a {{ color: #0066cc; text-decoration: none; font-weight: 500; }}
td a:hover {{ text-decoration: underline; }}
.empty {{ text-align: center; color: #999; padding: 40px 0; }}
.error {{ color: #cc3300; }}
#toast {{
    position: fixed; bottom: 20px; right: 20px;
    padding: 12px 20px; border-radius: 4px;
    background: #333; color: white; font-size: 14px;
    display: none; z-index: 1000;
}}
</style>
</head>
<body>
<div class="container">
<h1>NoteManager</h1>

<div class="card">
<h2>添加目录</h2>
<div class="add-form">
  <input type="text" id="dir-input" placeholder="输入目录路径...">
  <button onclick="addProject()">添加</button>
</div>
</div>

<div class="card">
<h2>监控目录</h2>
<table>
<thead><tr><th>名称</th><th>路径</th><th>状态</th><th>操作</th></tr></thead>
<tbody id="projects">
{projects_html if projects_html else '<tr><td colspan="4" class="empty">暂无监控目录</td></tr>'}
</tbody>
</table>
</div>

</div>
<div id="toast"></div>

<script>
function showToast(msg, duration) {{
    duration = duration || 2000;
    var t = document.getElementById('toast');
    t.textContent = msg;
    t.style.display = 'block';
    setTimeout(function() {{ t.style.display = 'none'; }}, duration);
}}

function addProject() {{
    var input = document.getElementById('dir-input');
    var dir = input.value.trim();
    if (!dir) return;
    fetch('/api/add', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{directory: dir}})
    }}).then(function(r) {{ return r.json(); }}).then(function(d) {{
        if (d.ok) showToast(d.message);
        else showToast('错误: ' + d.error, 3000);
    }});
}}

function removeProject(idx, dir) {{
    if (!confirm('确定移除 ' + dir + '？')) return;
    fetch('/api/remove', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{directory: dir}})
    }}).then(function(r) {{ return r.json(); }}).then(function(d) {{
        if (d.ok) showToast(d.message);
        else showToast('错误: ' + d.error, 3000);
    }});
}}

function rebuildProject(idx, dir) {{
    showToast('正在重建...');
    fetch('/api/rebuild', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{directory: dir}})
    }}).then(function(r) {{ return r.json(); }}).then(function(d) {{
        if (d.ok) showToast(d.message);
        else showToast('错误: ' + d.error, 3000);
    }});
}}

function refreshProjects(data) {{
    if (!data || !data.directories) return;
    var tbody = document.getElementById('projects');
    var html = '';
    var dirs = data.directories;
    var details = data.details || {{}};
    if (dirs.length === 0) {{
        tbody.innerHTML = '<tr><td colspan="4" class="empty">暂无监控目录</td></tr>';
        return;
    }}
    for (var i = 0; i < dirs.length; i++) {{
        var d = dirs[i];
        var name = d.split('/').pop();
        var detail = details[d] || {{}};
        var err = detail.error;
        var statusText;
        if (err) {{
            statusText = '<span class="error">' + err + '</span>';
        }} else {{
            var files = detail.file_count || '?';
            var labels = detail.label_count || '?';
            var last = (detail.last_build || '?').substring(0, 19);
            statusText = files + ' 文件, ' + labels + ' 标签, ' + last;
        }}
        html += '<tr data-dir="' + d + '">';
        html += '<td><a href="/p/' + i + '/">' + name + '</a></td>';
        html += '<td class="path">' + d + '</td>';
        html += '<td>' + statusText + '</td>';
        html += '<td>';
        html += '<button onclick="rebuildProject(' + i + ',\\'' + d + '\\')">重建</button> ';
        html += '<button class="danger" onclick="removeProject(' + i + ',\\'' + d + '\\')">移除</button>';
        html += '</td></tr>';
    }}
    tbody.innerHTML = html;
}}

// SSE: listen for server-pushed events
var evtSource = new EventSource('/api/events');
evtSource.addEventListener('directories_changed', function(e) {{
    var data = JSON.parse(e.data);
    refreshProjects(data);
}});
evtSource.addEventListener('build_complete', function(e) {{
    var data = JSON.parse(e.data);
    refreshProjects(data);
}});
evtSource.addEventListener('init', function(e) {{
    var data = JSON.parse(e.data);
    refreshProjects(data);
}});
evtSource.onerror = function() {{
    // Reconnect is automatic for EventSource
}};

document.getElementById('dir-input').addEventListener('keydown', function(e) {{
    if (e.key === 'Enter') addProject();
}});
</script>
</body>
</html>
"""
