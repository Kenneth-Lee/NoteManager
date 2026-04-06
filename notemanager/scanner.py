"""File scanner for NoteManager."""

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath

# Extensions to include
INCLUDE_EXTENSIONS = {".rst", ".md", ".txt"}

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
    ext: str          # .rst / .md / .txt
    mtime: float      # Modification time
    size: int         # File size in bytes


@dataclass(frozen=True)
class IgnoreRule:
    pattern: str
    negate: bool
    anchored: bool
    dir_only: bool


def load_ignore_rules(project_root: str | Path) -> list[IgnoreRule]:
    """Load .nmrignore rules from project root.

    Syntax is gitignore-like (subset):
    - blank lines and lines starting with # are ignored
    - leading ! negates a previous ignore rule
    - leading / anchors rule to project root
    - trailing / marks directory rule (matches that dir and descendants)
    """
    root = Path(project_root).resolve()
    ignore_file = root / ".nmrignore"
    if not ignore_file.is_file():
        return []

    rules: list[IgnoreRule] = []
    for raw in ignore_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        if negate:
            line = line[1:].strip()
            if not line:
                continue
        anchored = line.startswith("/")
        if anchored:
            line = line[1:]
        dir_only = line.endswith("/")
        if dir_only:
            line = line[:-1]
        line = line.strip()
        if not line:
            continue
        rules.append(IgnoreRule(line, negate, anchored, dir_only))
    return rules


def _rule_matches(rel_posix: str, rule: IgnoreRule, *, is_dir: bool) -> bool:
    rel = rel_posix.strip("/")
    if not rel:
        return False
    p = PurePosixPath(rel)
    name = p.name

    if rule.dir_only:
        # directory pattern matches the dir itself and everything under it
        if rule.anchored:
            return rel == rule.pattern or rel.startswith(rule.pattern + "/")
        parts = rel.split("/")
        prefixes = ["/".join(parts[:i + 1]) for i in range(len(parts))]
        for pref in prefixes:
            q = PurePosixPath(pref)
            if q.match(rule.pattern) or q.match("**/" + rule.pattern):
                return True
            if fnmatch.fnmatch(q.name, rule.pattern):
                return True
        return False

    if rule.anchored:
        return p.match(rule.pattern)

    return (
        p.match(rule.pattern)
        or p.match("**/" + rule.pattern)
        or fnmatch.fnmatch(name, rule.pattern)
    )


def is_ignored_path(
    project_root: str | Path,
    path: str | Path,
    *,
    is_dir: bool = False,
    rules: list[IgnoreRule] | None = None,
) -> bool:
    """Whether a file/dir under project root is ignored by .nmrignore."""
    root = Path(project_root).resolve()
    p = Path(path).resolve()
    try:
        rel = p.relative_to(root).as_posix()
    except ValueError:
        return False
    rel = rel.strip("/")
    if not rel:
        return False

    rs = rules if rules is not None else load_ignore_rules(root)
    ignored = False
    for r in rs:
        if _rule_matches(rel, r, is_dir=is_dir):
            ignored = not r.negate
    return ignored


def scan_directory(project_root: str | Path) -> list[FileInfo]:
    """Recursively scan a directory for .rst/.md/.txt files.

    Args:
        project_root: The root directory to scan.

    Returns:
        List of FileInfo objects for all discovered files.
    """
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")

    rules = load_ignore_rules(root)
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue

        # Skip files in excluded directories
        parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in parts):
            continue
        if is_ignored_path(root, path, is_dir=False, rules=rules):
            continue

        ext = path.suffix.lower()
        if ext in INCLUDE_EXTENSIONS:
            rel = path.relative_to(root)
            st = path.stat()
            files.append(FileInfo(
                path=path,
                rel_path=rel,
                ext=ext,
                mtime=st.st_mtime,
                size=st.st_size,
            ))

    return sorted(files, key=lambda f: f.rel_path)
