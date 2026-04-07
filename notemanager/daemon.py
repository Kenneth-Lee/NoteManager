"""NoteManager daemon process.

Runs as a background process, listens on TCP for CLI commands
and on HTTP for web UI, manages watchers for all registered directories.
"""

import json
import logging
import os
import re
import secrets
import socketserver
import subprocess
import sys
import threading
import datetime
import hashlib
import time
from pathlib import Path
from urllib.parse import quote, urlencode

from notemanager.protocol import (
    DEFAULT_HOST, DEFAULT_PORT, DEFAULT_UNIX_SOCKET, recv_command, send_response,
    send_command,
    use_unix_socket,
)
from notemanager.builder import full_build
from notemanager.scan_manifest import MANIFEST_VERSION, live_payload_and_files, read_scan_manifest
from notemanager.watcher import start_watcher
from notemanager.watcher import suppress_path
from notemanager.web import notify_event
from notemanager.indexer import filter_search_documents
from notemanager.renderer.rst_renderer import render_rst
from notemanager.renderer.md_renderer import render_md
from notemanager.renderer.txt_renderer import render_txt
from notemanager.renderer.template import wrap_html
from notemanager.scanner import INCLUDE_EXTENSIONS, SKIP_DIRS

logger = logging.getLogger(__name__)

DEFAULT_WEB_PORT = 19833

_PASTE_IMAGE_MAX_BYTES = 8 * 1024 * 1024
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

# CLI `_ensure_daemon` 轮询（与父进程等待同量级，避免 daemon 已在跑但构建未结束）
DAEMON_ENSURE_WAIT_STEPS = 2400  # ×0.5s ≈ 20 分钟

# Global config directory
HOME_NM_DIR = Path.home() / ".NoteManager"
CONFIG_FILE = HOME_NM_DIR / "config.json"
DAEMON_FILE = HOME_NM_DIR / "daemon.json"


def _load_config() -> dict:
    """Load daemon config from ~/.NoteManager/config.json."""
    if not CONFIG_FILE.exists():
        return {"directories": [], "port": DEFAULT_PORT}
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def _save_config(config: dict) -> None:
    """Save daemon config."""
    HOME_NM_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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
    """Remove daemon runtime info on shutdown."""
    if DAEMON_FILE.exists():
        DAEMON_FILE.unlink()
    if use_unix_socket():
        try:
            DEFAULT_UNIX_SOCKET.unlink(missing_ok=True)
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
    """Check if a process with given PID exists."""
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
        try:
            root_stat = root.stat()
            r_ns = getattr(root_stat, "st_mtime_ns", int(root_stat.st_mtime * 1_000_000_000))
            root_anchor = f"{r_ns}:{root_stat.st_ino}"
            if (
                root_anchor == saved.get("root_anchor")
                and int(saved.get("file_count", -1)) == built_file_count
            ):
                return False
        except (OSError, ValueError, TypeError):
            pass

    try:
        live, files = live_payload_and_files(root)
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
    if not files:
        return False

    return any(datetime.datetime.fromtimestamp(f.mtime) > last_build for f in files)


class NoteManagerDaemon:
    """The main daemon that manages all project watchers."""

    def __init__(self, port: int = DEFAULT_PORT, web_port: int = DEFAULT_WEB_PORT):
        self.port = port
        self.web_port = web_port
        self.transport = "unix" if use_unix_socket() else "tcp"
        self.socket_path = str(DEFAULT_UNIX_SOCKET) if use_unix_socket() else ""
        self.config = _load_config()
        self.config["port"] = port
        self.config["transport"] = self.transport
        if self.socket_path:
            self.config["socket_path"] = self.socket_path
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

    def add_directory(self, directory: str) -> dict:
        """Add a directory to monitoring."""
        path = Path(directory).resolve()
        if not path.is_dir():
            return {"ok": False, "error": f"目录不存在: {path}"}

        dir_str = str(path)

        with self._lock:
            if dir_str in self._observers:
                return {"ok": False, "error": f"已在监控中: {path}"}

        logger.info("构建目录: %s", path)
        try:
            full_build(path)
        except Exception as e:
            logger.error("构建失败: %s: %s", path, e)
            return {"ok": False, "error": f"构建失败: {e}"}

        try:
            observer = start_watcher(path)
            observer.start()
            with self._lock:
                self._observers[dir_str] = observer
        except Exception as e:
            logger.error("启动监控失败: %s: %s", path, e)
            return {"ok": False, "error": f"启动监控失败: {e}"}

        with self._lock:
            if dir_str not in self.config["directories"]:
                self.config["directories"].append(dir_str)
                _save_config(self.config)

        logger.info("已添加监控: %s", path)
        self._notify_change("directories_changed")
        return {"ok": True, "message": f"已添加监控: {path}"}

    def remove_directory(self, directory: str) -> dict:
        """Remove a directory from monitoring."""
        path = str(Path(directory).resolve())

        with self._lock:
            observer = self._observers.pop(path, None)
            if path in self.config["directories"]:
                self.config["directories"].remove(path)
                _save_config(self.config)

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
        return {
            "ok": True,
            "data": {
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
            },
        }

    def handle_search(self, cmd: dict) -> dict:
        """Handle CLI 'search' command: broadcast search query to web UI."""
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
        notify_event("cli_search", payload)
        return {"ok": True, "message": f"已发送搜索: {query}"}

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
        _save_config(self.config)
        self._notify_change("default_changed")
        return {"ok": True, "message": f"已设置默认工程: [{idx}] {Path(dirs[idx]).name}"}

    def handle_view(self, cmd: dict) -> dict:
        """Handle CLI 'view' command: open a source file in web UI."""
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

        # If file belongs to a managed project, jump to that project HTML directly.
        for idx, d in enumerate(dirs):
            root = Path(d)
            try:
                rel = source.relative_to(root)
            except ValueError:
                continue
            target = f"/p/{idx}/{rel.with_suffix('.html').as_posix()}"
            notify_event("cli_view", {"url": target})
            return {"ok": True, "message": f"已跳转: {target}"}

        # Otherwise render a temporary single-file page.
        temp_url = self._render_temp_view(source)
        notify_event("cli_view", {"url": temp_url})
        return {"ok": True, "message": f"已临时渲染: {temp_url}"}

    def get_temp_view_dir(self) -> Path:
        """Directory for temporary rendered single-file pages."""
        temp_dir = HOME_NM_DIR / "temp_views"
        temp_dir.mkdir(parents=True, exist_ok=True)
        return temp_dir

    def _render_temp_view(self, source: Path) -> str:
        """Render one unmanaged source file to temporary HTML."""
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
        )

        temp_dir = self.get_temp_view_dir()
        static_dir = temp_dir / "_static"
        static_dir.mkdir(parents=True, exist_ok=True)
        from notemanager.builder import _copy_static_assets  # local import
        _copy_static_assets(static_dir)

        key = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:16]
        out_name = f"{key}.html"
        (temp_dir / out_name).write_text(html, encoding="utf-8")
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
        parent = rel.parent
        new_rel = (parent / out_name).as_posix()
        try:
            new_abs = (root / Path(new_rel)).resolve()
        except OSError as e:
            return {"ok": False, "error": str(e)}
        root_r = root.resolve()
        if not str(new_abs).startswith(str(root_r)):
            return {"ok": False, "error": "拒绝路径"}
        try:
            new_abs.relative_to(root_r)
        except ValueError:
            return {"ok": False, "error": "拒绝路径"}
        if new_abs.exists():
            return {"ok": False, "error": "文件已存在"}
        new_abs.parent.mkdir(parents=True, exist_ok=True)
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
        self._notify_change("build_complete", {"directory": str(root_r)})
        return {"ok": True, "source_rel_path": new_rel, "html_rel_path": html_rel}

    def delete_managed_source(self, project_idx: int, source_rel_posix: str) -> dict:
        """删除当前监控工程内的源文件并更新索引与 HTML。"""
        with self._lock:
            dirs = list(self._observers.keys())
        if not (0 <= project_idx < len(dirs)):
            return {"ok": False, "error": "无效工程编号"}
        root, rel = self._validated_source_rel(Path(dirs[project_idx]), source_rel_posix)
        if rel is None:
            return {"ok": False, "error": "非法源文件路径"}
        abs_src = (root / rel).resolve()
        root_r = root.resolve()
        if not str(abs_src).startswith(str(root_r)):
            return {"ok": False, "error": "拒绝路径"}
        if not abs_src.is_file():
            return {"ok": False, "error": "文件不存在"}
        try:
            suppress_path(abs_src)
            abs_src.unlink()
        except OSError as e:
            return {"ok": False, "error": str(e)}
        try:
            from notemanager.builder import remove_file

            remove_file(root, abs_src)
        except Exception as e:
            logger.exception("删除笔记后更新索引失败")
            return {"ok": False, "error": f"更新索引失败: {e}"}
        self._notify_change("build_complete", {"directory": str(root_r)})
        return {"ok": True, "redirect_html": "index.html"}

    def save_paste_image(
        self,
        project_idx: int,
        source_rel_posix: str,
        data: bytes,
        raw_content_type: str,
    ) -> dict:
        """把粘贴的图片存到源文件同目录，返回插入片段。"""
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
        dest_dir = (root / rel.parent).resolve()
        root_r = root.resolve()
        if not str(dest_dir).startswith(str(root_r)):
            return {"ok": False, "error": "非法保存目录"}
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        fn = f"nm-paste-{stamp}-{secrets.token_hex(4)}{ext}"
        out = (dest_dir / fn).resolve()
        if not str(out).startswith(str(root_r)):
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
        elif action == "remove":
            return self.remove_directory(cmd["directory"])
        elif action == "list":
            return self.list_directories()
        elif action == "search":
            return self.handle_search(cmd)
        elif action == "default":
            return self.handle_default(cmd)
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

        if use_unix_socket():
            class ReuseUnixServer(socketserver.ThreadingUnixStreamServer):
                daemon_threads = True

            sock_path = Path(self.socket_path)
            sock_path.parent.mkdir(parents=True, exist_ok=True)
            if sock_path.exists():
                sock_path.unlink()
            self._tcp_server = ReuseUnixServer(self.socket_path, TCPHandler)
        else:
            class ReuseAddrTCPServer(socketserver.ThreadingTCPServer):
                allow_reuse_address = True

            self._tcp_server = ReuseAddrTCPServer(
                (DEFAULT_HOST, self.port), TCPHandler,
            )
        self._tcp_server.daemon_threads = True
        tcp_thread = threading.Thread(target=self._tcp_server.serve_forever)
        tcp_thread.daemon = True
        tcp_thread.start()

        if use_unix_socket():
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
                _save_config(self.config)

        self._startup_complete_event.set()
        logger.info("Daemon 就绪")

        # Wait for shutdown signal
        self._shutdown.wait()

        # Cleanup
        logger.info("正在关闭...")
        self._tcp_server.shutdown()
        self._tcp_server.server_close()
        self._web_server.shutdown()
        with self._lock:
            for observer in self._observers.values():
                observer.stop()
            for observer in self._observers.values():
                observer.join(timeout=5)
        _remove_daemon_info()
        logger.info("Daemon 已停止")

    def run_foreground(self) -> None:
        """Run daemon in foreground (for debugging)."""
        self._setup_logging(stream=sys.stdout)
        self.run()

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

            sys.stdin.close()
            HOME_NM_DIR.mkdir(parents=True, exist_ok=True)
            log_file = HOME_NM_DIR / "daemon.log"
            sys.stdout = open(log_file, "a", encoding="utf-8")
            sys.stderr = open(log_file, "a", encoding="utf-8")

            self._setup_logging(stream=sys.stderr)
            self.run()
            return

        # Windows: start a fully detached Python child process.
        HOME_NM_DIR.mkdir(parents=True, exist_ok=True)
        log_file = HOME_NM_DIR / "daemon.log"
        child_cmd = [
            sys.executable,
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
