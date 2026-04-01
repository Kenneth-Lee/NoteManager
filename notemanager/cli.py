"""CLI entry point for NoteManager."""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from notemanager.daemon import (
    is_daemon_running,
    NoteManagerDaemon,
    DAEMON_FILE,
    get_daemon_web_port,
    DEFAULT_WEB_PORT,
    DAEMON_ENSURE_WAIT_STEPS,
    _daemon_responded_startup_complete,
)
from notemanager.protocol import (
    send_command, DEFAULT_PORT, DEFAULT_UNIX_SOCKET,
)


def _ensure_daemon(port: int = DEFAULT_PORT, web_port: int = DEFAULT_WEB_PORT) -> bool:
    """Make sure daemon is running and finished restoring projects. Start if needed."""
    if not is_daemon_running():
        print("Daemon 未运行，正在启动...")
        daemon = NoteManagerDaemon(port=port, web_port=web_port)
        if not daemon.run_background():
            return False

    # PID 存在不等于已恢复完监控目录；须等 status.startup_complete（见 daemon.handle_command）。
    for step in range(DAEMON_ENSURE_WAIT_STEPS):
        time.sleep(0.5)
        if is_daemon_running() and _daemon_responded_startup_complete():
            return True
        if step > 0 and step % 60 == 0:
            print(
                "仍在等待 daemon 完成首次构建与恢复监控…",
                file=sys.stderr,
            )
    return False


def _get_daemon_port() -> int:
    if DAEMON_FILE.exists():
        try:
            info = json.loads(DAEMON_FILE.read_text(encoding="utf-8"))
            return info.get("port", DEFAULT_PORT)
        except (json.JSONDecodeError, KeyError):
            pass
    return DEFAULT_PORT


def _get_daemon_socket_path() -> str:
    if DAEMON_FILE.exists():
        try:
            info = json.loads(DAEMON_FILE.read_text(encoding="utf-8"))
            return info.get("socket_path", str(DEFAULT_UNIX_SOCKET))
        except (json.JSONDecodeError, KeyError):
            pass
    return str(DEFAULT_UNIX_SOCKET)


def _parse_editor_command(editor: str) -> list[str]:
    """Split EDITOR like a shell command; Windows needs posix=False for shlex."""
    posix = os.name != "nt"
    return shlex.split(editor, posix=posix)


def _resolve_windows_editor(parts: list[str]) -> list[str]:
    """Resolve editor executable on Windows (e.g. code -> code.cmd/full path)."""
    if os.name != "nt" or not parts:
        return parts
    exe = parts[0]
    if shutil.which(exe):
        return parts
    if exe.lower() == "code" and shutil.which("code.cmd"):
        return ["code.cmd"] + parts[1:]
    if exe.lower() in ("code", "code.cmd", "code.exe"):
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "bin" / "code.cmd",
            Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft VS Code" / "bin" / "code.cmd",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft VS Code" / "bin" / "code.cmd",
        ]
        for p in candidates:
            if p.is_file():
                return [str(p)] + parts[1:]
    return parts


def cmd_start(args):
    pid = is_daemon_running()
    if pid:
        web_port = get_daemon_web_port()
        print(f"Daemon 已在运行 (PID: {pid})")
        if web_port:
            print(f"Web UI: http://localhost:{web_port}")
        return
    daemon = NoteManagerDaemon(port=args.port, web_port=args.web_port)
    if not daemon.run_background():
        sys.exit(1)


def cmd_stop(args):
    pid = is_daemon_running()
    if not pid:
        print("Daemon 未运行")
        return
    port = _get_daemon_port()
    try:
        resp = send_command(
            {"cmd": "stop"},
            port=port,
            timeout=3,
            socket_path=_get_daemon_socket_path(),
        )
        print(resp.get("message", "已发送停止命令"))
    except ConnectionError:
        print("无法连接 Daemon，强制清理")
        from notemanager.daemon import _remove_daemon_info
        _remove_daemon_info()


def cmd_status(args):
    pid = is_daemon_running()
    if not pid:
        print("Daemon 未运行")
        return
    port = _get_daemon_port()
    try:
        resp = send_command(
            {"cmd": "status"},
            port=port,
            timeout=3,
            socket_path=_get_daemon_socket_path(),
        )
        if not resp.get("ok"):
            print(f"错误: {resp.get('error')}")
            return
        data = resp["data"]
        print(f"Daemon PID: {data['pid']}")
        if data.get("transport") == "unix":
            print(f"监听方式: unix ({data.get('socket_path')})")
        else:
            print(f"监听端口: {data['port']}")
        print(f"Web UI: http://localhost:{data['web_port']}")
        print(f"监控目录数: {data['directory_count']}")
        default = data.get("default_project")
        for i, d in enumerate(data["directories"]):
            detail = data.get("details", {}).get(d, {})
            files = detail.get("file_count", "?")
            labels = detail.get("label_count", "?")
            index_count = detail.get("index_count", "?")
            last = detail.get("last_build", "?")
            err = detail.get("error")
            marker = " *" if d == default else ""
            name = Path(d).name
            if err:
                print(f"  [{i}] {name}{marker} — {err}")
            else:
                print(f"  [{i}] {name}{marker} — {files} 文件, {labels} 标签, {index_count} 索引, {last[:19]}")
        if default:
            print("  * = 默认工程")
    except ConnectionError:
        print("无法连接 Daemon（PID 存在但连接失败）")


def cmd_add(args):
    directory = str(Path(args.directory).resolve())
    if not Path(directory).is_dir():
        print(f"错误: 目录不存在: {directory}")
        sys.exit(1)
    port = args.port if args.port else _get_daemon_port()
    web_port = args.web_port if args.web_port else DEFAULT_WEB_PORT
    if not _ensure_daemon(port, web_port):
        print("错误: 无法启动 Daemon")
        sys.exit(1)
    try:
        resp = send_command(
            {"cmd": "add", "directory": directory},
            port=port,
            socket_path=_get_daemon_socket_path(),
        )
        if resp.get("ok"):
            print(resp["message"])
            web_port = get_daemon_web_port()
            if web_port:
                print(f"Web UI: http://localhost:{web_port}")
        else:
            print(f"错误: {resp.get('error')}")
    except ConnectionError:
        print("错误: 无法连接 Daemon")


def cmd_remove(args):
    directory = str(Path(args.directory).resolve())
    pid = is_daemon_running()
    if not pid:
        print("Daemon 未运行")
        sys.exit(1)
    port = _get_daemon_port()
    try:
        resp = send_command(
            {"cmd": "remove", "directory": directory},
            port=port,
            timeout=5,
            socket_path=_get_daemon_socket_path(),
        )
        if resp.get("ok"):
            print(resp["message"])
        else:
            print(f"错误: {resp.get('error')}")
    except ConnectionError:
        print("错误: 无法连接 Daemon")


def cmd_list(args):
    pid = is_daemon_running()
    if not pid:
        print("Daemon 未运行")
        return
    port = _get_daemon_port()
    try:
        resp = send_command(
            {"cmd": "list"},
            port=port,
            timeout=3,
            socket_path=_get_daemon_socket_path(),
        )
        if not resp.get("ok"):
            print(f"错误: {resp.get('error')}")
            return
        data = resp["data"]
        if data["count"] == 0:
            print("没有监控中的目录")
            return
        default = data.get("default_project")
        print(f"监控目录 ({data['count']}):")
        for i, d in enumerate(data["directories"]):
            marker = " *" if d == default else ""
            print(f"  [{i}] {Path(d).name}{marker} ({d})")
        if default:
            print("  * = 默认工程")
        web_port = get_daemon_web_port()
        if web_port:
            print(f"\nWeb UI: http://localhost:{web_port}")
    except ConnectionError:
        print("错误: 无法连接 Daemon")


def _resolve_project(args):
    """Resolve --project flag to a project index. Returns index or exits."""
    port = _get_daemon_port()
    try:
        resp = send_command(
            {"cmd": "list"},
            port=port,
            timeout=3,
            socket_path=_get_daemon_socket_path(),
        )
    except ConnectionError:
        print("错误: 无法连接 Daemon")
        sys.exit(1)
    if not resp.get("ok"):
        print(f"错误: {resp.get('error')}")
        sys.exit(1)
    dirs = resp["data"]["directories"]
    if not dirs:
        print("错误: 没有监控中的目录，请先使用 notemanager add 添加")
        sys.exit(1)

    project = getattr(args, "project", None)
    if project:
        # Try as index number
        try:
            idx = int(project)
            if 0 <= idx < len(dirs):
                return idx
        except ValueError:
            pass
        for i, d in enumerate(dirs):
            if Path(d).name == project:
                return i
        for i, d in enumerate(dirs):
            if project.lower() in Path(d).name.lower():
                return i
        print(f"错误: 未找到工程 '{project}'")
        print("可用工程:")
        for i, d in enumerate(dirs):
            print(f"  [{i}] {Path(d).name}")
        sys.exit(1)

    # Use default project if set
    default = resp["data"].get("default_project")
    if default and default in dirs:
        return dirs.index(default)
    return 0


def cmd_search(args):
    """Send search query to the running web UI."""
    if not _ensure_daemon():
        print("错误: 无法启动 Daemon")
        sys.exit(1)

    idx = _resolve_project(args)
    project_dir = Path(_resolve_project_dir(args))
    port = _get_daemon_port()
    try:
        resp = send_command(
            {"cmd": "search", "query": args.query, "project": idx},
            port=port,
            timeout=5,
            socket_path=_get_daemon_socket_path(),
        )
    except ConnectionError:
        print("错误: 无法连接 Daemon")
        sys.exit(1)

    if resp.get("ok"):
        print(resp["message"])
        index_file = project_dir / ".NoteManager" / "html" / "_index" / "search_index.json"
        if not index_file.is_file():
            print(f"未找到索引文件: {index_file}")
            return
        data = json.loads(index_file.read_text(encoding="utf-8"))
        docs = data.get("documents", [])
        results = _search_entries(docs, args.query)
        paths = []
        seen = set()
        for item in results:
            src = _resolve_source_from_result(project_dir, item.get("path", ""))
            if src is None:
                continue
            full = str(src.resolve())
            if full not in seen:
                seen.add(full)
                paths.append(full)
        if not paths:
            print("命令行结果: 无可映射源文件")
            return
        print("命令行结果（源文件绝对路径）:")
        for p in paths:
            print(p)
    else:
        print(f"错误: {resp.get('error')}")
        sys.exit(1)


def cmd_default(args):
    """Set or show the default project."""
    pid = is_daemon_running()
    if not pid:
        print("Daemon 未运行")
        sys.exit(1)
    port = _get_daemon_port()
    try:
        resp = send_command(
            {"cmd": "default", "project": args.project or ""},
            port=port,
            timeout=3,
            socket_path=_get_daemon_socket_path(),
        )
    except ConnectionError:
        print("错误: 无法连接 Daemon")
        sys.exit(1)

    if resp.get("ok"):
        print(resp["message"])
    else:
        print(f"错误: {resp.get('error')}")
        sys.exit(1)


def _resolve_project_dir(args) -> str:
    idx = _resolve_project(args)
    port = _get_daemon_port()
    resp = send_command(
        {"cmd": "list"},
        port=port,
        timeout=3,
        socket_path=_get_daemon_socket_path(),
    )
    return resp["data"]["directories"][idx]


def _parse_query(query: str):
    m = re.match(r"^(chapter|label|index|dtag):(.+)$", query, re.IGNORECASE)
    if m:
        return m.group(1).lower(), m.group(2)
    return None, query


def _search_entries(entries, query: str):
    typ, core = _parse_query(query)
    if not core:
        return []
    try:
        rex = re.compile(core, re.IGNORECASE)
    except re.error:
        rex = re.compile(re.escape(core), re.IGNORECASE)
    results = []
    for e in entries:
        if typ and e.get("type") != typ:
            continue
        title = e.get("title", "")
        text = e.get("text", "")
        if rex.search(title) or rex.search(text):
            results.append(e)
            if len(results) >= 200:
                break
    return results


def _resolve_source_from_result(project_dir: Path, result_path: str) -> Path | None:
    html_rel = result_path.split("#", 1)[0]
    if not html_rel.endswith(".html"):
        return None
    base_rel = Path(html_rel).with_suffix("")
    rst = (project_dir / base_rel).with_suffix(".rst")
    md = (project_dir / base_rel).with_suffix(".md")
    if rst.is_file():
        return rst
    if md.is_file():
        return md
    return None


def cmd_searche(args):
    """Search and choose an entry to cd/edit source file."""
    if not _ensure_daemon():
        print("错误: 无法启动 Daemon")
        sys.exit(1)

    project_dir = Path(_resolve_project_dir(args))
    index_file = project_dir / ".NoteManager" / "html" / "_index" / "search_index.json"
    if not index_file.is_file():
        print(f"错误: 搜索索引不存在: {index_file}")
        sys.exit(1)

    data = json.loads(index_file.read_text(encoding="utf-8"))
    docs = data.get("documents", [])
    results = _search_entries(docs, args.query)
    if not results:
        print("没有搜索结果")
        return

    print(f"找到 {len(results)} 个结果（显示前 30 条）：")
    shown = results[:30]
    for i, item in enumerate(shown, start=1):
        print(f"[{i}] {item.get('title', '')} ({item.get('type', '')})")
        print(f"    {item.get('path', '')}")

    sel = input("选择编号（输入数字直接编辑，0 取消）: ").strip().lower()
    if not sel or sel == "0":
        return
    m = re.match(r"^(\d+)$", sel)
    if not m:
        print("输入无效")
        return
    idx = int(m.group(1)) - 1
    if idx < 0 or idx >= len(shown):
        print("编号越界")
        return

    target = _resolve_source_from_result(project_dir, shown[idx].get("path", ""))
    if target is None:
        print("该结果无法映射到源文件")
        return

    editor = os.environ.get("EDITOR")
    if not editor:
        print("错误: 未设置 EDITOR 环境变量")
        sys.exit(1)
    try:
        editor_parts = _parse_editor_command(editor)
    except ValueError as e:
        print(f"错误: 无法解析 EDITOR（检查引号与空格）: {e}")
        sys.exit(1)
    if not editor_parts:
        print("错误: EDITOR 解析结果为空")
        sys.exit(1)
    editor_parts = _resolve_windows_editor(editor_parts)
    cmd = editor_parts + [str(target)]
    try:
        subprocess.run(cmd, check=False)
    except FileNotFoundError:
        if os.name == "nt":
            shell_cmd = editor + " " + subprocess.list2cmdline([str(target)])
            try:
                subprocess.run(["cmd", "/c", shell_cmd], check=False)
                return
            except OSError:
                pass
        exe = cmd[0] if cmd else editor
        print(f"错误: 找不到编辑器「{exe}」。请确认其在 PATH 中。")
        if os.name == "nt":
            print("提示: VS Code 可设置 EDITOR=\"code --wait\"，或直接设为 code.cmd 的完整路径。")
        sys.exit(1)


def cmd_view(args):
    """Open a source file in web UI, render temp page if unmanaged."""
    if not _ensure_daemon():
        print("错误: 无法启动 Daemon")
        sys.exit(1)
    src = str(Path(args.file).expanduser().resolve())
    port = _get_daemon_port()
    try:
        resp = send_command(
            {"cmd": "view", "file": src},
            port=port,
            timeout=8,
            socket_path=_get_daemon_socket_path(),
        )
    except ConnectionError:
        print("错误: 无法连接 Daemon")
        sys.exit(1)
    if resp.get("ok"):
        print(resp.get("message", "已发送查看请求"))
    else:
        print(f"错误: {resp.get('error')}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="notemanager",
        description="本地笔记管理工具",
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    start_p = subparsers.add_parser("start", help="启动后台 daemon")
    start_p.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP 端口")
    start_p.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT, help="Web UI 端口")

    subparsers.add_parser("stop", help="停止后台 daemon")
    subparsers.add_parser("status", help="查看 daemon 和监控状态")

    add_p = subparsers.add_parser("add", help="添加目录到监控")
    add_p.add_argument("directory", help="要监控的目录路径")
    add_p.add_argument("--port", type=int, default=0, help="TCP 端口")
    add_p.add_argument("--web-port", type=int, default=0, help="Web UI 端口")

    rm_p = subparsers.add_parser("remove", help="移除目录监控")
    rm_p.add_argument("directory", help="要移除的目录路径")

    subparsers.add_parser("list", help="列出所有监控中的目录")

    search_p = subparsers.add_parser("search", help="在 Web UI 中搜索并显示结果")
    search_p.add_argument("query", help="搜索关键词 (支持正则和类型前缀如 chapter:xxx)")
    search_p.add_argument("--project", "-p", help="指定工程 (编号或目录名)")
    searche_p = subparsers.add_parser("searche", help="搜索并交互选择后进入目录或编辑")
    searche_p.add_argument("query", help="搜索关键词 (支持正则和类型前缀如 chapter:xxx)")
    searche_p.add_argument("--project", "-p", help="指定工程 (编号或目录名)")
    view_p = subparsers.add_parser("view", help="在 Web UI 查看指定 md/rst 文件")
    view_p.add_argument("file", help="要查看的 md/rst 文件路径")

    default_p = subparsers.add_parser("default", help="设置或查看默认工程")
    default_p.add_argument("project", nargs="?", default="",
                           help="工程编号或目录名 (省略则查看当前默认)")

    args = parser.parse_args()
    commands = {
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "add": cmd_add,
        "remove": cmd_remove,
        "list": cmd_list,
        "search": cmd_search,
        "searche": cmd_searche,
        "view": cmd_view,
        "default": cmd_default,
    }
    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
