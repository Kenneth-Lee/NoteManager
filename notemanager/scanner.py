"""File scanner for NoteManager."""

from dataclasses import dataclass
from pathlib import Path

# Extensions to include
INCLUDE_EXTENSIONS = {".rst", ".md"}

# Directory names to skip
SKIP_DIRS = {
    ".NoteManager",
    "_build",
    "_static",
    "_extensions",
    "__pycache__",
    ".git",
    ".svn",
    ".hg",
}


@dataclass
class FileInfo:
    path: Path        # Absolute path
    rel_path: Path    # Relative to project root
    ext: str          # .rst or .md
    mtime: float      # Modification time


def scan_directory(project_root: str | Path) -> list[FileInfo]:
    """Recursively scan a directory for .rst and .md files.

    Args:
        project_root: The root directory to scan.

    Returns:
        List of FileInfo objects for all discovered files.
    """
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")

    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue

        # Skip files in excluded directories
        parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in parts):
            continue

        ext = path.suffix.lower()
        if ext in INCLUDE_EXTENSIONS:
            rel = path.relative_to(root)
            files.append(FileInfo(
                path=path,
                rel_path=rel,
                ext=ext,
                mtime=path.stat().st_mtime,
            ))

    return sorted(files, key=lambda f: f.rel_path)
