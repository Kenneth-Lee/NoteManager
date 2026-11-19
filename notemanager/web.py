"""Web UI server for NoteManager daemon.

Serves a control panel, project HTML files, and SSE event stream.
"""

import email.utils
import json
import logging
import os
import re
import sys
import urllib.parse
from html import escape as html_escape
import weakref
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time

from notemanager.web_client_js import CLIENT_BOOTSTRAP_JS

logger = logging.getLogger(__name__)


# Errors that merely mean the browser dropped the connection (common when an
# SSE client navigates away). They are expected on every disconnect and must
# not pollute daemon.log with scary tracebacks.
_EXPECTED_CONN_ERRORS = (ConnectionError, TimeoutError)


class WebClient:
    """One connected browser page (SSE connection) plus routing metadata."""

    __slots__ = ("id", "wfile", "path", "project", "connected_at", "last_active")

    def __init__(self, client_id: str, wfile, path: str, project: int | None, now: float):
        self.id = client_id
        self.wfile = wfile
        self.path = path or "/"
        self.project = project
        self.connected_at = now
        self.last_active = now


class EventBroadcaster:
    """Manages SSE client connections and targeted/broadcast event delivery."""

    def __init__(self):
        self._clients: dict[str, WebClient] = {}
        self._lock = threading.Lock()

    def add_client(self, client_id: str, wfile, path: str = "/",
                   project: int | None = None) -> WebClient:
        """Register a page, replacing any previous connection with the same id.

        Replacing (rather than rejecting) makes tab reloads/navigations keep the
        same logical page and therefore the same main-page role.
        """
        now = time.time()
        client = WebClient(client_id, wfile, path, project, now)
        with self._lock:
            self._clients[client_id] = client
        return client

    def remove_client(self, wfile) -> WebClient | None:
        """Remove the client owning *wfile*. Old takeover connections are ignored."""
        with self._lock:
            for client_id, client in list(self._clients.items()):
                if client.wfile is wfile:
                    del self._clients[client_id]
                    return client
        return None

    def get_client(self, client_id: str) -> WebClient | None:
        with self._lock:
            return self._clients.get(client_id)

    def list_clients(self) -> list[WebClient]:
        with self._lock:
            return list(self._clients.values())

    def has_clients(self) -> bool:
        """Whether any SSE client (web UI page) is currently connected."""
        with self._lock:
            return bool(self._clients)

    def touch(self, client_id: str, when: float | None = None) -> bool:
        with self._lock:
            client = self._clients.get(client_id)
            if not client:
                return False
            client.last_active = time.time() if when is None else when
            return True

    def send_to(self, client_id: str, event_type: str, data: dict = None) -> bool:
        """Send one SSE event to a single page. Returns True when written."""
        payload = json.dumps(data or {}, ensure_ascii=False)
        message = f"event: {event_type}\ndata: {payload}\n\n"
        encoded = message.encode("utf-8")
        with self._lock:
            client = self._clients.get(client_id)
            if not client:
                return False
            try:
                client.wfile.write(encoded)
                client.wfile.flush()
                client.last_active = time.time()
                return True
            except Exception:
                self._clients.pop(client_id, None)
                return False

    def broadcast(self, event_type: str, data: dict = None) -> None:
        """Send an SSE event to all connected clients."""
        payload = json.dumps(data or {}, ensure_ascii=False)
        message = f"event: {event_type}\ndata: {payload}\n\n"
        encoded = message.encode("utf-8")
        dead = []
        with self._lock:
            for client in self._clients.values():
                try:
                    client.wfile.write(encoded)
                    client.wfile.flush()
                except Exception:
                    dead.append(client.id)
            for client_id in dead:
                self._clients.pop(client_id, None)


# Global broadcaster instance
broadcaster = EventBroadcaster()


def _parse_multipart_form(content_type: str, body: bytes) -> dict:
    """解析 multipart/form-data，字段名为 str，文件字段为 (bytes, filename, content_type)。"""
    m = re.search(r"boundary=([^;\s]+)", content_type, re.I)
    if not m:
        raise ValueError("multipart 缺少 boundary")
    boundary = m.group(1).strip().strip('"').encode("ascii")
    delim = b"--" + boundary
    parts = body.split(delim)
    out: dict = {}
    for raw in parts:
        chunk = raw.strip()
        if not chunk or chunk == b"--":
            continue
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        sep = chunk.find(b"\r\n\r\n")
        if sep < 0:
            continue
        headers_blob = chunk[:sep].decode("latin-1", errors="replace")
        content = chunk[sep + 4 :]
        if content.endswith(b"\r\n"):
            content = content[:-2]
        cd_line = ""
        ct_part = "application/octet-stream"
        for line in headers_blob.split("\r\n"):
            ll = line.lower()
            if ll.startswith("content-disposition:"):
                cd_line = line
            elif ll.startswith("content-type:"):
                ct_part = line.split(":", 1)[1].strip()
        if not cd_line:
            continue
        nm = re.search(r'name="([^"]+)"', cd_line)
        if not nm:
            continue
        name = nm.group(1)
        fn_m = re.search(r'filename="([^"]*)"', cd_line)
        if fn_m:
            out[name] = (content, fn_m.group(1), ct_part)
        else:
            try:
                out[name] = content.decode("utf-8")
            except UnicodeDecodeError:
                out[name] = content.decode("latin-1", errors="replace")
    return out


class WebHandler(BaseHTTPRequestHandler):
    """HTTP handler for control panel, project files, and SSE."""

    daemon_ref = None  # Set by daemon at startup

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "":
            self._serve_panel()
        elif path == "/api/events":
            self._handle_sse()
        elif path.startswith("/api/"):
            self._handle_api_get(path)
        elif path.startswith("/temp/"):
            if path == "/temp/open":
                self._serve_temp_open(parsed.query)
            elif path == "/temp/asset":
                self._serve_temp_asset(parsed.query)
            elif path == "/temp/check":
                self._serve_temp_check(parsed.query)
            else:
                self._serve_temp_file(path)
        elif path.startswith("/p/"):
            self._serve_project_file(path)
        else:
            self._send_error(404, "Not Found")

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/api/"):
            self._send_error(404, "Not Found")
            return
        if not self._origin_allowed():
            self._send_error(403, "Forbidden")
            return
        ctype = self.headers.get("Content-Type", "")
        if path == "/api/paste_image" and "multipart/form-data" in ctype.lower():
            self._handle_paste_image_post(ctype)
            return
        self._handle_api_post(path)

    def _origin_allowed(self) -> bool:
        """Reject cross-origin SSE clients (they could hijack the main page)."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        if origin == "null":
            return False
        host = self.headers.get("Host")
        if not host:
            return False
        try:
            return urllib.parse.urlparse(origin).netloc.lower() == host.lower()
        except ValueError:
            return False

    def _sse_request_path(self, params: dict) -> str:
        """Page path sent by the client, falling back to the Referer header."""
        path = (params.get("path") or [""])[0]
        if path:
            return path
        referer = self.headers.get("Referer") or ""
        if referer:
            try:
                return urllib.parse.urlparse(referer).path or "/"
            except ValueError:
                pass
        return "/"

    def _handle_sse(self):
        """Handle SSE connection: keep alive and push events."""
        if not self._origin_allowed():
            self._send_error(403, "Forbidden")
            return
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query or "", keep_blank_values=True)
        client_id = (params.get("client_id") or [""])[0]
        path = self._sse_request_path(params)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        info = self.daemon_ref.register_web_client(self.wfile, client_id, path)
        try:
            # Send initial state (includes the assigned client id and main role).
            status = self.daemon_ref.get_status()
            data = dict(status.get("data", {}))
            data.update(info)
            self._sse_send("init", data)

            # SSE is one-way; read the request stream in a helper thread so a
            # browser tab close/navigation is noticed immediately instead of
            # waiting for a heartbeat write to fail (which can be delayed by
            # socket buffers).  This keeps main-page handoff timely.
            def _watch_client_close():
                try:
                    while not self.close_connection:
                        if not self.rfile.read(1):
                            break
                except Exception:
                    pass
                self.close_connection = True

            threading.Thread(target=_watch_client_close, daemon=True).start()

            # Keep connection alive with heartbeat
            while not self.close_connection:
                time.sleep(1)
                try:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                except Exception:
                    break
        finally:
            self.daemon_ref.unregister_web_client(self.wfile)
            # Mark the connection dead so BaseHTTPRequestHandler.handle() does
            # not loop back into handle_one_request() and read from the broken
            # socket. On Windows that extra read raises ConnectionAbortedError
            # [WinError 10053]; on POSIX it harmlessly returns EOF.
            self.close_connection = True

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
        elif path == "/api/export_zip":
            try:
                pj = int(data.get("project", -1))
            except (TypeError, ValueError):
                self._send_json({"ok": False, "error": "无效工程编号"})
                return
            try:
                blob, fname = self.daemon_ref.export_project_mobile_zip(pj)
            except FileNotFoundError as e:
                self._send_json({"ok": False, "error": str(e)})
                return
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})
                return
            self._send_zip(blob, fname)
            return
        elif path == "/api/default":
            resp = self.daemon_ref.handle_default(data)
        elif path == "/api/client_active":
            resp = self.daemon_ref.touch_web_client(str(data.get("client_id", "")))
        elif path == "/api/save_source":
            try:
                pj = int(data.get("project", -1))
            except (TypeError, ValueError):
                self._send_json({"ok": False, "error": "无效工程编号"})
                return
            resp = self.daemon_ref.save_managed_source(
                pj,
                str(data.get("source_rel_path", "")),
                str(data.get("content", "")),
            )
        elif path == "/api/create_note":
            try:
                pj = int(data.get("project", -1))
            except (TypeError, ValueError):
                self._send_json({"ok": False, "error": "无效工程编号"})
                return
            resp = self.daemon_ref.create_note_same_dir(
                pj,
                str(data.get("source_rel_path", "")),
                str(data.get("filename", "")),
                str(data.get("source_ext", ".md")),
            )
        elif path == "/api/delete_note":
            try:
                pj = int(data.get("project", -1))
            except (TypeError, ValueError):
                self._send_json({"ok": False, "error": "无效工程编号"})
                return
            resp = self.daemon_ref.delete_managed_source(
                pj,
                str(data.get("source_rel_path", "")),
            )
        elif path == "/api/grep_start":
            try:
                pj = int(data.get("project", -1))
            except (TypeError, ValueError):
                self._send_json({"ok": False, "error": "无效工程编号"})
                return
            query = str(data.get("query", ""))
            filters = data.get("filters")
            if not isinstance(filters, list):
                filters = []
            resp = self.daemon_ref.start_grep_search(pj, query, filters)
        elif path == "/api/grep_status":
            try:
                jid = int(data.get("job_id", -1))
                since = int(data.get("since", 0))
            except (TypeError, ValueError):
                self._send_json({"ok": False, "error": "参数错误"})
                return
            resp = self.daemon_ref.get_grep_status(jid, since)
        elif path == "/api/grep_cancel":
            try:
                jid = int(data.get("job_id", -1))
            except (TypeError, ValueError):
                self._send_json({"ok": False, "error": "参数错误"})
                return
            resp = self.daemon_ref.cancel_grep_search(jid)
        else:
            self._send_error(404, "Unknown API endpoint")
            return
        self._send_json(resp)

    def _handle_paste_image_post(self, content_type: str):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else b""
            fields = _parse_multipart_form(content_type, body)
        except (ValueError, OSError) as e:
            self._send_json({"ok": False, "error": str(e)})
            return
        try:
            project = int(fields.get("project", -1))
        except (TypeError, ValueError):
            self._send_json({"ok": False, "error": "无效工程编号"})
            return
        rel = str(fields.get("source_rel_path", ""))
        file_entry = fields.get("file")
        if not isinstance(file_entry, tuple) or len(file_entry) != 3:
            self._send_json({"ok": False, "error": "缺少文件字段"})
            return
        raw, _filename, ct = file_entry
        if not isinstance(raw, bytes):
            self._send_json({"ok": False, "error": "无效文件数据"})
            return
        resp = self.daemon_ref.save_paste_image(project, rel, raw, ct)
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
        stat = path.stat()
        # 静态资源统一 no-cache + Last-Modified 重验证：浏览器每次条件请求,
        # 未变更拿 304,变更后拿新内容。避免 JS/CSS 被启发式缓存后更新不生效。
        try:
            last_modified = email.utils.formatdate(stat.st_mtime, usegmt=True)
        except Exception:
            last_modified = None
        ims = self.headers.get("If-Modified-Since")
        if ims and last_modified:
            try:
                since = email.utils.parsedate_to_datetime(ims).timestamp()
                if int(since) >= int(stat.st_mtime):
                    self.send_response(304)
                    self.send_header("Cache-Control", "no-cache, must-revalidate")
                    self.end_headers()
                    return
            except Exception:
                pass
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        if last_modified:
            self.send_header("Last-Modified", last_modified)
        size = stat.st_size
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with open(path, "rb") as f:
            self.wfile.write(f.read())

    def _send_error(self, code, message):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(message.encode("utf-8"))

    def _send_zip(self, data: bytes, filename: str):
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename) or "export.html.zip"
        self.send_header("Content-Disposition", f'attachment; filename="{safe}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_temp_file(self, path):
        """Serve temporary rendered single-file pages."""
        temp_root = self.daemon_ref.get_temp_view_dir().resolve()
        rel = urllib.parse.unquote(path[len("/temp/"):])
        if not rel:
            rel = "index.html"
        target = (temp_root / rel).resolve()
        if not str(target).startswith(str(temp_root)):
            self._send_error(403, "Forbidden")
            return
        if not target.is_file():
            self._send_error(404, "File not found")
            return
        if target.suffix.lower() == ".html":
            self.daemon_ref._refresh_temp_view_if_stale(target)
        self._send_file(target)

    def _serve_temp_open(self, query: str):
        """Render a local note source and redirect to /temp/<key>.html."""
        params = urllib.parse.parse_qs(query or "", keep_blank_values=False)
        raw = (params.get("file") or [""])[0]
        base_raw = (params.get("base") or [""])[0]
        if not raw:
            self._send_error(400, "Missing file")
            return
        source = Path(raw).expanduser().resolve()
        if not self._within_base(source, base_raw):
            self._send_error(403, "Forbidden")
            return
        if not source.is_file():
            self._send_error(404, "File not found")
            return
        if source.suffix.lower() not in (".md", ".rst", ".txt"):
            self._send_error(400, "Only .md/.rst/.txt are supported")
            return
        try:
            target_url = self.daemon_ref._render_temp_view(source)
        except (OSError, UnicodeDecodeError):
            self._send_error(500, "Render failed")
            return
        self.send_response(302)
        self.send_header("Location", target_url)
        self.end_headers()

    def _serve_temp_asset(self, query: str):
        """Serve local file assets referenced by /temp pages."""
        params = urllib.parse.parse_qs(query or "", keep_blank_values=False)
        raw = (params.get("file") or [""])[0]
        base_raw = (params.get("base") or [""])[0]
        if not raw:
            self._send_error(400, "Missing file")
            return
        target = Path(raw).expanduser().resolve()
        if not self._within_base(target, base_raw):
            self._send_error(403, "Forbidden")
            return
        if not target.is_file():
            self._send_error(404, "File not found")
            return
        self._send_file(target)

    def _serve_temp_check(self, query: str):
        """Return whether a temp-rendered file is stale and should be reloaded."""
        params = urllib.parse.parse_qs(query or "", keep_blank_values=False)
        raw = (params.get("file") or [""])[0]
        if not raw:
            self._send_json({"ok": False, "reload": False, "error": "Missing file"})
            return
        source = Path(raw).expanduser().resolve()
        if not source.is_file():
            self._send_json({"ok": True, "reload": False})
            return
        try:
            current_mtime = source.stat().st_mtime_ns
        except OSError:
            self._send_json({"ok": False, "reload": False, "error": "Stat failed"})
            return

        key = self.daemon_ref._temp_key_for_source(source)
        meta_path = self.daemon_ref.get_temp_view_dir() / f"{key}.meta.json"
        if not meta_path.is_file():
            self.daemon_ref._render_temp_view(source, force=True)
            self._send_json({"ok": True, "reload": True, "source_path": str(source)})
            return
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            self.daemon_ref._render_temp_view(source, force=True)
            self._send_json({"ok": True, "reload": True, "source_path": str(source)})
            return

        current_sig = self.daemon_ref._current_temp_renderer_signature()
        stale = int(meta.get("mtime_ns", -1)) != current_mtime or str(meta.get("renderer_sig", "")) != current_sig
        if stale:
            self.daemon_ref._render_temp_view(source, force=True)
            self._send_json({"ok": True, "reload": True, "source_path": str(source)})
            return
        self._send_json({"ok": True, "reload": False, "mtime_ns": current_mtime})

    @staticmethod
    def _within_base(target: Path, base_raw: str) -> bool:
        """Allow only target under base dir (inclusive)."""
        if not base_raw:
            return False
        try:
            base = Path(base_raw).expanduser().resolve()
            t = target.resolve()
        except OSError:
            return False
        try:
            t.relative_to(base)
            return True
        except ValueError:
            return False


def _same_project_root(a: str, b: str) -> bool:
    """Compare project root paths (Windows: case-insensitive)."""
    try:
        pa = Path(a).resolve()
        pb = Path(b).resolve()
    except OSError:
        pa, pb = Path(a), Path(b)
    if os.name == "nt":
        return os.path.normcase(str(pa)) == os.path.normcase(str(pb))
    return pa == pb


def _enrich_build_complete_event(data: dict) -> dict:
    """Add numeric project index so /p/N/ pages can reload only when relevant."""
    if "project" in data:
        return data
    directory = data.get("directory")
    if not directory:
        return data
    daemon = WebHandler.daemon_ref
    if not daemon:
        return data
    with daemon._lock:
        roots = list(daemon._observers.keys())
    for i, root in enumerate(roots):
        if _same_project_root(directory, root):
            data = dict(data)
            data["project"] = i
            break
    return data


def notify_event(event_type: str, data: dict = None):
    """Public function for daemon to broadcast events to all web clients."""
    payload = dict(data or {})
    if event_type == "build_complete":
        payload = _enrich_build_complete_event(payload)
    broadcaster.broadcast(event_type, payload)


def start_web_server(daemon_ref, port: int) -> HTTPServer:
    """Start the HTTP server in a thread."""
    class ReuseThreadingHTTPServer(ThreadingHTTPServer):
        allow_reuse_address = True
        daemon_threads = True

        def handle_error(self, request, client_address):
            """Log disconnects quietly, real failures with full traceback."""
            exc = sys.exc_info()[1]
            if isinstance(exc, _EXPECTED_CONN_ERRORS):
                logger.debug("Web client %s 断开: %r", client_address, exc)
                return
            logger.exception("处理来自 %s 的 Web 请求时出错", client_address)

    def _serve_with_guard(server) -> None:
        try:
            server.serve_forever()
        except Exception:
            # serve_forever should never raise; if it does, the whole Web UI
            # dies silently without this trace in the log.
            logger.exception("Web server 线程异常退出")
            raise

    WebHandler.daemon_ref = daemon_ref
    server = ReuseThreadingHTTPServer(("127.0.0.1", port), WebHandler)
    thread = threading.Thread(target=_serve_with_guard, args=(server,), daemon=True)
    thread.start()
    return server


def _render_panel(daemon_ref) -> str:
    """Render the control panel HTML."""
    with daemon_ref._lock:
        dirs = list(daemon_ref._observers.keys())
    default_project = daemon_ref.config.get("default_project")

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
        ref_lbl = detail.get("label_count", "?")
        dtags = detail.get("dtag_count")
        if dtags is None:
            dtags = "?"
        index_count = detail.get("index_count", "?")
        last = detail.get("last_build", "?")
        err = detail.get("error")
        if err:
            status_text = f'<span class="error">{err}</span>'
        else:
            status_text = (
                f"{files} 文件, {ref_lbl} 交叉引用, {dtags} 文档标记, "
                f"{index_count} 索引, {last[:19]}"
            )
        is_default = d == default_project
        default_badge = ' <span class="default-badge">默认</span>' if is_default else ''

        d_attr = html_escape(d, quote=True)
        d_js = json.dumps(d)
        projects_html += f"""\
<tr data-dir="{d_attr}">
  <td><a href="/p/{i}/">{name}</a>{default_badge}</td>
  <td class="id">[{i}]</td>
  <td class="path">{html_escape(d)}</td>
  <td>{status_text}</td>
  <td>
    <button onclick='rebuildProject({i}, {d_js})'>重建</button>
    <button onclick='exportZip({i})'>导出ZIP</button>
    <button onclick='setDefaultProject({i}, {d_js})'>默认</button>
    <button class="danger" onclick='removeProject({i}, {d_js})'>移除</button>
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
.table-wrap {{ overflow-x: auto; -webkit-overflow-scrolling: touch; max-width: 100%; }}
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
th, td {{ text-align: left; padding: 10px 8px; border-bottom: 1px solid #eee; vertical-align: top; }}
th {{ color: #666; font-weight: 600; font-size: 13px; }}
td.id {{ white-space: nowrap; font-family: monospace; font-size: 12px; color: #888; width: 3em; }}
td.path {{ font-family: monospace; font-size: 12px; color: #888; overflow-wrap: anywhere; word-break: break-word; max-width: 28em; }}
td.actions {{ white-space: nowrap; }}
td.actions button {{ margin: 2px 4px 2px 0; }}
td a {{ color: #0066cc; text-decoration: none; font-weight: 500; }}
td a:hover {{ text-decoration: underline; }}
.empty {{ text-align: center; color: #999; padding: 40px 0; }}
.error {{ color: #cc3300; }}
.default-badge {{
    display: inline-block; font-size: 11px; padding: 1px 6px; margin-left: 4px;
    background: #e3f2fd; color: #1565c0; border-radius: 8px;
    font-weight: 500;
}}
.default-badge {{ display: inline-block; font-size: 11px; padding: 1px 6px; background: #e3f2fd; color: #1565c0;
    border-radius: 3px; margin-left: 4px; font-weight: 600; }}
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
<div class="table-wrap">
<table>
<thead><tr><th>名称</th><th>#</th><th>路径</th><th>状态</th><th>操作</th></tr></thead>
<tbody id="projects">
{projects_html if projects_html else '<tr><td colspan="5" class="empty">暂无监控目录</td></tr>'}
</tbody>
</table>
</div>
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

function escHtml(s) {{
    if (!s) return '';
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}

function escAttr(s) {{
    return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;');
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

function setDefaultProject(idx, dir) {{
    fetch('/api/default', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{project: idx}})
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

function exportZip(idx) {{
    showToast('正在导出…');
    fetch('/api/export_zip', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{project: idx}})
    }}).then(function(r) {{
        var ct = (r.headers.get('Content-Type') || '').toLowerCase();
        if (ct.indexOf('application/json') >= 0) {{
            return r.json().then(function(d) {{
                throw new Error((d && d.error) ? d.error : '导出失败');
            }});
        }}
        if (!r.ok) throw new Error('HTTP ' + r.status);
        var cd = r.headers.get('Content-Disposition') || '';
        var m = cd.match(/filename="([^"]+)"/i);
        var name = (m && m[1]) ? m[1] : 'export.html.zip';
        return r.blob().then(function(blob) {{ return {{blob: blob, name: name}}; }});
    }}).then(function(o) {{
        var url = URL.createObjectURL(o.blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = o.name;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        showToast('已开始下载');
    }}).catch(function(e) {{
        showToast('错误: ' + (e.message || e), 4000);
    }});
}}

function refreshProjects(data) {{
    if (!data || !data.directories) return;
    var tbody = document.getElementById('projects');
    var html = '';
    var dirs = data.directories;
    var details = data.details || {{}};
    var defaultProject = data.default_project || null;
    if (dirs.length === 0) {{
        tbody.innerHTML = '<tr><td colspan="5" class="empty">暂无监控目录</td></tr>';
        return;
    }}
    function escAttr(s) {{
        return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');
    }}
    function escOnclick(s) {{
        return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;');
    }}
    for (var i = 0; i < dirs.length; i++) {{
        var d = dirs[i];
        var name = d.split(/[/\\\\]/).pop();
        var detail = details[d] || {{}};
        var err = detail.error;
        var isDefault = (d === defaultProject);
        var statusText;
        if (err) {{
            statusText = '<span class="error">' + err + '</span>';
        }} else {{
            var files = detail.file_count || '?';
            var refLbl = detail.label_count || '?';
            var dtags = (detail.dtag_count !== undefined && detail.dtag_count !== null)
                ? detail.dtag_count : '?';
            var indexCount = detail.index_count || '?';
            var last = (detail.last_build || '?').substring(0, 19);
            statusText = files + ' 文件, ' + refLbl + ' 交叉引用, ' + dtags
                + ' 文档标记, ' + indexCount + ' 索引, ' + last;
        }}
        var defaultBadge = isDefault ? ' <span class="default-badge">默认</span>' : '';
        html += '<tr data-dir="' + escAttr(d) + '">';
        html += '<td><a href="/p/' + i + '/">' + escAttr(name) + '</a>' + defaultBadge + '</td>';
        html += '<td class="id">[' + i + ']</td>';
        html += '<td class="path">' + escAttr(d) + '</td>';
        html += '<td>' + statusText + '</td>';
        html += '<td class="actions">';
        var ocRebuild = 'rebuildProject(' + i + ', ' + JSON.stringify(d) + ')';
        var ocExport = 'exportZip(' + i + ')';
        var ocDefault = 'setDefaultProject(' + i + ', ' + JSON.stringify(d) + ')';
        var ocRemove = 'removeProject(' + i + ', ' + JSON.stringify(d) + ')';
        html += '<button onclick="' + escOnclick(ocRebuild) + '">重建</button> ';
        html += '<button onclick="' + escOnclick(ocExport) + '">导出ZIP</button> ';
        html += '<button onclick="' + escOnclick(ocDefault) + '">默认</button> ';
        html += '<button class="danger" onclick="' + escOnclick(ocRemove) + '">移除</button>';
        html += '</td></tr>';
    }}
    tbody.innerHTML = html;
}}

// SSE: listen for server-pushed events
{CLIENT_BOOTSTRAP_JS}
var evtSource = window.NM_EVENT_SOURCE;
if (!evtSource) {{
    evtSource = new EventSource('/api/events');
}}
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
evtSource.addEventListener('default_changed', function(e) {{
    var data = JSON.parse(e.data);
    refreshProjects(data);
}});
evtSource.addEventListener('cli_search', function(e) {{
    var data = JSON.parse(e.data);
    if (!data) return;
    if (data.open_url) {{
        window.location.href = data.open_url;
        return;
    }}
    if (!data.query) return;
    var project = (data.project !== undefined && data.project !== null) ? data.project : 0;
    window.location.href = '/p/' + project + '/?q=' + encodeURIComponent(data.query);
}});
evtSource.addEventListener('cli_view', function(e) {{
    var data = JSON.parse(e.data);
    if (!data || !data.url) return;
    window.location.href = data.url;
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
