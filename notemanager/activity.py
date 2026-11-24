"""Build a lightweight document activity list for reading status/homepage."""

from __future__ import annotations

import datetime
import json
from pathlib import Path


def build_and_write(project_root: Path, html_dir: Path, documents: list[dict]) -> dict:
    project_root = Path(project_root)
    docs = []
    for doc in documents:
        path = str(doc.get("path") or "")
        if not path:
            continue
        source = str(doc.get("source_path") or "")
        mtime = 0.0
        if source:
            try:
                source_path = Path(source)
                if not source_path.is_absolute():
                    source_path = project_root / source_path
                mtime = source_path.stat().st_mtime
            except OSError:
                mtime = 0.0
        date = ""
        if mtime:
            try:
                date = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                date = ""
        docs.append({
            "path": path,
            "title": str(doc.get("title") or Path(path).stem),
            "mtime": mtime,
            "date": date,
        })
    docs.sort(key=lambda d: (d["mtime"], d["path"]), reverse=True)
    data = {"version": 1, "docs": docs}
    (html_dir / "_index" / "activity.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return data
