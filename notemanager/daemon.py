"""NoteManager daemon process.

Runs as a background process, listens on TCP for commands,
manages watchers for all registered directories.
"""

import json
import logging
import os
import socketserver
import sys
import threading
import datetime
from pathlib import Path

from notemanager.protocol import DEFAULT_HOST, DEFAULT_PORT, recv_command, send_response
from notemanager.builder import full_build
from notemanager.watcher import start_watcher

logger = logging.getLogger(__name__)

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


def _write_daemon_info(pid: int, port: int) -> None:
    """Write daemon runtime info."""
    HOME_NM_DIR.mkdir(parents=True, exist_ok=True)
    DAEMON_FILE.write_text(
        json.dumps({
            "pid": pid,
            "port": port,
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
        # Stale info file
        _remove_daemon_info()
        return None
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

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self.config = _load_config()
        self.config["port"] = port
        self._observers = {}  # directory -> Observer
        self._observer_threads = {}  # directory -> thread info
        self._lock = threading.Lock()
        self._shutdown = threading.Event()
        self._server = None

    def add_directory(self, directory: str) -> dict:
        """Add a directory to monitoring."""
        path = Path(directory).resolve()
        if not path.is_dir():
            return {"ok": False, "error": f"目录不存在: {path}"}

        dir_str = str(path)

        with self._lock:
            if dir_str in self._observers:
                return {"ok": False, "error": f"已在监控中: {path}"}

        # Full build first
        logger.info("构建目录: %s", path)
        try:
            full_build(path)
        except Exception as e:
            logger.error("构建失败: %s: %s", path, e)
            return {"ok": False, "error": f"构建失败: {e}"}

        # Start watcher
        try:
            observer = start_watcher(path)
            observer.start()
            with self._lock:
                self._observers[dir_str] = observer
        except Exception as e:
            logger.error("启动监控失败: %s: %s", path, e)
            return {"ok": False, "error": f"启动监控失败: {e}"}

        # Save to config
        with self._lock:
            if dir_str not in self.config["directories"]:
                self.config["directories"].append(dir_str)
                _save_config(self.config)

        logger.info("已添加监控: %s", path)
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

        # Remove .NoteManager directory
        nm_dir = Path(path) / ".NoteManager"
        if nm_dir.exists():
            import shutil
            shutil.rmtree(nm_dir, ignore_errors=True)
            logger.info("已删除: %s", nm_dir)

        return {"ok": True, "message": f"已移除: {path}"}

    def list_directories(self) -> dict:
        """List all monitored directories."""
        with self._lock:
            dirs = list(self._observers.keys())
        return {"ok": True, "data": {"directories": dirs, "count": len(dirs)}}

    def get_status(self) -> dict:
        """Get daemon status."""
        with self._lock:
            dirs = list(self._observers.keys())
        status_info = {}
        for d in dirs:
            nm_dir = Path(d) / ".NoteManager" / "status.json"
            if nm_dir.exists():
                try:
                    status_info[d] = json.loads(
                        nm_dir.read_text(encoding="utf-8")
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
                "directories": dirs,
                "directory_count": len(dirs),
                "details": status_info,
            },
        }

    def stop(self) -> dict:
        """Stop the daemon."""
        logger.info("收到停止命令")
        self._shutdown.set()
        return {"ok": True, "message": "正在停止..."}

    def handle_command(self, cmd: dict) -> dict:
        """Dispatch a command."""
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
            return self.stop()
        else:
            return {"ok": False, "error": f"未知命令: {action}"}

    def run(self) -> None:
        """Run the daemon main loop."""
        # Save daemon info
        _write_daemon_info(os.getpid(), self.port)

        # Start TCP server FIRST so CLI can connect immediately
        daemon_ref = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                try:
                    cmd = recv_command(self.request)
                    resp = daemon_ref.handle_command(cmd)
                    send_response(self.request, resp)
                except (ConnectionError, json.JSONDecodeError) as e:
                    logger.error("处理请求出错: %s", e)

        self._server = socketserver.ThreadingTCPServer(
            (DEFAULT_HOST, self.port), Handler,
            bind_and_activate=True,
        )
        self._server.daemon_threads = True

        logger.info("Daemon 已启动，监听 %s:%d", DEFAULT_HOST, self.port)

        # Run server in a thread so we can check shutdown event
        server_thread = threading.Thread(target=self._server.serve_forever)
        server_thread.daemon = True
        server_thread.start()

        # Restore previously monitored directories (AFTER TCP server is up)
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

        # Wait for shutdown signal
        self._shutdown.wait()

        # Cleanup
        logger.info("正在关闭...")
        self._server.shutdown()
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
        # First fork
        pid = os.fork()
        if pid > 0:
            # Parent waits briefly for child to start
            import time
            time.sleep(0.5)
            if _pid_exists(pid):
                print(f"Daemon 已启动 (PID: {pid})")
            else:
                print("Daemon 启动失败", file=sys.stderr)
                sys.exit(1)
            return

        # Decouple from parent
        os.setsid()

        # Second fork
        pid = os.fork()
        if pid > 0:
            os._exit(0)

        # Redirect stdin/stdout/stderr
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
