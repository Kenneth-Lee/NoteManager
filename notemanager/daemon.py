"""NoteManager daemon process.

Runs as a background process, listens on TCP for CLI commands
and on HTTP for web UI, manages watchers for all registered directories.
"""

import json
import logging
import os
import socketserver
import sys
import threading
import datetime
import hashlib
from pathlib import Path

from notemanager.protocol import DEFAULT_HOST, DEFAULT_PORT, recv_command, send_response
from notemanager.builder import full_build
from notemanager.watcher import start_watcher
from notemanager.web import notify_event
from notemanager.renderer.rst_renderer import render_rst
from notemanager.renderer.md_renderer import render_md
from notemanager.renderer.template import wrap_html

logger = logging.getLogger(__name__)

DEFAULT_WEB_PORT = 19833

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


def _write_daemon_info(pid: int, port: int, web_port: int) -> None:
    """Write daemon runtime info."""
    HOME_NM_DIR.mkdir(parents=True, exist_ok=True)
    DAEMON_FILE.write_text(
        json.dumps({
            "pid": pid,
            "port": port,
            "web_port": web_port,
            "started": datetime.datetime.now().isoformat(),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _remove_daemon_info() -> None:
    """Remove daemon runtime info on shutdown."""
    if DAEMON_FILE.exists():
        DAEMON_FILE.unlink()


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


class NoteManagerDaemon:
    """The main daemon that manages all project watchers."""

    def __init__(self, port: int = DEFAULT_PORT, web_port: int = DEFAULT_WEB_PORT):
        self.port = port
        self.web_port = web_port
        self.config = _load_config()
        self.config["port"] = port
        self._observers = {}  # directory -> Observer
        self._lock = threading.Lock()
        self._shutdown = threading.Event()
        self._tcp_server = None
        self._web_server = None

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
            self._notify_change("build_complete")
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
                "web_port": self.web_port,
                "directories": dirs,
                "directory_count": len(dirs),
                "details": status_info,
                "default_project": self.config.get("default_project"),
            },
        }

    def handle_search(self, cmd: dict) -> dict:
        """Handle CLI 'search' command: broadcast search query to web UI."""
        query = cmd.get("query", "")
        if not query:
            return {"ok": False, "error": "需要搜索关键词"}

        project = cmd.get("project")
        with self._lock:
            dirs = list(self._observers.keys())

        if not dirs:
            return {"ok": False, "error": "没有监控中的目录"}

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

    def _notify_change(self, event_type: str):
        """Push an event to all connected web UI clients."""
        try:
            status = self.get_status()
            notify_event(event_type, status.get("data", {}))
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
        if action == "add":
            return self.add_directory(cmd["directory"])
        elif action == "remove":
            return self.remove_directory(cmd["directory"])
        elif action == "list":
            return self.list_directories()
        elif action == "status":
            return self.get_status()
        elif action == "stop":
            return self.stop_daemon()
        elif action == "search":
            return self.handle_search(cmd)
        elif action == "default":
            return self.handle_default(cmd)
        elif action == "view":
            return self.handle_view(cmd)
        else:
            return {"ok": False, "error": f"未知命令: {action}"}

    def run(self) -> None:
        """Run the daemon main loop."""
        _write_daemon_info(os.getpid(), self.port, self.web_port)

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

        class ReuseAddrTCPServer(socketserver.ThreadingTCPServer):
            allow_reuse_address = True

        self._tcp_server = ReuseAddrTCPServer(
            (DEFAULT_HOST, self.port), TCPHandler,
        )
        self._tcp_server.daemon_threads = True
        tcp_thread = threading.Thread(target=self._tcp_server.serve_forever)
        tcp_thread.daemon = True
        tcp_thread.start()

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
                    full_build(directory)
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

        logger.info("Daemon 就绪")

        # Wait for shutdown signal
        self._shutdown.wait()

        # Cleanup
        logger.info("正在关闭...")
        self._tcp_server.shutdown()
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

    def run_background(self) -> None:
        """Fork to background and run daemon."""
        pid = os.fork()
        if pid > 0:
            import time
            time.sleep(0.5)
            if _pid_exists(pid):
                print(f"Daemon 已启动 (PID: {pid})")
                print(f"Web UI: http://localhost:{self.web_port}")
            else:
                print("Daemon 启动失败", file=sys.stderr)
                sys.exit(1)
            return

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

    @staticmethod
    def _setup_logging(stream=None) -> None:
        """Configure logging for the daemon."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            stream=stream,
        )
