"""File system watcher for NoteManager.

Uses watchdog to monitor .rst/.md/.txt file changes and triggers incremental updates.
"""

import logging
import threading
import time
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from notemanager.scanner import (
    INCLUDE_EXTENSIONS,
    SKIP_DIRS,
    load_ignore_rules,
    is_ignored_path,
)

logger = logging.getLogger(__name__)

_SUPPRESS_LOCK = threading.Lock()
_SUPPRESSED_UNTIL: dict[str, float] = {}


def suppress_path(path: str | Path, seconds: float = 2.0) -> None:
    """Suppress watcher-triggered rebuild for a path in a short time window."""
    abs_path = str(Path(path).resolve())
    until = time.time() + max(0.1, float(seconds))
    with _SUPPRESS_LOCK:
        _SUPPRESSED_UNTIL[abs_path] = until


def _is_suppressed(path: str | Path) -> bool:
    abs_path = str(Path(path).resolve())
    now = time.time()
    with _SUPPRESS_LOCK:
        until = _SUPPRESSED_UNTIL.get(abs_path)
        if until is None:
            return False
        if until <= now:
            _SUPPRESSED_UNTIL.pop(abs_path, None)
            return False
        return True


class NoteEventHandler(FileSystemEventHandler):
    """Handle file system events for .rst/.md/.txt files."""

    def __init__(self, project_root: Path, debounce_seconds: float = 0.3):
        self.project_root = project_root.resolve()
        self.debounce_seconds = debounce_seconds
        self._ignore_rules = load_ignore_rules(self.project_root)
        self._pending = {}  # abs_path -> event_type
        self._timer = None
        self._lock = threading.Lock()

    def _is_target(self, path: str) -> bool:
        """Check if this file path should be monitored."""
        p = Path(path)
        if p.suffix.lower() not in INCLUDE_EXTENSIONS:
            return False
        try:
            rel = p.relative_to(self.project_root)
            if any(part in SKIP_DIRS for part in rel.parts):
                return False
            if is_ignored_path(
                self.project_root, p, is_dir=False, rules=self._ignore_rules,
            ):
                return False
            return True
        except ValueError:
            return False

    def _schedule_update(self, path: str, event_type: str):
        """Debounce rapid events for the same file."""
        abs_path = str(Path(path).resolve())
        if _is_suppressed(abs_path):
            return

        with self._lock:
            self._pending[abs_path] = event_type

            if self._timer is not None:
                self._timer.cancel()

            def process():
                with self._lock:
                    pending = dict(self._pending)
                    self._pending.clear()
                    self._timer = None

                from notemanager.builder import update_file, remove_file

                for fpath, etype in pending.items():
                    try:
                        if etype == "deleted":
                            remove_file(self.project_root, fpath)
                        else:
                            if Path(fpath).exists():
                                update_file(
                                    self.project_root,
                                    fpath,
                                    refresh_static_assets=False,
                                    refresh_project_assets=False,
                                )
                    except Exception as e:
                        logger.error("处理 %s 时出错: %s", fpath, e)

                # Notify web clients about file changes
                try:
                    from notemanager.web import notify_event
                    notify_event("build_complete", {"directory": str(self.project_root)})
                except Exception:
                    pass

            self._timer = threading.Timer(self.debounce_seconds, process)
            self._timer.daemon = True
            self._timer.start()

    def _on_ignore_file_changed(self):
        """Reload .nmrignore and rebuild once so include/exclude takes effect."""
        self._ignore_rules = load_ignore_rules(self.project_root)
        try:
            from notemanager.builder import full_build

            full_build(self.project_root)
            from notemanager.web import notify_event

            notify_event("build_complete", {"directory": str(self.project_root)})
        except Exception as e:
            logger.error(".nmrignore 变更后重建失败: %s", e)

    def on_created(self, event):
        if not event.is_directory and Path(event.src_path).name == ".nmrignore":
            self._on_ignore_file_changed()
            return
        if not event.is_directory and self._is_target(event.src_path):
            logger.info("新增: %s", event.src_path)
            self._schedule_update(event.src_path, "created")

    def on_modified(self, event):
        if not event.is_directory and Path(event.src_path).name == ".nmrignore":
            self._on_ignore_file_changed()
            return
        if not event.is_directory and self._is_target(event.src_path):
            self._schedule_update(event.src_path, "modified")

    def on_deleted(self, event):
        if not event.is_directory and Path(event.src_path).name == ".nmrignore":
            self._on_ignore_file_changed()
            return
        if not event.is_directory and self._is_target(event.src_path):
            logger.info("删除: %s", event.src_path)
            self._schedule_update(event.src_path, "deleted")

    def on_moved(self, event):
        if not event.is_directory:
            if self._is_target(event.src_path):
                logger.info("移走: %s", event.src_path)
                self._schedule_update(event.src_path, "deleted")
            if self._is_target(event.dest_path):
                logger.info("移入: %s", event.dest_path)
                self._schedule_update(event.dest_path, "created")


def start_watcher(project_root: str | Path, debounce: float = 0.3) -> Observer:
    """Start watching a directory for file changes.

    Returns the Observer instance (call .stop() to stop).
    """
    project_root = Path(project_root).resolve()

    handler = NoteEventHandler(project_root, debounce)
    observer = Observer()
    observer.schedule(handler, str(project_root), recursive=True)

    return observer
