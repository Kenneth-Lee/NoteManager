"""冷启动时用于判断是否需要 full_build 的源树摘要（scan_manifest.json）。"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from pathlib import Path

from notemanager import __version__
from notemanager.scanner import FileInfo, scan_directory

logger = logging.getLogger(__name__)

MANIFEST_VERSION = 1
SCAN_MANIFEST_NAME = "scan_manifest.json"


def build_manifest_payload(project_root: Path, files: list[FileInfo]) -> dict:
    """由已扫描的 FileInfo 列表计算摘要（与 docs/快速统计缓存设计.md 一致）。"""
    root = project_root.resolve()
    lines: list[str] = []
    max_mtime_ns = 0
    for f in files:
        mtime_ns = int(f.mtime * 1_000_000_000)
        max_mtime_ns = max(max_mtime_ns, mtime_ns)
        lines.append(f"{f.rel_path.as_posix()}\0{mtime_ns}\0{f.size}\n")
    lines.sort()
    blob = "".join(lines).encode("utf-8")
    tree_rollup = hashlib.sha256(blob).hexdigest()

    root_stat = root.stat()
    r_ns = getattr(root_stat, "st_mtime_ns", int(root_stat.st_mtime * 1_000_000_000))
    root_anchor = f"{r_ns}:{root_stat.st_ino}"

    return {
        "version": MANIFEST_VERSION,
        "generated_at": datetime.datetime.now().isoformat(),
        "tree_rollup": tree_rollup,
        "file_count": len(files),
        "max_mtime_ns": max_mtime_ns,
        "root_anchor": root_anchor,
        "engine": f"notemanager-{__version__}",
    }


def write_scan_manifest(nm_dir: Path, payload: dict) -> None:
    nm_dir.mkdir(parents=True, exist_ok=True)
    path = nm_dir / SCAN_MANIFEST_NAME
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_scan_manifest(nm_dir: Path) -> dict | None:
    path = nm_dir / SCAN_MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("scan_manifest.json 损坏，将忽略: %s", path)
        return None


def live_payload_and_files(project_root: str | Path) -> tuple[dict, list[FileInfo]]:
    """扫描项目并返回摘要 + FileInfo 列表（单次遍历）。"""
    root = Path(project_root).resolve()
    files = scan_directory(root)
    return build_manifest_payload(root, files), files
