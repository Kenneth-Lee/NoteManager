"""NoteManager daemon process.

Runs as a background process, listens on TCP for CLI commands
and on HTTP for web UI, manages watchers for all registered directories.
"""

import json
import logging
import os
import socketserver
import subprocess
import sys
import threading
import datetime
import hashlib
import time
from pathlib import Path

from notemanager.protocol import (
    DEFAULT_HOST, DEFAULT_PORT, DEFAULT_UNIX_SOCKET, recv_command, send_response,
    send_command,
    use_unix_socket,
)
from notemanager.builder import full_build
from notemanager.scan_manifest import MANIFEST_VERSION, live_payload_and_files, read_scan_manifest
from notemanager.watcher import start_watcher
from notemanager.web import notify_event
from notemanager.renderer.rst_renderer import render_rst
from notemanager.renderer.md_renderer import render_md
from notemanager.renderer.template import wrap_html

logger = logging.getLogger(__name__)

DEFAULT_WEB_PORT = 19833

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

        notify_event("cli_search", {"query": query, "project": idx})
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
        if source.suffix.lower() not in (".md", ".rst"):
            return {"ok": False, "error": "仅支持 .md/.rst 文件"}

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
        else:
            rendered = render_md(content, str(source))

        title = source.stem
        body = rendered.get("body", "")
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
