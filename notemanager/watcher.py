"""File system watcher for NoteManager.

Uses watchdog to monitor a directory for .rst/.md file changes
and triggers incremental updates.
"""

import time
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileModifiedEvent, FileDeletedEvent, FileMovedEvent

from notemanager.scanner import INCLUDE_EXTENSIONS, SKIP_DIRS


class NoteEventHandler(FileSystemEventHandler):
    """Handle file system events for .rst/.md files."""

    def __init__(self, project_root: Path, debounce_seconds: float = 0.3):
        self.project_root = project_root.resolve()
        self.debounce_seconds = debounce_seconds
        self._pending = {}  # path -> (event_type, timestamp)
        self._timer = None

    def _is_target(self, path: str) -> bool:
        """Check if this file path should be monitored."""
        p = Path(path)
        if p.suffix.lower() not in INCLUDE_EXTENSIONS:
            return False
        # Skip excluded directories
        try:
            rel = p.relative_to(self.project_root)
            return not any(part in SKIP_DIRS for part in rel.parts)
        except ValueError:
            return False

    def _schedule_update(self, path: str, event_type: str):
        """Debounce rapid events for the same file."""
        import threading

        abs_path = str(Path(path).resolve())

        # Cancel existing timer
        if self._timer is not None:
            self._timer.cancel()

        self._pending[abs_path] = event_type

        def process():
            # Collect all pending events
            pending = dict(self._pending)
            self._pending.clear()
            self._timer = None

            from notemanager.builder import update_file, remove_file

            for fpath, etype in pending.items():
                try:
                    if etype == "deleted":
                        remove_file(self.project_root, fpath)
                    else:
                        # created or modified
                        if Path(fpath).exists():
                            update_file(self.project_root, fpath)
                except Exception as e:
                    print(f"处理 {fpath} 时出错: {e}")

        self._timer = threading.Timer(self.debounce_seconds, process)
        self._timer.daemon = True
        self._timer.start()

    def on_created(self, event):
        if not event.is_directory and self._is_target(event.src_path):
            print(f"新增: {event.src_path}")
            self._schedule_update(event.src_path, "created")

    def on_modified(self, event):
        if not event.is_directory and self._is_target(event.src_path):
            self._schedule_update(event.src_path, "modified")

    def on_deleted(self, event):
        if not event.is_directory and self._is_target(event.src_path):
            print(f"删除: {event.src_path}")
            self._schedule_update(event.src_path, "deleted")

    def on_moved(self, event):
        if not event.is_directory:
            if self._is_target(event.src_path):
                print(f"移走: {event.src_path}")
                self._schedule_update(event.src_path, "deleted")
            if self._is_target(event.dest_path):
                print(f"移入: {event.dest_path}")
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
