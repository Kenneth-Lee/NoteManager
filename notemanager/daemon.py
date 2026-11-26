"""NoteManager daemon process.

Runs as a background process, listens on TCP for CLI commands
and on HTTP for web UI, manages watchers for all registered directories.
"""

import json
import logging
import os
import re
import secrets
import socket
import socketserver
import subprocess
import sys
import threading
import datetime
import hashlib
import time
import shutil
import mimetypes
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from notemanager import __version__ as NOTEMANAGER_VERSION
from notemanager.protocol import (
    DEFAULT_HOST, DEFAULT_PORT, DEFAULT_UNIX_SOCKET, recv_command, send_response,
    send_command,
    use_unix_socket,
)
from notemanager.builder import full_build
from notemanager.scan_manifest import (
    MANIFEST_VERSION,
    live_payload_and_files,
    read_scan_manifest,
    runtime_fingerprint,
)
from notemanager.watcher import start_watcher
from notemanager.watcher import suppress_path
from notemanager.web import broadcaster, notify_event
from notemanager.indexer import filter_search_documents
from notemanager import editor_launcher
from notemanager import config_store
from notemanager.renderer.rst_renderer import render_rst
from notemanager.renderer.md_renderer import render_md
from notemanager.renderer.txt_renderer import render_txt
from notemanager.renderer.template import wrap_html
from notemanager.scanner import INCLUDE_EXTENSIONS, SKIP_DIRS

logger = logging.getLogger(__name__)

DEFAULT_WEB_PORT = 19833

_ANNOTATION_BAD_RE = re.compile(r"\bon[a-z]+\s*=", re.IGNORECASE)


def _annotation_svg_error(text: str) -> str | None:
    """Return an error string for unsafe/invalid annotation SVG."""
    if len(text.encode("utf-8")) > 2 * 1024 * 1024:
        return "标注文件过大"
    if not text.strip().startswith("<svg") or "</svg>" not in text:
        return "不是有效的 SVG"
    lowered = text.lower()
    for bad in ("<script", "<foreignobject", "<iframe", "<object", "<embed",
                "javascript:"):
        if bad in lowered:
            return "SVG 包含不允许的内容"
    if _ANNOTATION_BAD_RE.search(text):
        return "SVG 包含不允许的事件属性"
    return None

# A main page that disconnects (navigation/reload) keeps its role for a short
# grace period so a normal page transition does not hand the role to another tab.
WEB_MAIN_GRACE_SECONDS = 3.0
_WEB_CLIENT_ID_RE = re.compile(r"^nm-[A-Za-z0-9_-]{4,40}$")


def _temp_renderer_fingerprint() -> str:
    """Fingerprint renderer sources to invalidate stale temp HTML cache."""
    base = Path(__file__).resolve().parent
    files = [
        base / "renderer" / "md_renderer.py",
        base / "renderer" / "rst_renderer.py",
        base / "renderer" / "txt_renderer.py",
        base / "renderer" / "template.py",
        base / "renderer" / "export_template.py",
        # Temp pages load notemanager.js generated from builder._DEFAULT_JS.
        base / "builder.py",
        base / "web_client_js.py",
        base / "renderer" / "assets" / "nm-external-editor.js",
    ]
    sig: list[str] = []
    for f in files:
        try:
            st = f.stat()
            sig.append(f"{f.name}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            sig.append(f"{f.name}:missing")
    raw = "|".join(sig).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def _temp_renderer_signature() -> str:
    """Compatibility wrapper for temp view render signature."""
    return _temp_renderer_fingerprint()

_PASTE_IMAGE_MAX_BYTES = 8 * 1024 * 1024
_IMPORT_ASSET_MAX_BYTES = 32 * 1024 * 1024
_IMPORT_BLOCKED_EXTS = frozenset({
    ".html", ".htm", ".xhtml", ".js", ".mjs", ".svg",
})
_IMPORT_IMAGE_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico",
})
_PASTE_IMAGE_TYPES: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _first_search_hit_open_url(project_idx: int, project_dir: Path, query: str) -> str | None:
    """仅当搜索恰好命中 1 条时返回该条目的 URL；多条命中返回 None，由前端展示列表。"""
    index_file = project_dir / ".NoteManager" / "html" / "_index" / "search_index.json"
    if not index_file.is_file():
        return None
    try:
        data = json.loads(index_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    docs = data.get("documents") or []
    if not docs:
        return None
    results = filter_search_documents(docs, query, limit=200)
    if len(results) != 1:
        return None
    rel = (results[0].get("path") or "").strip()
    if not rel:
        return None
    base, _, frag = rel.partition("#")
    base = base.strip().strip("/")
    if not base:
        return None
    path_enc = "/".join(quote(seg, safe="") for seg in Path(base).parts)
    url = f"/p/{project_idx}/{path_enc}"
    if frag:
        url += "#" + quote(frag, safe="")
    return url

# 父进程等子进程写完索引再返回；首次 full_build 可能远超 1 分钟。
_DAEMON_PARENT_STARTUP_POLL_S = 0.2
_DAEMON_PARENT_STARTUP_MAX_STEPS = 6000  # ×0.2s ≈ 20 分钟
_DAEMON_STARTUP_FAIL_GRACE_STEPS = 50  # ×0.2s ≈ 10s

# CLI `_ensure_daemon` 轮询（与父进程等待同量级，避免 daemon 已在跑但构建未结束）
DAEMON_ENSURE_WAIT_STEPS = 2400  # ×0.5s ≈ 20 分钟

# Global config directory
HOME_NM_DIR = Path.home() / ".NoteManager"
CONFIG_FILE = HOME_NM_DIR / "config.json"
CONFIG_BACKUP_FILE = HOME_NM_DIR / "config.json.bak"
DAEMON_FILE = HOME_NM_DIR / "daemon.json"


def _default_config() -> dict:
    return {
        "directories": [],
        "port": DEFAULT_PORT,
        "web_port": DEFAULT_WEB_PORT,
    }


def _load_config() -> dict:
    """Load daemon config from ~/.NoteManager/config.json."""
    if not CONFIG_FILE.exists():
        return _default_config()
    data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise config_store.ConfigValidationError("配置文件顶层必须是 JSON 对象")
    return data


def _read_config_for_write() -> dict:
    text, _ = config_store.read_text(CONFIG_FILE)
    if text is None:
        return _default_config()
    return config_store.parse_config_text(text)


def _config_file_mode() -> int:
    try:
        return CONFIG_FILE.stat().st_mode & 0o777
    except OSError:
        return 0o600


def _write_config_data(config: dict) -> None:
    """Write a full config dict; caller holds CONFIG_WRITE_LOCK."""
    config_store.write_text_atomic(
        CONFIG_FILE, config_store.dump_config(config), mode=_config_file_mode(),
    )


def _backup_config() -> Path | None:
    return config_store.backup_file(CONFIG_FILE, CONFIG_BACKUP_FILE)


def _mutate_config_file(mutator) -> dict | None:
    """Read the current file, apply *mutator*, and write it back atomically.

    Runtime operations (add/remove/default) use this instead of writing their
    possibly stale in-memory config, so a config-editor save is not clobbered.
    """
    with config_store.CONFIG_WRITE_LOCK:
        try:
            current = _read_config_for_write()
        except (OSError, config_store.ConfigValidationError) as e:
            logger.error("读取配置文件失败，未写入: %s", e)
            return None
        updated = mutator(current)
        if updated is None:
            updated = current
        try:
            _backup_config()
            _write_config_data(updated)
        except OSError as e:
            logger.error("写入配置文件失败: %s", e)
            return None
        return updated


def _cfg_add_directory(cfg: dict, directory: str) -> dict:
    dirs = cfg.get("directories")
    if not isinstance(dirs, list):
        dirs = []
    if directory not in dirs:
        dirs.append(directory)
    cfg["directories"] = dirs
    return cfg


def _cfg_remove_directory(cfg: dict, directory: str) -> dict:
    dirs = cfg.get("directories")
    if isinstance(dirs, list):
        cfg["directories"] = [d for d in dirs if d != directory]
    return cfg


def _cfg_set_default(cfg: dict, default_project: str) -> dict:
    cfg["default_project"] = default_project
    return cfg


def _write_daemon_info(pid: int, port: int, web_port: int,
                       transport: str, socket_path: str = "") -> None:
    """Write daemon runtime info (PID is valid while process is up)."""
    HOME_NM_DIR.mkdir(parents=True, exist_ok=True)
    DAEMON_FILE.write_text(
        json.dumps({
            "pid": pid,
            "port": port,
            "web_port": web_port,
            "transport": transport,
            "socket_path": socket_path,
            "started": datetime.datetime.now().isoformat(),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _remove_daemon_info() -> None:
    """Remove daemon runtime info and the actual Unix socket (if any)."""
    info: dict = {}
    if DAEMON_FILE.exists():
        try:
            info = json.loads(DAEMON_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            info = {}
        try:
            DAEMON_FILE.unlink()
        except OSError:
            pass
    transport = info.get("transport")
    if transport == "unix" or (transport is None and use_unix_socket()):
        raw = info.get("socket_path") or str(DEFAULT_UNIX_SOCKET)
        try:
            Path(raw).unlink(missing_ok=True)
        except OSError:
            pass


def is_daemon_running() -> int | None:
    """Check if daemon is running. Returns PID or None."""
    if not DAEMON_FILE.exists():
        return None
    try:
        info = json.loads(DAEMON_FILE.read_text(encoding="utf-8"))
        pid = info.get("pid")
        if pid and _pid_exists(pid):
            return pid
        _remove_daemon_info()
        return None
    except (json.JSONDecodeError, KeyError):
        return None


def _daemon_responded_startup_complete() -> bool:
    """True if we can reach the daemon and status says startup is finished."""
    if not is_daemon_running() or not DAEMON_FILE.exists():
        return False
    try:
        info = json.loads(DAEMON_FILE.read_text(encoding="utf-8"))
        sock = info.get("socket_path", str(DEFAULT_UNIX_SOCKET))
        port = info.get("port", DEFAULT_PORT)
        resp = send_command(
            {"cmd": "status"},
            port=port,
            timeout=2,
            socket_path=sock,
        )
        if not resp.get("ok"):
            return False
        return resp["data"].get("startup_complete", True)
    except (ConnectionError, OSError, TimeoutError, json.JSONDecodeError, KeyError):
        return False


def get_daemon_web_port() -> int | None:
    """Get the web UI port of the running daemon."""
    if not DAEMON_FILE.exists():
        return None
    try:
        info = json.loads(DAEMON_FILE.read_text(encoding="utf-8"))
        return info.get("web_port")
    except (json.JSONDecodeError, KeyError):
        return None


def _pid_exists(pid: int) -> bool:
    """Check if a process with given PID exists.

    On Windows, ``os.kill(pid, 0)`` is not a harmless existence probe: Python
    documents that any signal other than CTRL_C_EVENT/CTRL_BREAK_EVENT causes
    unconditional process termination. The parent startup check used it and
    killed the freshly-started daemon right after it wrote daemon.json.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                # ERROR_ACCESS_DENIED means the process exists but is protected.
                return ctypes.get_last_error() == 5
            try:
                code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _need_rebuild_on_startup(directory: str | Path) -> bool:
    """Whether a project needs full rebuild before watcher restore."""
    root = Path(directory).resolve()
    nm_dir = root / ".NoteManager"
    status_file = nm_dir / "status.json"
    search_index_file = nm_dir / "html" / "_index" / "search_index.json"
    if not status_file.is_file() or not search_index_file.is_file():
        return True

    try:
        status = json.loads(status_file.read_text(encoding="utf-8"))
        last_build_raw = status.get("last_build")
        last_build = datetime.datetime.fromisoformat(last_build_raw)
        built_file_count = int(status.get("file_count", -1))
    except (json.JSONDecodeError, ValueError, TypeError):
        return True

    saved = read_scan_manifest(nm_dir)
    if saved is not None and int(saved.get("version", -1)) == MANIFEST_VERSION:
        if saved.get("runtime_fingerprint") != runtime_fingerprint():
            return True

    # A directory mtime only changes when entries are added/removed.  Editing
    # an existing note, or replacing a renderer asset, must also invalidate the
    # cold-start shortcut.  The scan is cheap relative to a full build.
    try:
        live, _files = live_payload_and_files(root)
    except Exception:
        return True

    if (
        saved is not None
        and int(saved.get("version", -1)) == MANIFEST_VERSION
        and live["tree_rollup"] == saved.get("tree_rollup")
        and live["file_count"] == int(saved.get("file_count", -1))
        and live["file_count"] == built_file_count
    ):
        return False

    if saved is not None and int(saved.get("version", -1)) == MANIFEST_VERSION:
        return True

    if live["file_count"] != built_file_count:
        return True
    if not _files:
        return False

    return any(datetime.datetime.fromtimestamp(f.mtime) > last_build for f in _files)


class NoteManagerDaemon:
    """The main daemon that manages all project watchers."""

    def __init__(self, port: int = DEFAULT_PORT, web_port: int = DEFAULT_WEB_PORT):
        self.port = port
        self.web_port = web_port
        try:
            self.config = _load_config()  # startup snapshot of the file
        except (OSError, ValueError, json.JSONDecodeError) as e:
            logger.error("读取配置文件失败，使用默认配置: %s", e)
            self.config = _default_config()
        if not isinstance(self.config.get("directories"), list):
            self.config["directories"] = []

        transport = str(self.config.get("transport") or "").strip().lower()
        if transport not in ("unix", "tcp"):
            transport = "unix" if use_unix_socket() else "tcp"
        if os.name == "nt":
            transport = "tcp"
        self.transport = transport
        if transport == "unix":
            raw_socket = str(self.config.get("socket_path") or "").strip()
            if raw_socket and not Path(raw_socket).is_absolute():
                logger.warning("socket_path 不是绝对路径，回退默认值: %s", raw_socket)
                raw_socket = ""
            self.socket_path = raw_socket or str(DEFAULT_UNIX_SOCKET)
        else:
            self.socket_path = ""

        self._observers = {}  # directory -> Observer
        self._lock = threading.Lock()
        self._shutdown = threading.Event()
        self._startup_complete_event = threading.Event()
        self._tcp_server = None
        self._web_server = None
        self._rebuild_job_lock = threading.Lock()
        self._rebuild_jobs: dict[int, dict] = {}
        self._rebuild_next_id = 1
        self._rebuild_active_job_id: int | None = None
        self._grep_job_lock = threading.Lock()
        self._grep_jobs: dict[int, dict] = {}
        self._grep_next_id = 1
        self._add_job_lock = threading.Lock()
        self._add_jobs: dict[int, dict] = {}
        self._add_next_id = 1
        self._add_active_job_id: int | None = None
        self._web_lock = threading.RLock()
        self._web_state_path = HOME_NM_DIR / "web_state.json"
        self._main_id: str | None = None
        self._main_disconnected_since: float | None = None
        self._load_web_state()

    # ---- Web page (SSE client) registry ---------------------------------
    @staticmethod
    def _valid_web_client_id(client_id: str) -> bool:
        return bool(_WEB_CLIENT_ID_RE.fullmatch(str(client_id or "").strip()))

    def _load_web_state(self) -> None:
        main_id = None
        try:
            raw = self._web_state_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            candidate = str(data.get("main_client_id") or "").strip()
            if self._valid_web_client_id(candidate):
                main_id = candidate
        except (OSError, ValueError, json.JSONDecodeError):
            main_id = None
        self._main_id = main_id
        self._main_disconnected_since = time.time() if main_id else None

    def _save_web_state_locked(self) -> None:
        try:
            self._web_state_path.parent.mkdir(parents=True, exist_ok=True)
            self._web_state_path.write_text(
                json.dumps({"main_client_id": self._main_id}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("保存 Web 页面状态失败: %s", e)

    def _new_web_client_id(self) -> str:
        for _ in range(30):
            client_id = "nm-" + secrets.token_hex(4)
            if client_id != self._main_id and broadcaster.get_client(client_id) is None:
                return client_id
        return "nm-" + secrets.token_hex(8)

    @staticmethod
    def _normalize_web_path(path: str) -> str:
        p = str(path or "").strip()
        if not p.startswith("/"):
            p = "/" + p if p else "/"
        p = p.split("?", 1)[0].split("#", 1)[0]
        return p or "/"

    @staticmethod
    def _project_from_path(path: str) -> int | None:
        m = re.match(r"^/p/(\d+)(?:/|$)", path or "")
        return int(m.group(1)) if m else None

    def _web_client_payloads_locked(self) -> list[dict]:
        clients = broadcaster.list_clients()
        out = []
        for client in clients:
            out.append({
                "id": client.id,
                "path": client.path,
                "project": client.project,
                "connected_at": round(client.connected_at, 3),
                "last_active": round(client.last_active, 3),
                "is_main": client.id == self._main_id,
            })
        out.sort(key=lambda c: (not c["is_main"], -c["last_active"], c["id"]))
        return out

    def _find_web_client_locked(self, raw: str) -> str | None:
        wanted = str(raw or "").strip()
        if not wanted:
            return None
        if broadcaster.get_client(wanted) is not None:
            return wanted
        matches = [c.id for c in broadcaster.list_clients() if c.id.startswith(wanted)]
        return matches[0] if len(matches) == 1 else None

    def _maybe_elect_main_locked(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if self._main_id and broadcaster.get_client(self._main_id) is not None:
            self._main_disconnected_since = None
            return

        if self._main_id is not None:
            if self._main_disconnected_since is None:
                self._main_disconnected_since = now
            if now - self._main_disconnected_since < WEB_MAIN_GRACE_SECONDS:
                return

        clients = broadcaster.list_clients()
        if not clients:
            if self._main_id is not None:
                self._main_id = None
                self._main_disconnected_since = None
                self._save_web_state_locked()
            return

        # Most recently active secondary takes over; fall back to connect time.
        best = max(clients, key=lambda c: (c.last_active, c.connected_at, c.id))
        if best.id != self._main_id:
            self._main_id = best.id
            self._main_disconnected_since = None
            self._save_web_state_locked()

    def register_web_client(self, wfile, client_id: str, path: str) -> dict:
        """Register an SSE page and return its routing info for the init event."""
        now = time.time()
        cid = str(client_id or "").strip()
        if not self._valid_web_client_id(cid):
            cid = self._new_web_client_id()
        norm_path = self._normalize_web_path(path)
        project = self._project_from_path(norm_path)

        with self._web_lock:
            existing = broadcaster.get_client(cid)
            replaced = existing is not None and existing.wfile is not wfile
            broadcaster.add_client(cid, wfile, norm_path, project)

            if self._main_id is None:
                self._main_id = cid
                self._main_disconnected_since = None
                self._save_web_state_locked()
            elif cid == self._main_id:
                self._main_disconnected_since = None
            else:
                self._maybe_elect_main_locked(now)

            clients = self._web_client_payloads_locked()
            return {
                "client_id": cid,
                "is_main": self._main_id == cid,
                "main_client": self._main_id,
                "main_connected": bool(
                    self._main_id and broadcaster.get_client(self._main_id) is not None
                ),
                "clients": clients,
                "replaced_existing": replaced,
            }

    def unregister_web_client(self, wfile) -> None:
        """Remove a page; keep main role through the grace period on navigation."""
        with self._web_lock:
            broadcaster.remove_client(wfile)
            # The broadcaster may already have pruned this wfile after a write
            # failure, so don't rely on the return value here.
            if self._main_id and broadcaster.get_client(self._main_id) is None:
                if self._main_disconnected_since is None:
                    self._main_disconnected_since = time.time()

    def touch_web_client(self, client_id: str) -> dict:
        cid = str(client_id or "").strip()
        if not self._valid_web_client_id(cid):
            return {"ok": False, "error": "无效的页面 id"}
        known = broadcaster.touch(cid)
        return {"ok": True, "known": known}

    def handle_main(self, cmd: dict) -> dict:
        """Show or change the main page (default target of view/search)."""
        raw = str(cmd.get("client", "") or "").strip()
        now = time.time()
        with self._web_lock:
            self._maybe_elect_main_locked(now)
            clients = self._web_client_payloads_locked()
            if not raw:
                main = self._main_id
                message = f"当前主页面: {main}" if main else "当前没有主页面"
                return {
                    "ok": True,
                    "message": message,
                    "main_client": main,
                    "main_connected": bool(
                        main and broadcaster.get_client(main) is not None
                    ),
                    "clients": clients,
                }
            cid = self._find_web_client_locked(raw)
            if not cid:
                return {
                    "ok": False,
                    "error": f"找不到已连接的页面: {raw}",
                    "clients": clients,
                }
            self._main_id = cid
            self._main_disconnected_since = None
            self._save_web_state_locked()
            return {
                "ok": True,
                "message": f"主页面已设为 {cid}",
                "main_client": cid,
                "main_connected": True,
                "clients": self._web_client_payloads_locked(),
            }

    @staticmethod
    def _append_query_param(url: str, key: str, value: str) -> str:
        base, sep, fragment = str(url or "").partition("#")
        joiner = "&" if "?" in base else "?"
        out = f"{base}{joiner}{key}={quote(str(value), safe='')}"
        if sep:
            out += "#" + fragment
        return out

    def open_editor(self, payload: dict) -> dict:
        """Open the source associated with a Web page in the configured editor.

        Managed pages identify their source with (project, source_rel_path);
        temp pages use the temp key so the server resolves the path from its
        own metadata instead of trusting an arbitrary client path.
        """
        if not isinstance(payload, dict):
            return {"ok": False, "error": "无效的请求"}
        kind = str(payload.get("kind") or "").strip()

        abs_path: Path | None = None
        if kind == "temp" or (not kind and payload.get("temp_key")):
            key = str(payload.get("temp_key") or "").strip()
            if not re.fullmatch(r"[0-9a-f]{16}", key):
                return {"ok": False, "error": "无效的临时页面 id"}
            meta_path = self.get_temp_view_dir() / f"{key}.meta.json"
            if not meta_path.is_file():
                return {"ok": False, "error": "临时页面不存在或已过期"}
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                return {"ok": False, "error": "无法读取临时页面信息"}
            raw_source = str(meta.get("source_path") or "").strip()
            if not raw_source:
                return {"ok": False, "error": "临时页面没有源文件路径"}
            candidate = Path(raw_source).expanduser().resolve()
            if candidate.suffix.lower() not in (".md", ".rst", ".txt"):
                return {"ok": False, "error": "仅支持 .md/.rst/.txt 文件"}
            if not candidate.is_file():
                return {"ok": False, "error": f"文件不存在: {candidate}"}
            abs_path = candidate
        else:
            try:
                project_idx = int(payload.get("project", -1))
            except (TypeError, ValueError):
                return {"ok": False, "error": "无效工程编号"}
            with self._lock:
                dirs = list(self._observers.keys())
            if not (0 <= project_idx < len(dirs)):
                return {"ok": False, "error": "无效工程编号"}
            root, rel = self._validated_source_rel(
                Path(dirs[project_idx]), str(payload.get("source_rel_path") or ""),
            )
            if rel is None:
                return {"ok": False, "error": "非法源文件路径"}
            candidate = (root / rel).resolve()
            if not candidate.is_file():
                return {"ok": False, "error": f"文件不存在: {candidate}"}
            abs_path = candidate

        # Use the daemon-startup snapshot: config file edits only apply after a
        # restart, consistently with the rest of the config model.
        editor_config = self.config.get("editor")

        result = editor_launcher.open_in_editor(editor_config, abs_path)
        result["source_path"] = str(abs_path)
        if result.get("ok"):
            logger.info("外部编辑器打开: %s", abs_path)
        else:
            logger.warning("外部编辑器打开失败: %s: %s", abs_path, result.get("error"))
        return result

    @staticmethod
    def _tokenize_search_query(query: str) -> list[str]:
        toks: list[str] = []
        cur = []
        in_quote = False
        esc = False
        for ch in str(query or ""):
            if esc:
                cur.append(ch)
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_quote = not in_quote
                continue
            if ch.isspace() and not in_quote:
                if cur:
                    toks.append("".join(cur).strip())
                    cur = []
                continue
            cur.append(ch)
        if cur:
            toks.append("".join(cur).strip())
        return [t for t in toks if t]

    @staticmethod
    def _parse_search_terms(query: str) -> list[dict]:
        out: list[dict] = []
        for tok in NoteManagerDaemon._tokenize_search_query(query):
            m = re.match(r"^(chapter|label|index|dtag|fname|grep):(.*)$", tok, re.IGNORECASE)
            if m:
                typ = m.group(1).lower()
                q = (m.group(2) or "").strip()
            else:
                typ = None
                q = tok.strip()
            if q:
                out.append({"type": typ, "query": q})
        return out

    @staticmethod
    def _build_regex(pattern: str):
        try:
            return re.compile(pattern, re.IGNORECASE)
        except re.error:
            return re.compile(re.escape(pattern), re.IGNORECASE)

    def _grep_worker(self, job_id: int) -> None:
        with self._grep_job_lock:
            job = self._grep_jobs.get(job_id)
        if not job:
            return
        project_root = Path(job["project_root"])
        html_dir = project_root / ".NoteManager" / "html"
        rgx = self._build_regex(job["pattern"])
        filters = job.get("filters") or []
        allowed_html: set[str] | None = None
        try:
            if filters:
                idx_file = html_dir / "_index" / "search_index.json"
                data = json.loads(idx_file.read_text(encoding="utf-8")) if idx_file.is_file() else {}
                docs = data.get("documents") or []
                matched_paths: set[str] | None = None
                for f in filters:
                    q = (f.get("query") or "").strip()
                    if not q:
                        continue
                    ft = f.get("type")
                    r = self._build_regex(q)
                    cur = set()
                    for d in docs:
                        if ft and d.get("type") != ft:
                            continue
                        blob = " ".join([str(d.get("title", "")), str(d.get("text", "")), str(d.get("snippet", ""))])
                        if r.search(blob):
                            p = str(d.get("path", "")).split("#", 1)[0]
                            if p:
                                cur.add(p)
                    if matched_paths is None:
                        matched_paths = cur
                    else:
                        matched_paths &= cur
                allowed_html = matched_paths if matched_paths is not None else set()
            files: list[Path] = []
            for p in project_root.rglob("*"):
                if not p.is_file():
                    continue
                try:
                    rel = p.relative_to(project_root)
                except ValueError:
                    continue
                if any(part in SKIP_DIRS for part in rel.parts):
                    continue
                if p.suffix.lower() not in INCLUDE_EXTENSIONS:
                    continue
                html_rel = str(rel.with_suffix(".html").as_posix())
                if allowed_html is not None and html_rel not in allowed_html:
                    continue
                files.append(p)
            with job["lock"]:
                job["total"] = len(files)
            for i, fp in enumerate(files, start=1):
                with job["lock"]:
                    if job.get("cancelled"):
                        job["done"] = True
                        return
                try:
                    text = fp.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    with job["lock"]:
                        job["scanned"] = i
                    continue
                m = rgx.search(text)
                if m:
                    line = text.count("\n", 0, m.start()) + 1
                    s = max(0, m.start() - 60)
                    e = min(len(text), m.end() + 100)
                    sn = re.sub(r"\s+", " ", text[s:e]).strip()
                    rel = fp.relative_to(project_root)
                    html_path = rel.with_suffix(".html").as_posix()
                    entry = {
                        "type": "grep",
                        "title": rel.stem,
                        "path": html_path,
                        "text": rel.as_posix(),
                        "snippet": sn,
                        "source_path": rel.as_posix(),
                        "line_start": line,
                    }
                    with job["lock"]:
                        if len(job["results"]) < 500:
                            job["results"].append(entry)
                with job["lock"]:
                    job["scanned"] = i
            with job["lock"]:
                job["done"] = True
        except Exception as e:
            with job["lock"]:
                job["error"] = str(e)
                job["done"] = True

    def start_grep_search(self, project_idx: int, query: str, filters: list[dict] | None = None) -> dict:
        with self._lock:
            dirs = list(self._observers.keys())
        if not (0 <= project_idx < len(dirs)):
            return {"ok": False, "error": "无效工程编号"}
        pattern = (query or "").strip()
        if not pattern:
            return {"ok": False, "error": "缺少 grep 查询"}
        with self._grep_job_lock:
            for jid in [k for k, v in self._grep_jobs.items() if v.get("done")]:
                self._grep_jobs.pop(jid, None)
            job_id = self._grep_next_id
            self._grep_next_id += 1
            self._grep_jobs[job_id] = {
                "project_root": str(Path(dirs[project_idx]).resolve()),
                "pattern": pattern,
                "filters": filters or [],
                "results": [],
                "scanned": 0,
                "total": 0,
                "done": False,
                "cancelled": False,
                "error": None,
                "lock": threading.Lock(),
            }
        t = threading.Thread(target=self._grep_worker, args=(job_id,), daemon=True)
        t.start()
        return {"ok": True, "job_id": job_id}

    def get_grep_status(self, job_id: int, since: int = 0) -> dict:
        with self._grep_job_lock:
            job = self._grep_jobs.get(job_id)
        if not job:
            return {"ok": False, "error": "无效或已结束的 grep 任务"}
        with job["lock"]:
            rs = job["results"]
            new = rs[since:] if since < len(rs) else []
            return {
                "ok": True,
                "results": new,
                "total_results": len(rs),
                "scanned": job["scanned"],
                "total": job["total"],
                "done": bool(job["done"]),
                "error": job["error"],
            }

    def cancel_grep_search(self, job_id: int) -> dict:
        with self._grep_job_lock:
            job = self._grep_jobs.get(job_id)
        if not job:
            return {"ok": True}
        with job["lock"]:
            job["cancelled"] = True
            job["done"] = True
        return {"ok": True}

    def _resolve_rebuild_targets(self, cmd: dict, dirs: list[str]) -> list[str]:
        """解析要重建的监控目录列表（全部 / 按 --project / 按 directory）。"""
        if not dirs:
            return []
        if cmd.get("directory"):
            p = str(Path(cmd["directory"]).resolve())
            if p not in dirs:
                raise ValueError(f"不在监控列表中: {p}")
            return [p]
        proj = cmd.get("project")
        if proj is not None and proj != "":
            idx = self._resolve_project_index(proj, dirs)
            if idx is None:
                raise ValueError(f"无效的工程标识: {proj}")
            return [dirs[idx]]
        return list(dirs)

    def _rebuild_worker(self, job_id: int, cmd: dict) -> None:
        job = self._rebuild_jobs.get(job_id)
        if not job:
            return

        def progress(msg: str) -> None:
            with job["lock"]:
                job["lines"].append(msg)

        try:
            with self._lock:
                dirs = list(self._observers.keys())
            targets = self._resolve_rebuild_targets(cmd, dirs)
            if not targets:
                raise ValueError("没有可重建的目录")
            progress(f"开始重建，共 {len(targets)} 个工程")
            for i, d in enumerate(targets):
                progress(f"[{i + 1}/{len(targets)}] {Path(d).name} — {d}")
                full_build(Path(d), progress=progress)
                progress("  ✓ 工程完成")
                self._notify_change(
                    "build_complete",
                    {"directory": str(Path(d).resolve())},
                )
            job["success"] = True
        except Exception as e:
            logger.exception("重建任务失败")
            job["success"] = False
            job["error"] = str(e)
            with job["lock"]:
                job["lines"].append(f"错误: {e}")
        finally:
            job["done"].set()
            with self._rebuild_job_lock:
                if self._rebuild_active_job_id == job_id:
                    self._rebuild_active_job_id = None

    def handle_rebuild(self, cmd: dict) -> dict:
        """启动异步全量重建；CLI 用 rebuild_progress 轮询输出。"""
        with self._lock:
            dirs = list(self._observers.keys())
        try:
            targets = self._resolve_rebuild_targets(cmd, dirs)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if not targets:
            return {"ok": False, "error": "没有监控中的目录"}

        with self._rebuild_job_lock:
            for stale_id in [
                jid for jid, j in self._rebuild_jobs.items() if j["done"].is_set()
            ]:
                self._rebuild_jobs.pop(stale_id, None)
            if self._rebuild_active_job_id is not None:
                cur = self._rebuild_jobs.get(self._rebuild_active_job_id)
                if cur is not None and not cur["done"].is_set():
                    return {"ok": False, "error": "已有重建任务进行中"}
            job_id = self._rebuild_next_id
            self._rebuild_next_id += 1
            job = {
                "lines": [],
                "lock": threading.Lock(),
                "done": threading.Event(),
                "success": None,
                "error": None,
            }
            self._rebuild_jobs[job_id] = job
            self._rebuild_active_job_id = job_id

        t = threading.Thread(
            target=self._rebuild_worker,
            args=(job_id, cmd),
            daemon=True,
        )
        t.start()
        return {"ok": True, "job_id": job_id}

    def handle_rebuild_progress(self, cmd: dict) -> dict:
        """拉取重建日志增量；全部消费且任务结束后移除 job。"""
        job_id = cmd.get("job_id")
        since = int(cmd.get("since", 0))
        if job_id is None:
            return {"ok": False, "error": "需要 job_id"}
        with self._rebuild_job_lock:
            job = self._rebuild_jobs.get(job_id)
        if not job:
            return {"ok": False, "error": "无效或已结束的 job_id"}
        with job["lock"]:
            lines = job["lines"]
            new_lines = lines[since:]
            total = len(lines)
            done = job["done"].is_set()
        resp = {
            "ok": True,
            "new_lines": new_lines,
            "total_lines": total,
            "done": done,
            "success": job["success"] if done else None,
            "error": job["error"] if done else None,
        }
        if done and since >= total:
            with self._rebuild_job_lock:
                self._rebuild_jobs.pop(job_id, None)
        return resp

    def _add_directory_impl(self, directory: str, progress=None) -> dict:
        """Shared add-directory implementation with optional progress callback."""
        if progress is None:
            progress = lambda _msg: None
        raw = str(directory or "").strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in '\"\'':
            raw = raw[1:-1].strip()
        path = Path(raw).resolve()
        if not raw:
            return {"ok": False, "error": "需要 directory"}
        if not path.is_dir():
            return {"ok": False, "error": f"目录不存在: {path}"}

        dir_str = str(path)

        with self._lock:
            if dir_str in self._observers:
                return {"ok": False, "error": f"已在监控中: {path}"}

        logger.info("构建目录: %s", path)
        progress(f"开始添加监控: {path}")
        progress("  构建目录（首次可能较慢）…")
        try:
            full_build(path, progress=progress)
        except Exception as e:
            logger.error("构建失败: %s: %s", path, e)
            return {"ok": False, "error": f"构建失败: {e}"}

        progress("  启动目录监控…")
        try:
            observer = start_watcher(path)
            observer.start()
            with self._lock:
                self._observers[dir_str] = observer
        except Exception as e:
            logger.error("启动监控失败: %s: %s", path, e)
            return {"ok": False, "error": f"启动监控失败: {e}"}

        progress("  更新配置…")
        with self._lock:
            if dir_str not in self.config["directories"]:
                self.config["directories"].append(dir_str)
        _mutate_config_file(lambda cfg: _cfg_add_directory(cfg, dir_str))

        logger.info("已添加监控: %s", path)
        self._notify_change("directories_changed")
        progress("  ✓ 添加完成")
        return {"ok": True, "message": f"已添加监控: {path}"}

    def add_directory(self, directory: str) -> dict:
        """Add a directory to monitoring."""
        return self._add_directory_impl(directory, progress=None)

    def get_log_tail(self, max_lines: int = 300) -> dict:
        """Return the tail of the daemon log for the control panel."""
        path = HOME_NM_DIR / "daemon.log"
        try:
            lines = max(1, min(int(max_lines or 300), 2000))
        except (TypeError, ValueError):
            lines = 300
        try:
            # Only read the tail for large logs; never load a multi-hundred-MB
            # log into the Web UI process.
            with path.open("rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 512 * 1024))
                data = f.read()
            text = data.decode("utf-8", errors="replace")
        except FileNotFoundError:
            return {"ok": True, "path": str(path), "text": ""}
        except OSError as e:
            return {"ok": False, "error": f"无法读取日志: {e}", "path": str(path)}
        tail = "\n".join(text.splitlines()[-lines:])
        return {"ok": True, "path": str(path), "text": tail}

    def pick_directory(self) -> dict:
        """Open a native folder picker on the machine running the daemon."""
        if os.name == "nt":
            script = (
                "$ErrorActionPreference = 'Stop'; "
                "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; "
                "$OutputEncoding = [Console]::OutputEncoding; "
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$d.Description = 'Select NoteManager directory'; "
                "$d.ShowNewFolderButton = $true; "
                "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
                "{ [Console]::Out.WriteLine($d.SelectedPath) }"
            )
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                proc = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=300, creationflags=flags,
                )
            except (OSError, subprocess.TimeoutExpired) as e:
                return {"ok": False, "error": f"无法打开目录选择器: {e}"}
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip()
                return {"ok": False, "error": detail or "目录选择器失败"}
            selected = (proc.stdout or "").strip().splitlines()
            if not selected:
                return {"ok": False, "error": "已取消"}
            return {"ok": True, "path": selected[-1].strip()}

        return {"ok": False, "error": "目录选择窗口目前仅支持 Windows，请手动输入路径。"}

    def _add_worker(self, job_id: int, directory: str) -> None:
        with self._add_job_lock:
            job = self._add_jobs.get(job_id)
        if not job:
            return

        def progress(msg: str) -> None:
            with job["lock"]:
                job["lines"].append(msg)

        try:
            result = self._add_directory_impl(directory, progress=progress)
            if result.get("ok"):
                job["success"] = True
                job["message"] = result.get("message", "添加完成")
            else:
                job["success"] = False
                job["error"] = result.get("error") or "添加失败"
                progress(f"错误: {job['error']}")
        except Exception as e:
            logger.exception("添加监控任务失败")
            job["success"] = False
            job["error"] = str(e)
            progress(f"错误: {e}")
        finally:
            job["done"].set()
            with self._add_job_lock:
                if self._add_active_job_id == job_id:
                    self._add_active_job_id = None

    def handle_add_with_progress(self, cmd: dict) -> dict:
        raw = str(cmd.get("directory", "")).strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in '\"\'':
            raw = raw[1:-1].strip()
        directory = str(Path(raw).resolve())
        if not raw:
            return {"ok": False, "error": "需要 directory"}

        with self._add_job_lock:
            for stale_id in [jid for jid, j in self._add_jobs.items() if j["done"].is_set()]:
                self._add_jobs.pop(stale_id, None)
            if self._add_active_job_id is not None:
                cur = self._add_jobs.get(self._add_active_job_id)
                if cur is not None and not cur["done"].is_set():
                    return {"ok": False, "error": "已有添加目录任务进行中"}
            job_id = self._add_next_id
            self._add_next_id += 1
            job = {
                "lines": [],
                "lock": threading.Lock(),
                "done": threading.Event(),
                "success": None,
                "error": None,
                "message": None,
            }
            self._add_jobs[job_id] = job
            self._add_active_job_id = job_id

        t = threading.Thread(target=self._add_worker, args=(job_id, directory), daemon=True)
        t.start()
        return {"ok": True, "job_id": job_id}

    def handle_add_progress(self, cmd: dict) -> dict:
        job_id = cmd.get("job_id")
        since = int(cmd.get("since", 0))
        if job_id is None:
            return {"ok": False, "error": "需要 job_id"}

        with self._add_job_lock:
            job = self._add_jobs.get(job_id)
        if not job:
            return {"ok": False, "error": "无效或已结束的 add 任务"}

        with job["lock"]:
            lines = job["lines"]
            new_lines = lines[since:]
            total = len(lines)
            done = job["done"].is_set()

        resp = {
            "ok": True,
            "new_lines": new_lines,
            "total_lines": total,
            "done": done,
            "success": job["success"] if done else None,
            "error": job["error"] if done else None,
            "message": job.get("message") if done else None,
        }

        if done and since >= total:
            with self._add_job_lock:
                self._add_jobs.pop(job_id, None)
        return resp

    def remove_directory(self, directory: str) -> dict:
        """Remove a directory from monitoring."""
        path = str(Path(directory).resolve())

        with self._lock:
            observer = self._observers.pop(path, None)
            if path in self.config["directories"]:
                self.config["directories"].remove(path)
        _mutate_config_file(lambda cfg: _cfg_remove_directory(cfg, path))

        if observer:
            observer.stop()
            observer.join(timeout=5)
            logger.info("已停止监控: %s", path)

        nm_dir = Path(path) / ".NoteManager"
        if nm_dir.exists():
            import shutil
            shutil.rmtree(nm_dir, ignore_errors=True)
            logger.info("已删除: %s", nm_dir)

        logger.info("已移除: %s", path)
        self._notify_change("directories_changed")
        return {"ok": True, "message": f"已移除: {path}"}

    def rebuild_directory(self, directory: str) -> dict:
        """Force rebuild a directory."""
        path = Path(directory).resolve()
        dir_str = str(path)

        logger.info("重建目录: %s", path)
        try:
            full_build(path)
            self._notify_change("build_complete", {"directory": dir_str})
            return {"ok": True, "message": f"重建完成: {path}"}
        except Exception as e:
            logger.error("重建失败: %s: %s", path, e)
            return {"ok": False, "error": f"重建失败: {e}"}

    def export_project_mobile_zip(self, project_idx: int) -> tuple[bytes, str]:
        """生成供手机本地管家使用的 .html.zip（内存），不写入工程 .NoteManager/html。"""
        from notemanager.export_bundle import export_mobile_html_zip_bytes

        with self._lock:
            dirs = list(self._observers.keys())
        if not 0 <= project_idx < len(dirs):
            raise ValueError("无效工程编号")
        root = Path(dirs[project_idx])
        logger.info("导出 mobile zip: %s", root)
        return export_mobile_html_zip_bytes(
            root, progress=lambda m: logger.info("%s", m),
        )

    def _runtime_config_info(self) -> dict:
        return {
            "pid": os.getpid(),
            "port": self.port,
            "web_port": self.web_port,
            "transport": self.transport,
            "socket_path": self.socket_path,
        }

    def get_pending_config_changes(self) -> dict:
        """Compare config.json with the values this daemon actually runs with.

        A non-empty ``fields`` list means the file has been edited but the
        running instance has not been restarted (or was started with CLI
        overrides that differ from the file).
        """
        try:
            cfg = _load_config()
        except (OSError, ValueError, json.JSONDecodeError) as e:
            return {"fields": [], "changed": False, "error": str(e)}

        fields: list[str] = []

        def _int_or(value, default):
            if isinstance(value, bool):
                return default
            try:
                n = int(value)
            except (TypeError, ValueError):
                return default
            return n if 1 <= n <= 65535 else default

        if _int_or(cfg.get("port"), DEFAULT_PORT) != self.port:
            fields.append("port")
        if _int_or(cfg.get("web_port"), DEFAULT_WEB_PORT) != self.web_port:
            fields.append("web_port")

        cfg_transport = str(cfg.get("transport") or "").strip().lower()
        if cfg_transport not in ("unix", "tcp"):
            cfg_transport = "unix" if use_unix_socket() else "tcp"
        if os.name == "nt":
            cfg_transport = "tcp"
        if cfg_transport != self.transport:
            fields.append("transport")

        if cfg_transport == "unix":
            raw_socket = str(cfg.get("socket_path") or "").strip()
            if raw_socket and not Path(raw_socket).is_absolute():
                raw_socket = ""
            effective_socket = raw_socket or str(DEFAULT_UNIX_SOCKET)
            if effective_socket != self.socket_path:
                fields.append("socket_path")

        with self._lock:
            runtime_dirs = [str(d) for d in self._observers.keys()]
        file_dirs = cfg.get("directories")
        if not isinstance(file_dirs, list):
            file_dirs = []
        file_dirs = [d for d in file_dirs if isinstance(d, str) and d]
        if set(file_dirs) != set(runtime_dirs):
            fields.append("directories")

        if (cfg.get("default_project") or "") != (self.config.get("default_project") or ""):
            fields.append("default_project")
        if cfg.get("editor") != self.config.get("editor"):
            fields.append("editor")

        return {"fields": fields, "changed": bool(fields), "error": None}

    def _port_warnings(self, data: dict) -> list[str]:
        """Soft warnings for ports that cannot be bound (best effort)."""
        warnings: list[str] = []
        effective_transport = str(data.get("transport") or self.transport).lower()
        checks = [("web_port", self.web_port)]
        if effective_transport != "unix":
            checks.append(("port", self.port))
        for key, current in checks:
            value = data.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value == current:
                continue
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.bind(("127.0.0.1", value))
            except OSError:
                warnings.append(f"{key} {value} 当前可能已被占用或无权限绑定")
        return warnings

    def get_config_file(self, source: str = "") -> dict:
        """Return config.json text for the Web config editor."""
        pending = self.get_pending_config_changes()
        try:
            if source == "backup":
                text, sha = config_store.read_text(CONFIG_BACKUP_FILE)
                return {
                    "ok": True,
                    "source": "backup",
                    "path": str(CONFIG_BACKUP_FILE),
                    "exists": text is not None,
                    "text": text or "",
                    "sha256": sha,
                    "runtime": self._runtime_config_info(),
                    "pending_restart": pending["changed"],
                    "pending_fields": pending["fields"],
                    "pending_error": pending["error"],
                }

            text, sha = config_store.read_text(CONFIG_FILE)
        except OSError as e:
            return {"ok": False, "error": f"读取配置失败: {e}"}
        exists = text is not None
        if text is None:
            text = config_store.dump_config(_default_config())
            sha = None
        return {
            "ok": True,
            "source": "config",
            "path": str(CONFIG_FILE),
            "exists": exists,
            "text": text,
            "sha256": sha,
            "backup_path": str(CONFIG_BACKUP_FILE),
            "backup_exists": CONFIG_BACKUP_FILE.is_file(),
            "runtime": self._runtime_config_info(),
            "pending_restart": pending["changed"],
            "pending_fields": pending["fields"],
            "pending_error": pending["error"],
        }

    def save_config_text(
        self, text: str, base_sha256: str | None = None, force: bool = False,
    ) -> dict:
        """Validate and write config.json; never applies it to this process."""
        if not isinstance(text, str):
            return {"ok": False, "error": "配置内容必须是字符串"}
        try:
            data = config_store.parse_config_text(text)
        except config_store.ConfigValidationError as e:
            return {"ok": False, "error": str(e)}

        errors, warnings = config_store.validate_config_data(data)
        if errors:
            return {
                "ok": False,
                "error": "；".join(errors),
                "errors": errors,
                "warnings": warnings,
            }
        editor_warning = config_store.editor_command_warning(data)
        if editor_warning:
            warnings.append(editor_warning)
        warnings.extend(self._port_warnings(data))

        with config_store.CONFIG_WRITE_LOCK:
            try:
                current_text, current_sha = config_store.read_text(CONFIG_FILE)
            except OSError as e:
                return {"ok": False, "error": f"读取配置失败: {e}"}
            if base_sha256 and not force and current_sha != base_sha256:
                return {
                    "ok": False,
                    "conflict": True,
                    "error": "配置文件已被外部修改，请重新加载后再保存",
                    "current_sha256": current_sha,
                    "warnings": warnings,
                }
            if current_text == text:
                pending = self.get_pending_config_changes()
                return {
                    "ok": True,
                    "changed": False,
                    "path": str(CONFIG_FILE),
                    "sha256": current_sha,
                    "runtime": self._runtime_config_info(),
                    "warnings": warnings,
                    "restart_required": pending["changed"],
                    "pending_restart": pending["changed"],
                    "pending_fields": pending["fields"],
                    "pending_error": pending["error"],
                }

            backup_path = None
            try:
                backup_path = _backup_config()
                config_store.write_text_atomic(
                    CONFIG_FILE, text, mode=_config_file_mode(),
                )
            except OSError as e:
                return {"ok": False, "error": f"保存配置失败: {e}"}

        saved = {
            "port": data.get("port"),
            "web_port": data.get("web_port"),
            "transport": data.get("transport"),
            "socket_path": data.get("socket_path"),
        }
        saved_web = saved.get("web_port")
        if isinstance(saved_web, int) and saved_web != self.web_port:
            warnings.append(
                f"web_port 已从 {self.web_port} 改为 {saved_web}；"
                f"重启后请访问 http://localhost:{saved_web}/"
            )
        pending = self.get_pending_config_changes()
        return {
            "ok": True,
            "changed": True,
            "path": str(CONFIG_FILE),
            "sha256": config_store.sha256_text(text),
            "backup_path": str(backup_path) if backup_path else "",
            "runtime": self._runtime_config_info(),
            "saved": saved,
            "warnings": warnings,
            "restart_required": True,
            "pending_restart": pending["changed"],
            "pending_fields": pending["fields"],
            "pending_error": pending["error"],
        }

    def list_directories(self) -> dict:
        """List all monitored directories."""
        with self._lock:
            dirs = list(self._observers.keys())
        return {
            "ok": True,
            "data": {
                "directories": dirs,
                "count": len(dirs),
                "default_project": self.config.get("default_project"),
            },
        }

    def get_status(self) -> dict:
        """Get daemon status."""
        with self._lock:
            dirs = list(self._observers.keys())
        status_info = {}
        for d in dirs:
            nm_status = Path(d) / ".NoteManager" / "status.json"
            if nm_status.exists():
                try:
                    status_info[d] = json.loads(
                        nm_status.read_text(encoding="utf-8")
                    )
                except Exception:
                    status_info[d] = {"error": "无法读取状态"}
            else:
                status_info[d] = {"error": "尚未构建"}
        config_pending = self.get_pending_config_changes()
        now = time.time()
        with self._web_lock:
            self._maybe_elect_main_locked(now)
            web_clients = self._web_client_payloads_locked()
            main_client = self._main_id
            main_connected = bool(
                main_client and broadcaster.get_client(main_client) is not None
            )
        return {
            "ok": True,
            "data": {
                "version": NOTEMANAGER_VERSION,
                "pid": os.getpid(),
                "port": self.port,
                "transport": self.transport,
                "socket_path": self.socket_path,
                "web_port": self.web_port,
                "directories": dirs,
                "directory_count": len(dirs),
                "details": status_info,
                "default_project": self.config.get("default_project"),
                "startup_complete": self._startup_complete_event.is_set(),
                "clients": web_clients,
                "client_count": len(web_clients),
                "main_client": main_client,
                "main_connected": main_connected,
                "config_pending": config_pending["changed"],
                "config_pending_fields": config_pending["fields"],
                "config_pending_error": config_pending["error"],
            },
        }

    def handle_search(self, cmd: dict) -> dict:
        """Handle CLI 'search' command: send it to the main/specified page."""
        query = cmd.get("query", "")
        if not query:
            return {"ok": False, "error": "需要搜索关键词"}

        with self._lock:
            dirs = list(self._observers.keys())

        if not dirs:
            return {"ok": False, "error": "没有监控中的目录"}

        project = cmd.get("project")
        idx = self._resolve_project_index(project, dirs)
        if idx is None:
            return {"ok": False, "error": f"无效的工程标识: {project}"}

        project_dir = Path(dirs[idx])
        payload: dict = {"query": query, "project": idx}
        open_url = _first_search_hit_open_url(idx, project_dir, query)
        if open_url:
            payload["open_url"] = open_url

        if cmd.get("new"):
            with self._web_lock:
                new_id = self._new_web_client_id()
            base_url = open_url or f"/p/{idx}/?q={quote(str(query), safe='')}"
            new_url = self._append_query_param(base_url, "nm_client_id", new_id)
            return {
                "ok": True,
                "message": f"已在新页面打开搜索: {query}",
                "url": new_url,
                "new_url": new_url,
                "new_client_id": new_id,
                "web_port": self.web_port,
                "delivered": False,
            }

        raw_target = str(cmd.get("target", "") or "").strip()
        now = time.time()
        with self._web_lock:
            self._maybe_elect_main_locked(now)
            clients = self._web_client_payloads_locked()
            if raw_target:
                target = self._find_web_client_locked(raw_target)
                if not target:
                    return {
                        "ok": False,
                        "error": f"找不到已连接的页面: {raw_target}",
                        "clients": clients,
                    }
            else:
                target = self._main_id if (
                    self._main_id and broadcaster.get_client(self._main_id) is not None
                ) else None
            main_client = self._main_id
            main_connected = bool(
                main_client and broadcaster.get_client(main_client) is not None
            )

        delivered = broadcaster.send_to(target, "cli_search", payload) if target else False
        if delivered:
            message = f"已发送搜索: {query}"
            if target:
                message += f"（页面 {target}）"
        elif raw_target:
            message = f"页面 {raw_target} 已断开"
        else:
            message = "没有可用的主页面，搜索结果仅在终端显示"
        return {
            "ok": True,
            "message": message,
            "url": open_url,
            "web_port": self.web_port,
            "delivered": delivered,
            "target_client": target,
            "main_client": main_client,
            "main_connected": main_connected,
            "clients": clients,
        }

    def handle_default(self, cmd: dict) -> dict:
        """Set the default project."""
        project = cmd.get("project", "")
        if not project:
            # Show current default
            default = self.config.get("default_project")
            if default:
                return {"ok": True, "message": f"当前默认工程: {default}"}
            return {"ok": True, "message": "未设置默认工程"}

        with self._lock:
            dirs = list(self._observers.keys())
        if not dirs:
            return {"ok": False, "error": "没有监控中的目录"}

        idx = self._resolve_project_index(project, dirs)
        if idx is None:
            return {"ok": False, "error": f"未找到工程: {project}"}

        self.config["default_project"] = dirs[idx]
        _mutate_config_file(lambda cfg: _cfg_set_default(cfg, dirs[idx]))
        self._notify_change("default_changed")
        return {"ok": True, "message": f"已设置默认工程: [{idx}] {Path(dirs[idx]).name}"}

    def handle_view(self, cmd: dict) -> dict:
        """Handle CLI 'view' command: show a source file in one web page."""
        file_path = cmd.get("file", "")
        if not file_path:
            return {"ok": False, "error": "缺少文件路径"}
        source = Path(file_path).expanduser().resolve()
        if not source.is_file():
            return {"ok": False, "error": f"文件不存在: {source}"}
        if source.suffix.lower() not in (".md", ".rst", ".txt"):
            return {"ok": False, "error": "仅支持 .md/.rst/.txt 文件"}

        with self._lock:
            dirs = list(self._observers.keys())

        target_url = ""
        message = ""
        # If file belongs to a managed project, jump to that project HTML directly.
        for idx, d in enumerate(dirs):
            root = Path(d)
            try:
                rel = source.relative_to(root)
            except ValueError:
                continue
            target_url = f"/p/{idx}/{rel.with_suffix('.html').as_posix()}"
            message = f"已跳转: {target_url}"
            break
        if not target_url:
            target_url = self._render_temp_view(source)
            message = f"已临时渲染: {target_url}"

        if cmd.get("new"):
            with self._web_lock:
                new_id = self._new_web_client_id()
            new_url = self._append_query_param(target_url, "nm_client_id", new_id)
            return {
                "ok": True,
                "message": f"已在新页面打开: {target_url}",
                "url": new_url,
                "new_url": new_url,
                "new_client_id": new_id,
                "web_port": self.web_port,
                "delivered": False,
            }

        raw_target = str(cmd.get("target", "") or "").strip()
        now = time.time()
        with self._web_lock:
            self._maybe_elect_main_locked(now)
            clients = self._web_client_payloads_locked()
            if raw_target:
                target = self._find_web_client_locked(raw_target)
                if not target:
                    return {
                        "ok": False,
                        "error": f"找不到已连接的页面: {raw_target}",
                        "clients": clients,
                    }
            else:
                target = self._main_id if (
                    self._main_id and broadcaster.get_client(self._main_id) is not None
                ) else None
            main_client = self._main_id
            main_connected = bool(
                main_client and broadcaster.get_client(main_client) is not None
            )

        delivered = broadcaster.send_to(target, "cli_view", {"url": target_url}) if target else False
        if delivered:
            message += f"（页面 {target}）"
        elif raw_target:
            message = f"页面 {raw_target} 已断开"
        elif not clients:
            message = f"没有已连接的 Web 页面: {target_url}"
        else:
            message = "没有可用的主页面（可用 nmr status 查看页面）"
        return {
            "ok": True,
            "message": message,
            "url": target_url,
            "web_port": self.web_port,
            "delivered": delivered,
            "target_client": target,
            "main_client": main_client,
            "main_connected": main_connected,
            "clients": clients,
        }

    def get_temp_view_dir(self) -> Path:
        """Directory for temporary rendered single-file pages."""
        temp_dir = HOME_NM_DIR / "temp_views"
        temp_dir.mkdir(parents=True, exist_ok=True)
        return temp_dir

    @staticmethod
    def _temp_key_for_source(source: Path) -> str:
        return hashlib.sha1(str(source.resolve()).encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _current_temp_renderer_signature() -> str:
        return _temp_renderer_signature()

    def _refresh_temp_view_if_stale(self, html_path: Path) -> None:
        """If source file changed, re-render the temporary HTML in-place."""
        try:
            key = html_path.name[:-len(".html")]
        except Exception:
            return
        meta_path = html_path.with_suffix(".meta.json")
        if not meta_path.is_file():
            return
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return
        source_path = meta.get("source_path")
        if not source_path:
            return
        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            return
        try:
            current_mtime = source.stat().st_mtime_ns
        except OSError:
            return
        current_sig = self._current_temp_renderer_signature()
        if int(meta.get("mtime_ns", -1)) == current_mtime and str(meta.get("renderer_sig", "")) == current_sig:
            return
        self._render_temp_view(source, force=True)

    def _render_temp_view(self, source: Path, *, force: bool = False) -> str:
        """Render one unmanaged source file to temporary HTML.

        A cached temp page is refreshed automatically when the source file's mtime changes.
        """
        content = source.read_text(encoding="utf-8")
        if source.suffix.lower() == ".rst":
            rendered = render_rst(content, str(source))
        elif source.suffix.lower() == ".txt":
            rendered = render_txt(content, str(source))
        else:
            rendered = render_md(content, str(source))

        title = source.stem
        body = rendered.get("body", "")
        body = self._rewrite_temp_body_links(body, source)
        toc = rendered.get("toc", "")
        warnings = rendered.get("warnings", [])
        html = wrap_html(
            body=body,
            title=title,
            html_rel_path="index.html",
            toc=toc,
            source=content,
            warnings=warnings,
            source_path=str(source),
            dtags=rendered.get("dtags", []),
            source_editable=False,
            heading_lines=rendered.get("heading_lines"),
            # 临时单文件不属于任何工程，没有工程首页；Home 回控制台让用户选工程。
            home_href="/",
        )

        temp_dir = self.get_temp_view_dir()
        static_dir = temp_dir / "_static"
        static_dir.mkdir(parents=True, exist_ok=True)
        from notemanager.builder import _copy_static_assets  # local import
        _copy_static_assets(static_dir)

        key = self._temp_key_for_source(source)
        out_name = f"{key}.html"
        meta_name = f"{key}.meta.json"
        meta = {
            "source_path": str(source),
            "mtime_ns": source.stat().st_mtime_ns,
            "renderer_sig": self._current_temp_renderer_signature(),
            "rendered_at": time.time(),
            "title": title,
        }
        (temp_dir / out_name).write_text(html, encoding="utf-8")
        (temp_dir / meta_name).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"/temp/{out_name}"

    def _rewrite_temp_body_links(self, html_body: str, source: Path) -> str:
        """Rewrite unmanaged-body local links/assets to temp handlers."""
        if not html_body:
            return html_body
        from html.parser import HTMLParser
        import urllib.parse

        base_dir = source.parent

        def _is_external_scheme(s: str) -> bool:
            sl = s.lower()
            return sl.startswith(("http://", "https://", "mailto:", "javascript:", "data:"))

        def _resolve_local_ref(raw: str) -> tuple[Path | None, str]:
            parsed = urllib.parse.urlparse(raw)
            frag = parsed.fragment or ""
            if parsed.scheme and parsed.scheme.lower() != "file":
                return None, frag
            if parsed.netloc and parsed.scheme.lower() != "file":
                return None, frag
            p_raw = parsed.path or ""
            if parsed.scheme.lower() == "file":
                p_raw = urllib.parse.unquote(p_raw)
            if not p_raw:
                return None, frag
            cand = Path(p_raw)
            if not cand.is_absolute():
                cand = (base_dir / cand).resolve()
            else:
                cand = cand.resolve()
            return cand, frag

        def _note_target_from_href(raw_href: str) -> tuple[Path | None, str]:
            target, frag = _resolve_local_ref(raw_href)
            if target is None:
                return None, frag
            suf = target.suffix.lower()
            if suf in (".md", ".rst", ".txt") and target.is_file():
                return target, frag
            if suf == ".html":
                stem = target.with_suffix("")
                for ext in (".md", ".rst", ".txt"):
                    cand = stem.with_suffix(ext)
                    if cand.is_file():
                        return cand, frag
            return None, frag

        class _TempLinkRewriter(HTMLParser):
            def __init__(self) -> None:
                super().__init__(convert_charrefs=False)
                self.parts: list[str] = []

            def _fmt(self, tag: str, attrs) -> str:
                if attrs:
                    return "<" + tag + " " + " ".join(f'{k}="{v}"' for k, v in attrs) + ">"
                return f"<{tag}>"

            def handle_starttag(self, tag: str, attrs):
                new_attrs = list(attrs)
                for i, (k, v) in enumerate(new_attrs):
                    if not isinstance(v, str):
                        continue
                    if k == "href":
                        href = v.strip()
                        if not href or href.startswith("#") or _is_external_scheme(href):
                            continue
                        note_target, frag = _note_target_from_href(href)
                        if note_target is not None:
                            q = urlencode({"file": str(note_target), "base": str(base_dir)})
                            new_href = f"/temp/open?{q}"
                            if frag:
                                new_href += "#" + urllib.parse.quote(frag, safe="")
                            new_attrs[i] = (k, new_href)
                            continue
                        local_target, _ = _resolve_local_ref(href)
                        if local_target is not None and local_target.is_file():
                            q = urlencode({"file": str(local_target), "base": str(base_dir)})
                            new_attrs[i] = (k, f"/temp/asset?{q}")
                    elif k == "src":
                        src = v.strip()
                        if not src or _is_external_scheme(src):
                            continue
                        local_target, _ = _resolve_local_ref(src)
                        if local_target is not None and local_target.is_file():
                            q = urlencode({"file": str(local_target), "base": str(base_dir)})
                            new_attrs[i] = (k, f"/temp/asset?{q}")
                self.parts.append(self._fmt(tag, new_attrs))

            def handle_endtag(self, tag: str):
                self.parts.append(f"</{tag}>")

            def handle_data(self, data: str):
                self.parts.append(data)

            def handle_entityref(self, name: str):
                self.parts.append(f"&{name};")

            def handle_charref(self, name: str):
                self.parts.append(f"&#{name};")

            def handle_comment(self, data: str):
                self.parts.append(f"<!--{data}-->")

            def handle_decl(self, decl: str):
                self.parts.append(f"<!{decl}>")

            def handle_pi(self, data: str):
                self.parts.append(f"<?{data}>")

            def get_result(self) -> str:
                return "".join(self.parts)

        parser = _TempLinkRewriter()
        try:
            parser.feed(html_body)
            parser.close()
            return parser.get_result()
        except Exception:
            return html_body

    def _validated_source_rel(
        self, project_root: Path, source_rel_posix: str,
    ) -> tuple[Path, Path] | tuple[None, None]:
        """若合法则返回 (工程根 pathlib.Path, 源文件相对 Path)，否则 (None, None)。"""
        rel = Path(source_rel_posix.replace("\\", "/"))
        if rel.is_absolute() or any(p == ".." for p in rel.parts):
            return None, None
        if rel.suffix.lower() not in (".md", ".rst", ".txt"):
            return None, None
        root = project_root.resolve()
        abs_src = (root / rel).resolve()
        if not str(abs_src).startswith(str(root)):
            return None, None
        return root, rel

    def save_managed_source(self, project_idx: int, source_rel_posix: str, content: str) -> dict:
        """将源码写回磁盘并增量重建该文件。"""
        with self._lock:
            dirs = list(self._observers.keys())
        if not (0 <= project_idx < len(dirs)):
            return {"ok": False, "error": "无效工程编号"}
        root, rel = self._validated_source_rel(Path(dirs[project_idx]), source_rel_posix)
        if rel is None:
            return {"ok": False, "error": "非法源文件路径"}
        abs_src = (root / rel).resolve()
        try:
            old = abs_src.read_text(encoding="utf-8")
            if old == content:
                return {"ok": True}
        except OSError:
            pass
        try:
            suppress_path(abs_src)
            abs_src.write_text(content, encoding="utf-8", newline="\n")
        except OSError as e:
            return {"ok": False, "error": str(e)}
        try:
            from notemanager.builder import update_file

            update_file(
                root,
                abs_src,
                refresh_static_assets=False,
                refresh_project_assets=False,
            )
        except Exception as e:
            logger.exception("保存后重建失败")
            return {"ok": False, "error": f"重建失败: {e}"}
        return {"ok": True}

    def create_note_same_dir(
        self,
        project_idx: int,
        source_rel_posix: str,
        filename: str,
        default_ext: str,
    ) -> dict:
        """在与当前源文件同一目录下新建 .md / .rst 笔记并增量构建。"""
        with self._lock:
            dirs = list(self._observers.keys())
        if not (0 <= project_idx < len(dirs)):
            return {"ok": False, "error": "无效工程编号"}
        root, rel = self._validated_source_rel(Path(dirs[project_idx]), source_rel_posix)
        if rel is None:
            return {"ok": False, "error": "非法源文件路径"}
        return self._create_note_in_parent(
            project_idx, rel.parent.as_posix(), filename, default_ext
        )

    def create_note_in_dir(
        self,
        project_idx: int,
        dir_rel_posix: str,
        filename: str,
        default_ext: str,
    ) -> dict:
        """在指定目录下新建 .md / .rst 笔记并增量构建。"""
        return self._create_note_in_parent(
            project_idx, dir_rel_posix, filename, default_ext
        )

    def _create_note_in_parent(
        self,
        project_idx: int,
        parent_rel_posix: str,
        filename: str,
        default_ext: str,
    ) -> dict:
        with self._lock:
            dirs = list(self._observers.keys())
        if not (0 <= project_idx < len(dirs)):
            return {"ok": False, "error": "无效工程编号"}
        root = Path(dirs[project_idx]).resolve()
        root_r = root.resolve()

        raw_parent = str(parent_rel_posix or "").strip().replace("\\", "/")
        if raw_parent in ("", "."):
            parent = Path(".")
        else:
            pp = PurePosixPath(raw_parent)
            if pp.is_absolute() or any(part in ("", "..") for part in pp.parts):
                return {"ok": False, "error": "非法目录路径"}
            if ".NoteManager" in pp.parts:
                return {"ok": False, "error": "拒绝在 .NoteManager 内创建笔记"}
            parent = Path(*pp.parts)
        try:
            parent_abs = (root / parent).resolve()
            parent_abs.relative_to(root_r)
        except (OSError, ValueError):
            return {"ok": False, "error": "拒绝目录路径"}
        if not parent_abs.is_dir():
            return {"ok": False, "error": "目标目录不存在"}

        ext = (default_ext or "").lower()
        if ext not in (".md", ".rst"):
            ext = ".md"
        name = (filename or "").strip()
        if not name:
            return {"ok": False, "error": "文件名为空"}
        if name in (".", ".."):
            return {"ok": False, "error": "无效文件名"}
        if any(c in name for c in '<>:"|?*\\\x00') or "/" in name or "\\" in name:
            return {"ok": False, "error": "文件名含有非法字符或路径分隔符"}
        if len(name) > 160:
            return {"ok": False, "error": "文件名过长"}
        bn = Path(name).name
        if bn != name:
            return {"ok": False, "error": "仅允许单个文件名，不要包含路径"}
        low = name.lower()
        if low.endswith(".md"):
            out_name = name[:-3] + ".md"
            out_ext = ".md"
        elif low.endswith(".rst"):
            out_name = name[:-4] + ".rst"
            out_ext = ".rst"
        else:
            out_ext = ext
            out_name = name + out_ext

        new_rel = (parent / out_name).as_posix()
        try:
            new_abs = (root / Path(new_rel)).resolve()
            new_abs.relative_to(root_r)
        except (OSError, ValueError):
            return {"ok": False, "error": "拒绝路径"}
        if new_abs.exists():
            return {"ok": False, "error": "文件已存在"}

        stem = Path(out_name).stem.replace("_", " ").replace("-", " ").strip() or "Untitled"
        today = datetime.date.today().isoformat()
        if out_ext == ".rst":
            title_line = "=" * max(len(stem), 3)
            initial = (
                f"{stem}\n"
                f"{title_line}\n\n"
                f":创建日期: {today}\n\n"
                "TODO：\n\n"
                "- [ ] TODO\n"
            )
        else:
            initial = (
                f"# {stem}\n\n"
                f"创建日期：{today}\n\n"
                "## TODO\n\n"
                "- [ ] TODO\n"
            )
        try:
            suppress_path(new_abs)
            new_abs.parent.mkdir(parents=True, exist_ok=True)
            new_abs.write_text(initial, encoding="utf-8", newline="\n")
        except OSError as e:
            return {"ok": False, "error": str(e)}
        try:
            from notemanager.builder import update_file

            update_file(
                root,
                new_abs,
                refresh_static_assets=False,
                refresh_project_assets=False,
            )
        except Exception as e:
            try:
                if new_abs.is_file():
                    new_abs.unlink()
            except OSError:
                pass
            logger.exception("新建笔记后重建失败")
            return {"ok": False, "error": f"重建失败: {e}"}
        html_rel = Path(new_rel).with_suffix(".html").as_posix()
        open_url = "/p/%d/%s" % (
            project_idx,
            "/".join(quote(part, safe="") for part in Path(html_rel).parts),
        )
        self._notify_change("build_complete", {"directory": str(root_r)})
        return {
            "ok": True,
            "source_rel_path": new_rel,
            "html_rel_path": html_rel,
            "open_url": open_url,
        }

    def _read_asset_index(self, root: Path) -> dict:
        path = root / ".NoteManager" / "html" / "_index" / "assets.json"
        if not path.is_file():
            return {"assets": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"assets": {}}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {"assets": {}}

    def _asset_usage(self, root: Path, source_rel: str) -> tuple[list[str], list[str]]:
        """Return (all referenced local images, exclusively referenced images)."""
        data = self._read_asset_index(root)
        all_refs = []
        exclusive = []
        for rel, entry in (data.get("assets") or {}).items():
            refs = entry.get("referenced_by") or []
            if not any(r.get("source_path") == source_rel for r in refs):
                continue
            if not entry.get("exists"):
                continue
            all_refs.append(rel)
            if len(refs) == 1 and refs[0].get("source_path") == source_rel:
                exclusive.append(rel)
        return sorted(all_refs), sorted(exclusive)

    def note_delete_info(self, project_idx: int, source_rel_posix: str) -> dict:
        """Return impact data before moving a note to trash."""
        with self._lock:
            dirs = list(self._observers.keys())
        if not (0 <= project_idx < len(dirs)):
            return {"ok": False, "error": "无效工程编号"}
        root, rel = self._validated_source_rel(Path(dirs[project_idx]), source_rel_posix)
        if rel is None:
            return {"ok": False, "error": "非法源文件路径"}
        root_r = root.resolve()
        abs_src = (root / rel).resolve()
        try:
            abs_src.relative_to(root_r)
        except ValueError:
            return {"ok": False, "error": "拒绝路径"}
        if not abs_src.is_file():
            return {"ok": False, "error": "文件不存在"}

        html_rel = rel.with_suffix(".html").as_posix()
        title = rel.stem
        backlinks = []
        relations_file = root / ".NoteManager" / "html" / "_index" / "relations.json"
        if relations_file.is_file():
            try:
                rel_data = json.loads(relations_file.read_text(encoding="utf-8"))
                doc = (rel_data.get("docs") or {}).get(html_rel) or {}
                backlinks = doc.get("backlinks") or []
                if doc.get("title"):
                    title = str(doc["title"])
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                backlinks = []

        source_rel = rel.as_posix()
        images, exclusive = self._asset_usage(root_r, source_rel)

        return {
            "ok": True,
            "source_rel_path": source_rel,
            "html_rel_path": html_rel,
            "title": title,
            "backlink_count": len(backlinks),
            "backlinks": backlinks[:20],
            "image_count": len(images),
            "images": images[:20],
            "exclusive_image_count": len(exclusive),
            "exclusive_images": exclusive[:20],
            "shared_image_count": max(0, len(images) - len(exclusive)),
        }

    def _trash_root(self, root: Path) -> Path:
        return root / ".NoteManager" / "trash"

    def _new_trash_batch(self, root: Path) -> Path:
        trash = self._trash_root(root)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        batch = trash / stamp
        n = 1
        while batch.exists():
            batch = trash / f"{stamp}-{n}"
            n += 1
        batch.mkdir(parents=True)
        return batch

    def _restore_batch(self, root: Path, batch: Path, meta: dict) -> None:
        source_rel = meta.get("source_rel") or ""
        if source_rel:
            src = batch / "source" / source_rel
            dst = root / source_rel
            if src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
        for asset in meta.get("assets") or []:
            src = batch / "assets" / asset
            dst = root / asset
            if src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
        for asset in meta.get("sidecars") or []:
            src = batch / "assets" / asset
            dst = root / asset
            if src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
        shutil.rmtree(batch, ignore_errors=True)

    def delete_managed_source(
        self,
        project_idx: int,
        source_rel_posix: str,
        delete_assets: bool = False,
    ) -> dict:
        """把当前监控工程内的源文件移到工程回收站并更新索引与 HTML。"""
        with self._lock:
            dirs = list(self._observers.keys())
        if not (0 <= project_idx < len(dirs)):
            return {"ok": False, "error": "无效工程编号"}
        root, rel = self._validated_source_rel(Path(dirs[project_idx]), source_rel_posix)
        if rel is None:
            return {"ok": False, "error": "非法源文件路径"}
        root_r = root.resolve()
        abs_src = (root / rel).resolve()
        try:
            abs_src.relative_to(root_r)
        except ValueError:
            return {"ok": False, "error": "拒绝路径"}
        if not abs_src.is_file():
            return {"ok": False, "error": "文件不存在"}

        source_rel = rel.as_posix()
        all_refs, exclusive = self._asset_usage(root_r, source_rel)
        assets_to_move = exclusive if delete_assets else []
        sidecars_to_move = []
        for asset in assets_to_move:
            sidecar = asset + ".ann.svg"
            if (root_r / sidecar).is_file():
                sidecars_to_move.append(sidecar)
        batch = None
        meta = {
            "version": 1,
            "kind": "note",
            "source_rel": source_rel,
            "html_rel": rel.with_suffix(".html").as_posix(),
            "assets": assets_to_move,
            "sidecars": sidecars_to_move,
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        try:
            batch = self._new_trash_batch(root_r)
            src_dest = batch / "source" / rel
            src_dest.parent.mkdir(parents=True, exist_ok=True)
            suppress_path(abs_src)
            shutil.move(str(abs_src), str(src_dest))
            moved_assets = []
            for asset in assets_to_move:
                asset_abs = (root_r / asset).resolve()
                try:
                    asset_abs.relative_to(root_r)
                except ValueError:
                    continue
                if not asset_abs.is_file():
                    continue
                asset_dest = batch / "assets" / asset
                asset_dest.parent.mkdir(parents=True, exist_ok=True)
                suppress_path(asset_abs)
                shutil.move(str(asset_abs), str(asset_dest))
                moved_assets.append(asset)
            meta["assets"] = moved_assets
            moved_sidecars = []
            for sidecar in sidecars_to_move:
                side_abs = (root_r / sidecar).resolve()
                try:
                    side_abs.relative_to(root_r)
                except ValueError:
                    continue
                if not side_abs.is_file():
                    continue
                side_dest = batch / "assets" / sidecar
                side_dest.parent.mkdir(parents=True, exist_ok=True)
                suppress_path(side_abs)
                shutil.move(str(side_abs), str(side_dest))
                moved_sidecars.append(sidecar)
            meta["sidecars"] = moved_sidecars
            (batch / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            if batch is not None:
                try:
                    self._restore_batch(root_r, batch, meta)
                except OSError:
                    pass
            return {"ok": False, "error": f"移动到回收站失败: {e}"}

        try:
            from notemanager.builder import remove_file

            remove_file(root, abs_src)
        except Exception as e:
            logger.exception("删除笔记后更新索引失败")
            if batch is not None:
                try:
                    self._restore_batch(root_r, batch, meta)
                except OSError:
                    pass
            return {"ok": False, "error": f"更新索引失败: {e}"}
        html_dir = root_r / ".NoteManager" / "html"
        for asset in list(meta.get("assets") or []) + list(meta.get("sidecars") or []):
            try:
                generated = (html_dir / asset).resolve()
                generated.relative_to(html_dir.resolve())
                if generated.is_file():
                    generated.unlink()
            except (OSError, ValueError):
                pass
        self._notify_change("build_complete", {"directory": str(root_r)})
        return {
            "ok": True,
            "redirect_html": "index.html",
            "trash_rel": batch.relative_to(root_r).as_posix(),
            "trash_id": batch.name,
            "assets_deleted": meta["assets"],
        }

    def _write_asset_index(self, root: Path, data: dict) -> None:
        path = root / ".NoteManager" / "html" / "_index" / "assets.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _remove_assets_from_index(self, root: Path, paths: list[str]) -> None:
        data = self._read_asset_index(root)
        assets = data.setdefault("assets", {})
        remove = set(paths)
        for path in remove:
            assets.pop(path, None)
        data["orphans"] = [p for p in (data.get("orphans") or []) if p not in remove]
        data["missing"] = [p for p in (data.get("missing") or []) if p not in remove]
        self._write_asset_index(root, data)

    def _add_assets_to_index(self, root: Path, paths: list[str]) -> None:
        data = self._read_asset_index(root)
        assets = data.setdefault("assets", {})
        for rel in paths:
            abs_path = root / rel
            try:
                size = abs_path.stat().st_size
            except OSError:
                size = 0
            entry = assets.setdefault(rel, {
                "path": rel,
                "referenced_by": [],
                "exists": True,
                "size": 0,
            })
            entry["exists"] = abs_path.is_file()
            entry["size"] = size
            entry.setdefault("referenced_by", [])
        data["orphans"] = sorted(set(data.get("orphans") or []) | {
            p for p in paths if (root / p).is_file()
        })
        self._write_asset_index(root, data)

    def list_orphan_assets(self, project_idx: int) -> dict:
        with self._lock:
            dirs = list(self._observers.keys())
        if not (0 <= project_idx < len(dirs)):
            return {"ok": False, "error": "无效工程编号"}
        root = Path(dirs[project_idx]).resolve()
        data = self._read_asset_index(root)
        items = []
        for rel in data.get("orphans") or []:
            abs_path = root / rel
            if not abs_path.is_file():
                continue
            try:
                size = abs_path.stat().st_size
            except OSError:
                size = 0
            items.append({"path": rel, "size": size})
        missing = [
            p for p in (data.get("missing") or [])
            if not (root / p).is_file()
        ]
        return {
            "ok": True,
            "items": items,
            "missing": missing,
            "total_images": len(data.get("assets") or {}),
        }

    def list_assets(self, project_idx: int) -> dict:
        """List all managed image resources, including referenced ones."""
        with self._lock:
            dirs = list(self._observers.keys())
        if not (0 <= project_idx < len(dirs)):
            return {"ok": False, "error": "无效工程编号"}
        root = Path(dirs[project_idx]).resolve()
        data = self._read_asset_index(root)
        items = []
        orphan_count = 0
        for rel, entry in (data.get("assets") or {}).items():
            if not entry.get("exists"):
                continue
            refs = entry.get("referenced_by") or []
            if not refs:
                orphan_count += 1
            items.append({
                "path": rel,
                "size": entry.get("size", 0),
                "reference_count": len(refs),
                "referenced_by": [r.get("source_path", "") for r in refs[:12]],
            })
        missing = [
            p for p in (data.get("missing") or [])
            if not (root / p).is_file()
        ]
        items.sort(key=lambda item: (item["reference_count"] > 0, item["path"]))
        return {
            "ok": True,
            "items": items,
            "missing": missing,
            "total_images": len(items),
            "orphan_count": orphan_count,
        }

    def trash_orphan_assets(
        self, project_idx: int, paths: list, allow_referenced: bool = False
    ) -> dict:
        with self._lock:
            dirs = list(self._observers.keys())
        if not (0 <= project_idx < len(dirs)):
            return {"ok": False, "error": "无效工程编号"}
        root = Path(dirs[project_idx]).resolve()
        data = self._read_asset_index(root)
        allowed_set = (
            set(data.get("assets") or {})
            if allow_referenced else set(data.get("orphans") or [])
        )
        selected = []
        for raw in paths or []:
            rel = str(raw or "").replace("\\", "/").strip()
            if rel not in allowed_set or rel in selected:
                continue
            abs_path = (root / rel).resolve()
            try:
                abs_path.relative_to(root)
            except ValueError:
                continue
            if abs_path.is_file():
                selected.append(rel)
        if not selected:
            return {"ok": False, "error": "没有可删除的资源"}

        batch = None
        sidecars = []
        for rel in selected:
            sidecar = rel + ".ann.svg"
            if (root / sidecar).is_file():
                sidecars.append(sidecar)
        try:
            batch = self._new_trash_batch(root)
            moved = []
            moved_sidecars = []
            for rel in selected:
                src = (root / rel).resolve()
                dest = batch / "assets" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                suppress_path(src)
                shutil.move(str(src), str(dest))
                moved.append(rel)
                generated = (root / ".NoteManager" / "html" / rel).resolve()
                try:
                    generated.relative_to((root / ".NoteManager" / "html").resolve())
                    if generated.is_file():
                        generated.unlink()
                except (OSError, ValueError):
                    pass
            for sidecar in sidecars:
                side_src = (root / sidecar).resolve()
                if not side_src.is_file():
                    continue
                side_dest = batch / "assets" / sidecar
                side_dest.parent.mkdir(parents=True, exist_ok=True)
                suppress_path(side_src)
                shutil.move(str(side_src), str(side_dest))
                moved_sidecars.append(sidecar)
                generated = (root / ".NoteManager" / "html" / sidecar).resolve()
                try:
                    generated.relative_to((root / ".NoteManager" / "html").resolve())
                    if generated.is_file():
                        generated.unlink()
                except (OSError, ValueError):
                    pass
            meta = {
                "version": 1,
                "kind": "assets",
                "title": "未引用图片清理",
                "assets": moved,
                "sidecars": moved_sidecars,
                "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
            }
            (batch / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as e:
            if batch is not None:
                for rel in selected + sidecars:
                    src = batch / "assets" / rel
                    dst = root / rel
                    if src.is_file():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(src), str(dst))
                shutil.rmtree(batch, ignore_errors=True)
            return {"ok": False, "error": f"清理图片失败: {e}"}
        self._remove_assets_from_index(root, selected)
        self._notify_change("build_complete", {"directory": str(root)})
        return {
            "ok": True,
            "trash_id": batch.name,
            "asset_count": len(selected),
        }

    def list_trash(self, project_idx: int) -> dict:
        with self._lock:
            dirs = list(self._observers.keys())
        if not (0 <= project_idx < len(dirs)):
            return {"ok": False, "error": "无效工程编号"}
        root = Path(dirs[project_idx]).resolve()
        trash = self._trash_root(root)
        if not trash.is_dir():
            return {"ok": True, "items": []}
        items = []
        for batch in sorted(trash.iterdir(), reverse=True):
            if not batch.is_dir():
                continue
            meta_file = batch / "meta.json"
            meta = None
            if meta_file.is_file():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    meta = None
            kind = "note"
            if meta and meta.get("kind") == "assets":
                source_rel = ""
                assets = list(meta.get("assets") or [])
                title = str(meta.get("title") or "未引用图片清理")
                kind = "assets"
            elif meta and meta.get("source_rel"):
                source_rel = str(meta["source_rel"])
                assets = list(meta.get("assets") or [])
                title = Path(source_rel).stem
                source = batch / "source" / source_rel
            else:
                # Compatibility with the original trash layout:
                # .NoteManager/trash/<timestamp>/<source_rel>
                candidates = [
                    p for p in batch.rglob("*")
                    if p.is_file() and p.suffix.lower() in (".md", ".rst", ".txt")
                ]
                if not candidates:
                    continue
                source = candidates[0]
                source_rel = source.relative_to(batch).as_posix()
                assets = []
                title = source.stem
                meta = {"kind": "legacy", "source_rel": source_rel, "assets": []}
                kind = "legacy"
            try:
                size = sum(p.stat().st_size for p in batch.rglob("*") if p.is_file())
            except OSError:
                size = 0
            items.append({
                "trash_id": batch.name,
                "source_rel_path": source_rel,
                "title": title,
                "kind": kind,
                "asset_count": len(assets),
                "assets": assets,
                "created_at": (meta or {}).get("created_at", ""),
                "legacy": not (batch / "meta.json").is_file(),
                "size": size,
            })
        items.sort(key=lambda x: (x["trash_id"], x["source_rel_path"]), reverse=True)
        return {"ok": True, "items": items}

    def restore_trash(self, project_idx: int, trash_id: str) -> dict:
        with self._lock:
            dirs = list(self._observers.keys())
        if not (0 <= project_idx < len(dirs)):
            return {"ok": False, "error": "无效工程编号"}
        root = Path(dirs[project_idx]).resolve()
        trash = self._trash_root(root)
        raw = str(trash_id or "").strip()
        if not raw or "/" in raw or "\\" in raw or raw in (".", ".."):
            return {"ok": False, "error": "非法回收站编号"}
        batch = (trash / raw).resolve()
        try:
            batch.relative_to(trash.resolve())
        except ValueError:
            return {"ok": False, "error": "拒绝路径"}
        if not batch.is_dir():
            return {"ok": False, "error": "回收站条目不存在"}

        meta = None
        meta_file = batch / "meta.json"
        if meta_file.is_file():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                meta = None
        if meta and meta.get("kind") == "assets":
            assets = list(meta.get("assets") or [])
            sidecars = list(meta.get("sidecars") or [])
            if not assets:
                return {"ok": False, "error": "回收站条目没有图片"}
            for asset in assets + sidecars:
                target = (root / asset).resolve()
                try:
                    target.relative_to(root)
                except ValueError:
                    return {"ok": False, "error": "拒绝资源路径"}
                if target.exists():
                    return {"ok": False, "error": f"图片已存在，未覆盖: {asset}"}
                if not (batch / "assets" / asset).is_file():
                    return {"ok": False, "error": f"回收站中的图片不存在: {asset}"}
            try:
                for asset in assets + sidecars:
                    target = root / asset
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(batch / "assets" / asset), str(target))
                shutil.rmtree(batch, ignore_errors=True)
            except OSError as e:
                return {"ok": False, "error": f"恢复失败: {e}"}
            self._add_assets_to_index(root, assets)
            self._notify_change("build_complete", {"directory": str(root)})
            return {"ok": True, "asset_count": len(assets)}
        if meta and meta.get("source_rel"):
            source_rel = str(meta["source_rel"])
            assets = list(meta.get("assets") or [])
            sidecars = list(meta.get("sidecars") or [])
            src = batch / "source" / source_rel
        else:
            candidates = [
                p for p in batch.rglob("*")
                if p.is_file() and p.suffix.lower() in (".md", ".rst", ".txt")
            ]
            if not candidates:
                return {"ok": False, "error": "回收站条目没有源文件"}
            src = candidates[0]
            source_rel = src.relative_to(batch).as_posix()
            assets = []
            sidecars = []
            meta = {"kind": "legacy", "source_rel": source_rel, "assets": []}

        dst = (root / source_rel).resolve()
        try:
            dst.relative_to(root)
        except ValueError:
            return {"ok": False, "error": "拒绝恢复路径"}
        if dst.exists():
            return {"ok": False, "error": "目标文件已存在，未覆盖"}
        for asset in assets + sidecars:
            target = (root / asset).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                return {"ok": False, "error": "拒绝资源路径"}
            if target.exists():
                return {"ok": False, "error": f"图片已存在，未覆盖: {asset}"}
        if not src.is_file():
            return {"ok": False, "error": "回收站中的源文件不存在"}
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            for asset in assets + sidecars:
                asset_src = batch / "assets" / asset
                if asset_src.is_file():
                    target = root / asset
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(asset_src), str(target))
            shutil.rmtree(batch, ignore_errors=True)
        except OSError as e:
            return {"ok": False, "error": f"恢复失败: {e}"}
        try:
            from notemanager.builder import update_file

            update_file(root, dst, refresh_static_assets=False, refresh_project_assets=True)
        except Exception as e:
            logger.exception("恢复笔记后重建失败")
            return {"ok": False, "error": f"恢复后重建失败: {e}"}
        self._notify_change("build_complete", {"directory": str(root)})
        return {"ok": True, "source_rel_path": source_rel}

    def _asset_filename(self, root_static: Path, filename: str) -> str:
        original = unquote(str(filename or "")).strip().replace("\\", "/")
        original = Path(original).name
        ext = Path(original).suffix.lower()
        if ext in _IMPORT_BLOCKED_EXTS:
            raise ValueError(f"不允许导入该类型文件: {ext}")
        safe_ext = ext if re.fullmatch(r"\.[A-Za-z0-9]{1,10}", ext or "") else ""
        stem = re.sub(
            r"[^0-9A-Za-z_.\-\u4e00-\u9fff]+", "-", Path(original).stem
        ).strip("-_. ") or "resource"
        stem = stem[:80]
        candidate = stem + safe_ext
        if (root_static / candidate).exists():
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            candidate = f"{stem}-{stamp}-{secrets.token_hex(3)}{safe_ext}"
        return candidate

    def save_annotation(self, project_idx: int, image_rel_posix: str, svg_text: str) -> dict:
        """Save a transparent SVG annotation layer next to an image."""
        text = str(svg_text or "")
        error = _annotation_svg_error(text)
        if error:
            return {"ok": False, "error": error}

        with self._lock:
            dirs = list(self._observers.keys())
        if not (0 <= project_idx < len(dirs)):
            return {"ok": False, "error": "无效工程编号"}
        root = Path(dirs[project_idx]).resolve()
        rel = Path(str(image_rel_posix or "").replace("\\", "/"))
        if rel.is_absolute() or any(part == ".." for part in rel.parts):
            return {"ok": False, "error": "非法图片路径"}
        if rel.suffix.lower() not in _IMPORT_IMAGE_EXTS:
            return {"ok": False, "error": "只支持图片文件"}
        image_abs = (root / rel).resolve()
        try:
            image_abs.relative_to(root)
        except ValueError:
            return {"ok": False, "error": "拒绝路径"}
        if not image_abs.is_file():
            return {"ok": False, "error": "图片不存在"}
        sidecar = image_abs.with_name(image_abs.name + ".ann.svg")
        try:
            suppress_path(sidecar)
            sidecar.write_text(text, encoding="utf-8")
        except OSError as e:
            return {"ok": False, "error": str(e)}
        html_dir = root / ".NoteManager" / "html"
        if html_dir.is_dir():
            try:
                from notemanager.builder import _copy_project_assets

                _copy_project_assets(root, html_dir)
            except Exception as ex:
                logger.warning("保存标注后同步 html 资源目录失败: %s", ex)
        return {
            "ok": True,
            "annotation_rel_path": sidecar.relative_to(root).as_posix(),
            "image_rel_path": rel.as_posix(),
        }

    def set_image_width(
        self, project_idx: int, source_rel_posix: str, image_rel_posix: str,
        width_px: int, occurrence: int = 0,
    ) -> dict:
        """Update only the image width in the source file, preserving all other text."""
        import os as _os
        import re as _re
        from urllib.parse import unquote as _unquote

        try:
            width = int(width_px)
        except (TypeError, ValueError):
            return {"ok": False, "error": "无效宽度"}
        width = max(40, min(4000, width))
        with self._lock:
            dirs = list(self._observers.keys())
        if not (0 <= project_idx < len(dirs)):
            return {"ok": False, "error": "无效工程编号"}
        root, rel = self._validated_source_rel(Path(dirs[project_idx]), source_rel_posix)
        if rel is None:
            return {"ok": False, "error": "非法源文件路径"}
        suffix = rel.suffix.lower()
        if suffix not in (".md", ".rst"):
            return {"ok": False, "error": "只有 Markdown/reST 支持图片宽度"}
        source_path = (root / rel).resolve()
        image_rel = Path(str(image_rel_posix or "").replace("\\", "/"))
        if image_rel.is_absolute() or any(p == ".." for p in image_rel.parts):
            return {"ok": False, "error": "非法图片路径"}
        image_abs = (root / image_rel).resolve()
        try:
            image_abs.relative_to(root)
        except ValueError:
            return {"ok": False, "error": "拒绝路径"}
        if not image_abs.is_file():
            return {"ok": False, "error": "图片不存在"}
        try:
            with source_path.open("r", encoding="utf-8", newline="") as f:
                text = f.read()
        except OSError as e:
            return {"ok": False, "error": f"读取源码失败: {e}"}
        target_rel = image_rel.as_posix()

        def resolve_ref(raw: str) -> str | None:
            raw = _unquote(str(raw or "")).strip().strip("<>\"'")
            if not raw or raw.startswith(("http://", "https://", "data:", "#")):
                return None
            p = Path(raw)
            if p.is_absolute():
                return None
            try:
                resolved = (source_path.parent / p).resolve()
                return resolved.relative_to(root).as_posix()
            except (ValueError, OSError):
                return None

        changed = False
        if suffix == ".md":
            pattern = _re.compile(
                r'!\[[^\]]*\]\(\s*<?([^)\s>]+)(?:\s+["\'][^"\']*["\'])?\s*\)(\{[^}\n]*\})?'
            )
            matches = list(pattern.finditer(text))
            target_matches = [
                m for m in matches if resolve_ref(m.group(1)) == target_rel
            ]
            idx = max(0, int(occurrence or 0))
            if idx >= len(target_matches):
                return {"ok": False, "error": "没有找到对应的图片引用"}
            match = target_matches[idx]
            attrs = match.group(2)
            if attrs:
                body = attrs[1:-1].strip()
                body = _re.sub(r'(^|\s)width\s*=\s*\S+', r'\1', body).strip()
                if body:
                    body += " "
                body += f"width={width}px"
                new_attrs = "{" + body + "}"
                start = match.start(2)
                end = match.end(2)
            else:
                new_attrs = f"{{width={width}px}}"
                start = end = match.end()
            text = text[:start] + new_attrs + text[end:]
            changed = True
        else:
            lines = text.splitlines(keepends=True)
            directive = _re.compile(
                r'^([ \t]*)\.\.\s+(?:image|figure)::\s*(\S+)\s*$'
            )
            target_indices = []
            for i, line in enumerate(lines):
                m = directive.match(line.rstrip("\r\n"))
                if m and resolve_ref(m.group(2)) == target_rel:
                    target_indices.append((i, m.group(1)))
            idx = max(0, int(occurrence or 0))
            if idx >= len(target_indices):
                return {"ok": False, "error": "没有找到对应的图片引用"}
            i, indent = target_indices[idx]
            m = directive.match(lines[i].rstrip("\r\n"))
            option_re = _re.compile(r'^(\s*):width:\s*.*$')
            j = i + 1
            found = None
            while j < len(lines):
                raw_line = lines[j]
                stripped = raw_line.strip()
                if not stripped:
                    break
                leading = len(raw_line) - len(raw_line.lstrip(" \t"))
                if leading <= len(indent):
                    break
                if option_re.match(raw_line.rstrip("\r\n")):
                    found = j
                    break
                j += 1
            if found is not None:
                nl = "\r\n" if lines[found].endswith("\r\n") else ("\n" if lines[found].endswith("\n") else "")
                lines[found] = option_re.sub(
                    lambda om: om.group(1) + f":width: {width}px", lines[found].rstrip("\r\n")
                ) + nl
            else:
                nl = "\r\n" if lines[i].endswith("\r\n") else ("\n" if lines[i].endswith("\n") else "\n")
                lines.insert(i + 1, indent + f"   :width: {width}px{nl}")
            text = "".join(lines)
            changed = True
        if not changed:
            return {"ok": False, "error": "没有修改"}
        tmp = source_path.with_name(source_path.name + ".nmr-width.tmp")
        try:
            suppress_path(tmp)
            with tmp.open("w", encoding="utf-8", newline="") as f:
                f.write(text)
            _os.replace(tmp, source_path)
        except OSError as e:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return {"ok": False, "error": f"写入源码失败: {e}"}
        try:
            from notemanager.builder import update_file

            update_file(
                root, source_path,
                refresh_static_assets=False,
                refresh_project_assets=False,
            )
        except Exception as e:
            logger.exception("更新图片宽度后重建失败")
            return {"ok": False, "error": f"已修改源码，但重建失败: {e}"}
        return {"ok": True, "width": width, "source_rel_path": rel.as_posix()}

    def export_annotated_svg(
        self, project_idx: int, image_rel_posix: str, svg_text: str,
        delete_originals: bool = False,
    ) -> dict:
        """Write a self-contained SVG (image + annotations) for sharing."""
        import base64
        import xml.etree.ElementTree as ET
        from xml.sax.saxutils import escape as xml_escape

        text = str(svg_text or "")
        error = _annotation_svg_error(text)
        if error:
            return {"ok": False, "error": error}
        with self._lock:
            dirs = list(self._observers.keys())
        if not (0 <= project_idx < len(dirs)):
            return {"ok": False, "error": "无效工程编号"}
        root = Path(dirs[project_idx]).resolve()
        rel = Path(str(image_rel_posix or "").replace("\\", "/"))
        if rel.is_absolute() or any(part == ".." for part in rel.parts):
            return {"ok": False, "error": "非法图片路径"}
        if rel.suffix.lower() not in _IMPORT_IMAGE_EXTS:
            return {"ok": False, "error": "只支持图片文件"}
        image_abs = (root / rel).resolve()
        try:
            image_abs.relative_to(root)
        except ValueError:
            return {"ok": False, "error": "拒绝路径"}
        if not image_abs.is_file():
            return {"ok": False, "error": "图片不存在"}

        try:
            ann_root = ET.fromstring(text)
        except ET.ParseError as e:
            return {"ok": False, "error": f"标注 SVG 解析失败: {e}"}
        view_box = (ann_root.get("viewBox") or "").strip()
        parts = view_box.replace(",", " ").split()
        if len(parts) != 4:
            return {"ok": False, "error": "标注 SVG 缺少 viewBox"}
        try:
            width = float(parts[2])
            height = float(parts[3])
        except ValueError:
            return {"ok": False, "error": "标注 SVG 的 viewBox 无效"}
        if width <= 0 or height <= 0:
            return {"ok": False, "error": "标注 SVG 的尺寸无效"}
        mime = mimetypes.guess_type(image_abs.name)[0] or "application/octet-stream"
        if not mime.startswith("image/"):
            return {"ok": False, "error": "无法识别图片类型"}
        try:
            data = base64.b64encode(image_abs.read_bytes()).decode("ascii")
        except OSError as e:
            return {"ok": False, "error": f"读取图片失败: {e}"}

        ET.register_namespace("", "http://www.w3.org/2000/svg")
        inner = "".join(
            ET.tostring(child, encoding="unicode") for child in ann_root
        )
        out = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="{xml_escape(view_box)}" width="{width:g}" height="{height:g}">\n'
            f'<image x="0" y="0" width="{width:g}" height="{height:g}" '
            'preserveAspectRatio="none" '
            f'href="data:{mime};base64,{data}"/>\n'
            f'<g>{inner}</g>\n</svg>\n'
        )
        out_abs = image_abs.with_name(image_abs.name + ".annotated.svg")
        try:
            suppress_path(out_abs)
            out_abs.write_text(out, encoding="utf-8")
        except OSError as e:
            return {"ok": False, "error": f"写入 SVG 失败: {e}"}
        html_dir = root / ".NoteManager" / "html"
        out_rel = out_abs.relative_to(root).as_posix()
        if html_dir.is_dir():
            try:
                from notemanager.builder import _copy_project_assets

                _copy_project_assets(root, html_dir)
                self._add_assets_to_index(root, [out_rel])
            except Exception as ex:
                logger.warning("导出标注 SVG 后同步资源失败: %s", ex)

        deleted = []
        if delete_originals:
            originals = [rel.as_posix(), rel.as_posix() + ".ann.svg"]
            for original in originals:
                src = (root / original).resolve()
                try:
                    src.relative_to(root)
                except ValueError:
                    continue
                if not src.is_file():
                    continue
                try:
                    suppress_path(src)
                    src.unlink()
                    generated = (html_dir / original).resolve()
                    generated.relative_to(html_dir.resolve())
                    if generated.is_file():
                        generated.unlink()
                    deleted.append(original)
                except (OSError, ValueError) as e:
                    return {
                        "ok": False,
                        "error": f"SVG 已导出，但删除 {original} 失败: {e}",
                        "path": out_rel,
                    }
            self._remove_assets_from_index(root, deleted)
        return {
            "ok": True,
            "path": out_rel,
            "markdown": f"![]({out_rel})",
            "rst": f".. image:: {out_rel}",
            "deleted_originals": deleted,
        }

    def save_imported_asset(
        self,
        project_idx: int,
        source_rel_posix: str,
        filename: str,
        data: bytes,
        content_type: str = "",
    ) -> dict:
        """Import a local file into the note directory's _static/ and return insert syntax."""
        if len(data) > _IMPORT_ASSET_MAX_BYTES:
            return {"ok": False, "error": "资源过大"}
        with self._lock:
            dirs = list(self._observers.keys())
        if not (0 <= project_idx < len(dirs)):
            return {"ok": False, "error": "无效工程编号"}
        root, rel = self._validated_source_rel(Path(dirs[project_idx]), source_rel_posix)
        if rel is None:
            return {"ok": False, "error": "非法源文件路径"}
        root_r = root.resolve()
        dest_dir = (root / rel.parent / "_static").resolve()
        try:
            dest_dir.relative_to(root_r)
        except ValueError:
            return {"ok": False, "error": "非法保存目录"}
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            name = self._asset_filename(dest_dir, filename)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if not Path(name).suffix and content_type:
            guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
            if guessed:
                name += guessed
        if Path(name).suffix.lower() in _IMPORT_BLOCKED_EXTS:
            return {"ok": False, "error": f"不允许导入该类型文件: {Path(name).suffix.lower()}"}
        if (dest_dir / name).exists():
            stem = Path(name).stem
            suffix = Path(name).suffix
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            name = f"{stem}-{stamp}-{secrets.token_hex(3)}{suffix}"
        out = (dest_dir / name).resolve()
        try:
            out.relative_to(root_r)
        except ValueError:
            return {"ok": False, "error": "拒绝路径"}
        try:
            out.write_bytes(data)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        src_dir = (root / rel.parent).resolve()
        rel_insert = Path(os.path.relpath(out, src_dir)).as_posix()
        is_image = out.suffix.lower() in _IMPORT_IMAGE_EXTS
        if is_image:
            insert_markdown = f"![]({rel_insert})"
            insert_rst = f".. image:: {rel_insert}"
        else:
            insert_markdown = f"[{name}]({rel_insert})"
            insert_rst = f"`{name} <{rel_insert}>`_"
        html_dir = root / ".NoteManager" / "html"
        if html_dir.is_dir():
            try:
                from notemanager.builder import _copy_project_assets

                _copy_project_assets(root, html_dir)
            except Exception as ex:
                logger.warning("导入资源后同步 html 资源目录失败: %s", ex)
        return {
            "ok": True,
            "saved_rel_path": rel_insert,
            "filename": name,
            "insert_markdown": insert_markdown,
            "insert_rst": insert_rst,
        }

    def import_asset_from_url(
        self,
        project_idx: int,
        source_rel_posix: str,
        url: str,
    ) -> dict:
        parsed = urlparse(str(url or "").strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return {"ok": False, "error": "只支持 http/https URL"}
        try:
            req = Request(
                url,
                headers={"User-Agent": "NoteManager/import"},
            )
            with urlopen(req, timeout=20) as resp:
                data = resp.read(_IMPORT_ASSET_MAX_BYTES + 1)
                ctype = resp.headers.get("Content-Type", "")
                disposition = resp.headers.get("Content-Disposition", "")
                if len(data) > _IMPORT_ASSET_MAX_BYTES:
                    return {"ok": False, "error": "资源过大"}
        except Exception as e:
            return {"ok": False, "error": f"下载失败: {e}"}
        filename = ""
        if disposition:
            match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition, re.I)
            if match:
                filename = unquote(match.group(1).strip())
        if not filename:
            filename = Path(unquote(parsed.path)).name or "resource"
        return self.save_imported_asset(
            project_idx, source_rel_posix, filename, data, ctype
        )

    def save_paste_image(
        self,
        project_idx: int,
        source_rel_posix: str,
        data: bytes,
        raw_content_type: str,
    ) -> dict:
        """把粘贴的图片存到源文件目录的 _static，返回插入片段。"""
        ct = raw_content_type.split(";")[0].strip().lower()
        ext = _PASTE_IMAGE_TYPES.get(ct)
        if ext is None:
            return {"ok": False, "error": f"不支持的图片类型: {ct}"}
        if len(data) > _PASTE_IMAGE_MAX_BYTES:
            return {"ok": False, "error": "图片过大"}
        with self._lock:
            dirs = list(self._observers.keys())
        if not (0 <= project_idx < len(dirs)):
            return {"ok": False, "error": "无效工程编号"}
        root, rel = self._validated_source_rel(Path(dirs[project_idx]), source_rel_posix)
        if rel is None:
            return {"ok": False, "error": "非法源文件路径"}
        root_r = root.resolve()
        # Keep images next to the note inside its directory-level _static/.
        dest_dir = (root / rel.parent / "_static").resolve()
        try:
            dest_dir.relative_to(root_r)
        except ValueError:
            return {"ok": False, "error": "非法保存目录"}
        if dest_dir.exists() and not dest_dir.is_dir():
            return {"ok": False, "error": "_static 不是目录"}
        dest_dir.mkdir(parents=True, exist_ok=True)
        safe_stem = re.sub(
            r"[^0-9A-Za-z_.\-\u4e00-\u9fff]+", "-", rel.stem
        ).strip("-_. ") or "note"
        safe_stem = safe_stem[:60]
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        fn = f"nm-paste-{safe_stem}-{stamp}-{secrets.token_hex(4)}{ext}"
        out = (dest_dir / fn).resolve()
        try:
            out.relative_to(root_r)
        except ValueError:
            return {"ok": False, "error": "拒绝路径"}
        try:
            out.write_bytes(data)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        src_dir = (root / rel.parent).resolve()
        try:
            rel_insert = Path(os.path.relpath(out, src_dir)).as_posix()
        except ValueError:
            rel_insert = fn
        html_dir = root / ".NoteManager" / "html"
        if html_dir.is_dir():
            try:
                from notemanager.builder import _copy_project_assets

                _copy_project_assets(root, html_dir)
            except Exception as ex:
                logger.warning("粘贴图片后同步 html 资源目录失败: %s", ex)
        return {
            "ok": True,
            "insert_markdown": f"![]({rel_insert})",
            "insert_rst": f".. image:: {rel_insert}",
        }

    def _resolve_project_index(self, project, dirs):
        """Resolve a project identifier (index, name, or None) to an index."""
        if project is None or (isinstance(project, int) and project == 0):
            # Use default_project if set
            default = self.config.get("default_project")
            if default and default in dirs:
                return dirs.index(default)
            return 0
        try:
            idx = int(project)
            if 0 <= idx < len(dirs):
                return idx
            return None
        except (ValueError, TypeError):
            pass
        # Match by directory basename
        for i, d in enumerate(dirs):
            if Path(d).name == project:
                return i
        for i, d in enumerate(dirs):
            if project.lower() in Path(d).name.lower():
                return i
        return None

    def _notify_change(self, event_type: str, extra: dict | None = None):
        """Push an event to all connected web UI clients."""
        try:
            status = self.get_status()
            payload = dict(status.get("data", {}))
            if extra:
                payload.update(extra)
            notify_event(event_type, payload)
        except Exception as e:
            logger.error("通知事件失败: %s", e)

    def stop_daemon(self) -> dict:
        """Stop the daemon."""
        logger.info("收到停止命令")
        self._shutdown.set()
        return {"ok": True, "message": "正在停止..."}

    def handle_command(self, cmd: dict) -> dict:
        """Dispatch a command (for TCP/CLI)."""
        action = cmd.get("cmd")
        if action == "stop":
            return self.stop_daemon()
        if action == "status":
            return self.get_status()
        # Avoid empty _observers while config directories are still being restored.
        if not self._startup_complete_event.wait(timeout=600):
            return {"ok": False, "error": "Daemon 启动超时（恢复监控目录未完成）"}
        if action == "add":
            return self.add_directory(cmd["directory"])
        elif action == "add_with_progress":
            return self.handle_add_with_progress(cmd)
        elif action == "add_progress":
            return self.handle_add_progress(cmd)
        elif action == "remove":
            return self.remove_directory(cmd["directory"])
        elif action == "list":
            return self.list_directories()
        elif action == "search":
            return self.handle_search(cmd)
        elif action == "default":
            return self.handle_default(cmd)
        elif action == "main":
            return self.handle_main(cmd)
        elif action == "open_editor":
            return self.open_editor(cmd)
        elif action == "view":
            return self.handle_view(cmd)
        elif action == "rebuild":
            return self.handle_rebuild(cmd)
        elif action == "rebuild_progress":
            return self.handle_rebuild_progress(cmd)
        else:
            return {"ok": False, "error": f"未知命令: {action}"}

    def run(self) -> None:
        """Run the daemon main loop."""
        # Leave a trace in daemon.log if the process dies from a native crash
        # (e.g. in a C extension) - Python-level tracebacks would miss those.
        import faulthandler
        try:
            faulthandler.enable()
        except Exception:
            pass

        _write_daemon_info(
            os.getpid(), self.port, self.web_port, self.transport, self.socket_path,
        )

        # Start TCP server for CLI
        daemon_ref = self

        class TCPHandler(socketserver.BaseRequestHandler):
            def handle(self):
                try:
                    cmd = recv_command(self.request)
                    resp = daemon_ref.handle_command(cmd)
                    send_response(self.request, resp)
                except (ConnectionError, json.JSONDecodeError) as e:
                    logger.error("处理请求出错: %s", e)

        class QuietTCPServer(socketserver.ThreadingTCPServer):
            allow_reuse_address = True

            def handle_error(self, request, client_address):
                exc = sys.exc_info()[1]
                if isinstance(exc, (ConnectionError, TimeoutError)):
                    logger.debug("CLI 客户端 %s 断开: %r", client_address, exc)
                    return
                logger.exception("处理来自 %s 的 CLI 请求时出错", client_address)

        if self.transport == "unix":
            class ReuseUnixServer(socketserver.ThreadingUnixStreamServer):
                daemon_threads = True

                def handle_error(self, request, client_address):
                    exc = sys.exc_info()[1]
                    if isinstance(exc, (ConnectionError, TimeoutError)):
                        logger.debug("CLI 客户端 %s 断开: %r", client_address, exc)
                        return
                    logger.exception("处理来自 %s 的 CLI 请求时出错", client_address)

            sock_path = Path(self.socket_path)
            sock_path.parent.mkdir(parents=True, exist_ok=True)
            if sock_path.exists():
                sock_path.unlink()
            self._tcp_server = ReuseUnixServer(self.socket_path, TCPHandler)
        else:
            self._tcp_server = QuietTCPServer(
                (DEFAULT_HOST, self.port), TCPHandler,
            )
        self._tcp_server.daemon_threads = True

        def _tcp_serve_with_guard() -> None:
            try:
                self._tcp_server.serve_forever()
            except Exception:
                logger.exception("CLI server 线程异常退出")
                raise

        tcp_thread = threading.Thread(target=_tcp_serve_with_guard)
        tcp_thread.daemon = True
        tcp_thread.start()

        if self.transport == "unix":
            logger.info("Unix Socket 已启动 %s", self.socket_path)
        else:
            logger.info("TCP 已启动 %s:%d", DEFAULT_HOST, self.port)

        # Start HTTP server for web UI
        from notemanager.web import start_web_server
        self._web_server = start_web_server(daemon_ref, self.web_port)
        logger.info("Web UI 已启动 http://localhost:%d", self.web_port)

        # Restore previously monitored directories
        for directory in list(self.config["directories"]):
            if Path(directory).is_dir():
                logger.info("恢复监控: %s", directory)
                try:
                    if _need_rebuild_on_startup(directory):
                        logger.info("检测到变更，执行重建: %s", directory)
                        full_build(directory)
                    else:
                        logger.info("未检测到变更，跳过重建: %s", directory)
                    observer = start_watcher(directory)
                    observer.start()
                    with self._lock:
                        self._observers[directory] = observer
                except Exception as e:
                    logger.error("恢复监控失败: %s: %s", directory, e)
            else:
                logger.warning("目录不存在，移除: %s", directory)
                self.config["directories"].remove(directory)
                _mutate_config_file(lambda cfg: _cfg_remove_directory(cfg, directory))

        self._startup_complete_event.set()
        logger.info("Daemon 就绪")

        # Wait for shutdown signal
        self._shutdown.wait()

        # Cleanup
        logger.info("正在关闭...")
        self._tcp_server.shutdown()
        self._tcp_server.server_close()
        self._web_server.shutdown()
        # shutdown() stops serve_forever() but does not close the listening
        # socket.  Without server_close() the old process can still hold the
        # Web port while the parent starts a replacement daemon.
        self._web_server.server_close()
        with self._lock:
            for observer in self._observers.values():
                observer.stop()
            for observer in self._observers.values():
                observer.join(timeout=5)
        _remove_daemon_info()
        logger.info("Daemon 已停止")

    def run_foreground(self) -> None:
        """Run daemon in foreground (for debugging)."""
        stream = sys.stdout
        if stream is None:
            # pythonw.exe has no stdio; log to the normal daemon log instead.
            try:
                HOME_NM_DIR.mkdir(parents=True, exist_ok=True)
                stream = open(HOME_NM_DIR / "daemon.log", "a", encoding="utf-8")
            except OSError:
                stream = sys.stderr
        self._setup_logging(stream=stream)
        self.run()

    @staticmethod
    def _windows_pythonw_executable() -> str:
        """Prefer pythonw.exe so the detached daemon has no console window."""
        exe = Path(sys.executable)
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.is_file():
            return str(pythonw)
        return str(exe)

    def run_background(self) -> bool:
        """Run daemon in background (POSIX fork or Windows detached process).

        Returns True once the child reports ``startup_complete`` (or False on timeout).
        """
        if hasattr(os, "fork"):
            pid = os.fork()
            if pid > 0:
                for step in range(_DAEMON_PARENT_STARTUP_MAX_STEPS):
                    time.sleep(_DAEMON_PARENT_STARTUP_POLL_S)
                    if _daemon_responded_startup_complete():
                        info = {}
                        if DAEMON_FILE.exists():
                            try:
                                info = json.loads(
                                    DAEMON_FILE.read_text(encoding="utf-8")
                                )
                            except (json.JSONDecodeError, OSError):
                                pass
                        real_pid = info.get("pid", pid)
                        print(f"Daemon 已启动 (PID: {real_pid})")
                        print(f"Web UI: http://localhost:{self.web_port}")
                        return True
                    # A child that crashes before writing daemon.json (or is
                    # removed after its PID dies) must fail fast instead of
                    # leaving the CLI blocked for the full startup timeout.
                    if (
                        step >= _DAEMON_STARTUP_FAIL_GRACE_STEPS
                        and not DAEMON_FILE.exists()
                    ):
                        print(
                            "Daemon 启动失败：后台进程已退出，请查看 "
                            f"{HOME_NM_DIR / 'daemon.log'} 末尾的 traceback。",
                            file=sys.stderr,
                        )
                        return False
                    if step > 0 and step % 150 == 0:
                        print(
                            "仍在等待 daemon 完成首次构建与恢复监控…",
                            file=sys.stderr,
                        )
                print("Daemon 启动失败（等待超时，可能仍在后台构建，请稍后 nmr status）", file=sys.stderr)
                return False

            os.setsid()

            pid = os.fork()
            if pid > 0:
                os._exit(0)

            # Detach stdio at the OS level: merely rebinding sys.stdout leaves the
            # inherited pipe/tty fds open, so callers piping CLI output (e.g.
            # `nmr view f.md | tail`) would hang until the daemon exits.
            HOME_NM_DIR.mkdir(parents=True, exist_ok=True)
            log_file = HOME_NM_DIR / "daemon.log"
            devnull_fd = os.open(os.devnull, os.O_RDONLY)
            os.dup2(devnull_fd, 0)
            if devnull_fd > 0:
                os.close(devnull_fd)
            sys.stdin = os.fdopen(0, "r", encoding="utf-8")
            log_fd = os.open(
                log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644
            )
            os.dup2(log_fd, 1)
            os.dup2(log_fd, 2)
            if log_fd > 2:
                os.close(log_fd)
            sys.stdout = os.fdopen(1, "a", encoding="utf-8", buffering=1)
            sys.stderr = os.fdopen(2, "a", encoding="utf-8", buffering=1)

            self._setup_logging(stream=sys.stderr)
            self.run()
            return

        # Windows: start a fully detached Python child process.
        HOME_NM_DIR.mkdir(parents=True, exist_ok=True)
        log_file = HOME_NM_DIR / "daemon.log"
        child_cmd = [
            self._windows_pythonw_executable(),
            "-c",
            (
                "from notemanager.daemon import NoteManagerDaemon; "
                f"NoteManagerDaemon(port={self.port}, web_port={self.web_port}).run_foreground()"
            ),
        ]
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )

        log_fp = open(log_file, "a", encoding="utf-8")
        try:
            breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
            try:
                proc = subprocess.Popen(
                    child_cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=log_fp,
                    stderr=log_fp,
                    creationflags=creationflags | breakaway,
                    close_fds=True,
                )
            except OSError:
                # Some Windows job objects forbid breakaway; still detach the
                # console and hide the child window.
                proc = subprocess.Popen(
                    child_cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=log_fp,
                    stderr=log_fp,
                    creationflags=creationflags,
                    close_fds=True,
                )
        finally:
            log_fp.close()

        # Restore may run full_build per project; wait until status reports ready.
        for step in range(_DAEMON_PARENT_STARTUP_MAX_STEPS):
            time.sleep(_DAEMON_PARENT_STARTUP_POLL_S)
            if _daemon_responded_startup_complete():
                print(f"Daemon 已启动 (PID: {proc.pid})")
                print(f"Web UI: http://localhost:{self.web_port}")
                return True
            if (
                step >= _DAEMON_STARTUP_FAIL_GRACE_STEPS
                and (not DAEMON_FILE.exists() or proc.poll() is not None)
            ):
                print(
                    "Daemon 启动失败：后台进程已退出，请查看 "
                    f"{HOME_NM_DIR / 'daemon.log'} 末尾的 traceback。",
                    file=sys.stderr,
                )
                return False
            if step > 0 and step % 150 == 0:
                print(
                    "仍在等待 daemon 完成首次构建与恢复监控…",
                    file=sys.stderr,
                )

        print("Daemon 启动失败（等待超时，可能仍在后台构建，请稍后 nmr status）", file=sys.stderr)
        return False

    @staticmethod
    def _setup_logging(stream=None) -> None:
        """Configure logging for the daemon."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            stream=stream,
        )
