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
import webbrowser
from pathlib import Path

from notemanager import __version__
from notemanager.daemon import (
    is_daemon_running,
    NoteManagerDaemon,
    DAEMON_FILE,
    get_daemon_web_port,
    DEFAULT_WEB_PORT,
    DAEMON_ENSURE_WAIT_STEPS,
    _daemon_responded_startup_complete,
    _pid_exists,
    _load_config,
)
from notemanager.protocol import (
    send_command, DEFAULT_PORT, DEFAULT_UNIX_SOCKET,
)
from notemanager.indexer import (
    filter_search_documents,
    parse_search_query,
)
from notemanager.checker import check_file_with_options, CHECK_SCOPES, INPUT_FORMATS


def _load_config_safe() -> dict:
    try:
        return _load_config()
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _valid_port(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def _startup_ports(
    port=None,
    web_port=None,
    *,
    fallback_port=None,
    fallback_web_port=None,
) -> tuple[int, int]:
    """Resolve startup ports: explicit CLI > config.json > fallback > default."""
    cfg = _load_config_safe()
    resolved_port = (
        _valid_port(port)
        or _valid_port(cfg.get("port"))
        or _valid_port(fallback_port)
        or DEFAULT_PORT
    )
    resolved_web = (
        _valid_port(web_port)
        or _valid_port(cfg.get("web_port"))
        or _valid_port(fallback_web_port)
        or DEFAULT_WEB_PORT
    )
    return resolved_port, resolved_web


def _ensure_daemon(port: int | None = None, web_port: int | None = None) -> bool:
    """Make sure daemon is running and finished restoring projects. Start if needed."""
    if not is_daemon_running():
        start_port, start_web_port = _startup_ports(port, web_port)
        print("Daemon 未运行，正在启动...")
        daemon = NoteManagerDaemon(port=start_port, web_port=start_web_port)
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
    if getattr(args, "restart", False):
        _restart_daemon(args)
        return
    pid = is_daemon_running()
    if pid:
        web_port = get_daemon_web_port()
        print(f"Daemon 已在运行 (PID: {pid})")
        if web_port:
            print(f"Web UI: http://localhost:{web_port}")
        print("提示: 使用 nmr start --restart 重启")
        return
    port, web_port = _startup_ports(
        getattr(args, "port", 0), getattr(args, "web_port", 0),
    )
    daemon = NoteManagerDaemon(port=port, web_port=web_port)
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


def _restart_daemon(args):
    """Stop the running daemon (if any) and start a fresh one.

    Used by ``nmr start --restart``.  Without explicit CLI ports, config.json
    wins over the current runtime values; explicit flags only override this
    run and are not written back to the config file.
    """
    pid = is_daemon_running()
    old_port = None
    old_web_port = None
    if pid:
        old_port = _get_daemon_port()
        old_web_port = get_daemon_web_port()
        print(f"正在停止 Daemon (PID: {pid})...")
        try:
            resp = send_command(
                {"cmd": "stop"},
                port=old_port,
                timeout=3,
                socket_path=_get_daemon_socket_path(),
            )
            print(resp.get("message", "正在停止..."))
        except (ConnectionError, TimeoutError):
            print("错误: 无法连接 Daemon，无法确认其已停止；请先检查 nmr status")
            sys.exit(1)

        # The stop command only sets the shutdown event.  daemon.json is
        # removed during cleanup, before the process has necessarily exited
        # and released the Web port.  Wait for the actual PID too, not just
        # for the metadata file to disappear.
        for _ in range(100):  # up to ~10s
            if not _pid_exists(pid) and not is_daemon_running():
                break
            time.sleep(0.1)
        else:
            print("错误: Daemon 未在预期时间内退出，未启动新实例")
            sys.exit(1)
    else:
        print("Daemon 未运行，直接启动")

    port, web_port = _startup_ports(
        args.port,
        args.web_port,
        fallback_port=old_port,
        fallback_web_port=old_web_port,
    )
    daemon = NoteManagerDaemon(port=port, web_port=web_port)
    if not daemon.run_background():
        sys.exit(1)


def _format_age(timestamp) -> str:
    try:
        delta = max(0, int(time.time() - float(timestamp)))
    except (TypeError, ValueError):
        return "?"
    if delta < 60:
        return f"{delta}s 前"
    if delta < 3600:
        return f"{delta // 60}m 前"
    return f"{delta // 3600}h 前"


def _print_web_clients_hint(clients, directories=None) -> None:
    if not clients:
        print("  当前没有已连接的 Web 页面。")
        return
    print("  已连接的页面：")
    for c in clients:
        mark = "[主]" if c.get("is_main") else "    "
        project = c.get("project")
        if isinstance(project, int):
            if directories and 0 <= project < len(directories):
                label = f"[{project}] {Path(directories[project]).name}"
            else:
                label = f"[{project}]"
        elif str(c.get("path") or "").startswith("/temp/"):
            label = "(临时页)"
        else:
            label = "(控制台)"
        print(
            f"    {mark} {c.get('id')}  {label}  {c.get('path')}  "
            f"{_format_age(c.get('last_active'))}"
        )


def cmd_status(args):
    pid = is_daemon_running()
    if not pid:
        print(f"NoteManager 版本: {__version__}")
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
        if getattr(args, "json", False):
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return
        daemon_version = data.get("version") or "未知"
        print(f"NoteManager 版本: {daemon_version}")
        if daemon_version != __version__:
            print(f"  CLI 版本: {__version__}（与 Daemon 不一致，建议重启 Daemon）")
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
            ref_lbl = detail.get("label_count", "?")
            dtags = detail.get("dtag_count")
            if dtags is None:
                dtags = "?"
            index_count = detail.get("index_count", "?")
            last = detail.get("last_build", "?")
            err = detail.get("error")
            marker = " *" if d == default else ""
            name = Path(d).name
            if err:
                print(f"  [{i}] {name}{marker} — {err}")
            else:
                print(
                    f"  [{i}] {name}{marker} — {files} 文件, {ref_lbl} 交叉引用, "
                    f"{dtags} 文档标记, {index_count} 索引, {last[:19]}"
                )
        if default:
            print("  * = 默认工程")

        clients = data.get("clients") or []
        main = data.get("main_client")
        main_connected = data.get("main_connected")
        if main:
            suffix = f"，主页面 {main}" if main_connected else f"，主页面 {main} 正在重连"
        else:
            suffix = ""
        print(f"Web 页面: {len(clients)}{suffix}")
        _print_web_clients_hint(clients, data.get("directories"))
        if main and not main_connected:
            print("  提示: 主页面正在重连宽限期内；可用 nmr main <id> 手动切换。")

        if data.get("config_pending_error"):
            print(f"配置: 无法解析（{data.get('config_pending_error')}）")
        elif data.get("config_pending"):
            labels = {
                "port": "TCP 端口", "web_port": "Web 端口",
                "transport": "传输方式", "socket_path": "Socket 路径",
                "directories": "监控目录", "default_project": "默认工程",
                "editor": "外部编辑器",
            }
            fields = [labels.get(f, f) for f in (data.get("config_pending_fields") or [])]
            print(f"配置: 有未重启修改（{'、'.join(fields)}）；执行 nmr start --restart 生效")
    except ConnectionError:
        print("无法连接 Daemon（PID 存在但连接失败）")


def cmd_add(args):
    directory = str(Path(args.directory).resolve())
    if not Path(directory).is_dir():
        print(f"错误: 目录不存在: {directory}")
        sys.exit(1)
    requested_port = args.port or None
    requested_web_port = args.web_port or None
    if not _ensure_daemon(requested_port, requested_web_port):
        print("错误: 无法启动 Daemon")
        sys.exit(1)
    port = _get_daemon_port()
    try:
        resp = send_command(
            {"cmd": "add_with_progress", "directory": directory},
            port=port,
            socket_path=_get_daemon_socket_path(),
            timeout=8,
        )
        if not resp.get("ok"):
            # Backward-compatible fallback for older daemon versions.
            if "未知命令" in str(resp.get("error", "")):
                legacy = send_command(
                    {"cmd": "add", "directory": directory},
                    port=port,
                    socket_path=_get_daemon_socket_path(),
                )
                if legacy.get("ok"):
                    print(legacy["message"])
                    web_port = get_daemon_web_port()
                    if web_port:
                        print(f"Web UI: http://localhost:{web_port}")
                    return
                print(f"错误: {legacy.get('error')}")
                return
            print(f"错误: {resp.get('error')}")
            return

        job_id = resp.get("job_id")
        if job_id is None:
            print("错误: Daemon 未返回 add 任务 ID")
            sys.exit(1)

        since = 0
        while True:
            st = send_command(
                {"cmd": "add_progress", "job_id": job_id, "since": since},
                port=port,
                socket_path=_get_daemon_socket_path(),
                timeout=20,
            )
            if not st.get("ok"):
                print(f"错误: {st.get('error')}")
                sys.exit(1)

            new_lines = st.get("new_lines") or []
            for line in new_lines:
                print(line)
            since = int(st.get("total_lines", since))

            if st.get("done"):
                if st.get("success"):
                    msg = st.get("message") or f"已添加监控: {directory}"
                    print(msg)
                    web_port = get_daemon_web_port()
                    if web_port:
                        print(f"Web UI: http://localhost:{web_port}")
                    return
                print(f"错误: {st.get('error') or '添加失败'}")
                sys.exit(1)

            time.sleep(0.2)
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


def _compile_search_regex(query: str) -> re.Pattern[str]:
    """与 filter_search_documents 一致：核心串为正则，非法则按字面转义。"""
    _, core = parse_search_query(query)
    if not core:
        return re.compile(r"(?!.)")
    try:
        return re.compile(core, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(core), re.IGNORECASE)


def _stdout_supports_color() -> bool:
    if os.environ.get("NO_COLOR", "").strip():
        return False
    return sys.stdout.isatty()


def _highlight_regex_matches(segment: str, rex: re.Pattern[str], hi: str, reset: str) -> str:
    parts: list[str] = []
    pos = 0
    for m in rex.finditer(segment):
        parts.append(segment[pos : m.start()])
        parts.append(hi)
        parts.append(segment[m.start() : m.end()])
        parts.append(reset)
        pos = m.end()
    parts.append(segment[pos:])
    return "".join(parts)


def _format_search_result_text(
    text: str,
    rex: re.Pattern[str],
    *,
    max_len: int = 80,
    use_color: bool,
) -> str:
    """截取关键字附近至多 max_len 字符，并对匹配子串加终端高亮。"""
    hi = "\033[1;33m" if use_color else ""
    reset = "\033[0m" if use_color else ""
    if not text:
        return ""
    n = len(text)
    if n <= max_len:
        return _highlight_regex_matches(text, rex, hi, reset)
    m = rex.search(text)
    if not m:
        return text[:max_len] + "…"
    m0, m1 = m.span()
    if m1 - m0 >= max_len:
        start, end = m0, m0 + max_len
    else:
        start = max(0, min(m0, n - max_len))
        if start + max_len < m1:
            start = m1 - max_len
        start = max(0, start)
        end = min(n, start + max_len)
    segment = text[start:end]
    out = _highlight_regex_matches(segment, rex, hi, reset)
    if start > 0:
        out = "…" + out
    if end < n:
        out = out + "…"
    return out


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

    port = _get_daemon_port()
    sock = _get_daemon_socket_path()
    payload: dict = {"cmd": "search", "query": args.query, "project": _resolve_project(args)}
    if getattr(args, "target", None):
        payload["target"] = args.target
    if getattr(args, "new", False):
        payload["new"] = True
    try:
        resp = send_command(
            payload,
            port=port,
            timeout=5,
            socket_path=sock,
        )
    except ConnectionError:
        print("错误: 无法连接 Daemon")
        sys.exit(1)

    if not resp.get("ok"):
        print(f"错误: {resp.get('error')}")
        _print_web_clients_hint(resp.get("clients"))
        sys.exit(1)

    if resp.get("new_client_id"):
        web_port = resp.get("web_port") or get_daemon_web_port() or DEFAULT_WEB_PORT
        url = resp.get("new_url") or resp.get("url")
        full_url = f"http://localhost:{web_port}{url}"
        webbrowser.open_new_tab(full_url)
        print(f"已在新页面打开: {full_url}")
        print(f"新页面 id: {resp['new_client_id']}（之后可用 --id 指定）")
    else:
        print(resp.get("message", ""))
    try:
        list_resp = send_command(
            {"cmd": "list"},
            port=port,
            timeout=3,
            socket_path=sock,
        )
    except ConnectionError:
        print("错误: 无法获取工程列表")
        sys.exit(1)
    if not list_resp.get("ok"):
        print(f"错误: {list_resp.get('error')}")
        sys.exit(1)
    dirs = list_resp["data"]["directories"]

    project_dir = Path(dirs[payload["project"]])
    index_file = project_dir / ".NoteManager" / "html" / "_index" / "search_index.json"
    if not index_file.is_file():
        print(f"未找到索引文件: {index_file}")
        return
    data = json.loads(index_file.read_text(encoding="utf-8"))
    docs = data.get("documents", [])
    results = filter_search_documents(docs, args.query, limit=200)
    search_rex = _compile_search_regex(args.query)
    use_color = _stdout_supports_color()

    for item in results:
        src = _resolve_source_from_result(project_dir, item.get("path", ""))
        if src is None:
            continue
        full = str(src.resolve())
        body = _format_search_result_text(
            item.get("text", "") or "",
            search_rex,
            max_len=80,
            use_color=use_color,
        )
        print(f"{full}: {body}")

    if not getattr(args, "target", None) and not resp.get("new_client_id") and not resp.get("delivered"):
        print("（没有可用的主页面，以上结果仅在终端显示）")

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


def _resolve_source_from_result(project_dir: Path, result_path: str) -> Path | None:
    html_rel = result_path.split("#", 1)[0]
    if not html_rel.endswith(".html"):
        return None
    base_rel = Path(html_rel).with_suffix("")
    rst = (project_dir / base_rel).with_suffix(".rst")
    md = (project_dir / base_rel).with_suffix(".md")
    txt = (project_dir / base_rel).with_suffix(".txt")
    if rst.is_file():
        return rst
    if md.is_file():
        return md
    if txt.is_file():
        return txt
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
    results = filter_search_documents(docs, args.query, limit=500)

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

    chosen = shown[idx]
    target = _resolve_source_from_result(project_dir, chosen.get("path", ""))
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


def cmd_rebuild(args):
    """全量重建所有监控工程（HTML、索引、_static），阻塞至完成并打印进度。"""
    if not _ensure_daemon():
        print("错误: 无法启动 Daemon")
        sys.exit(1)
    port = _get_daemon_port()
    sock = _get_daemon_socket_path()
    payload: dict = {"cmd": "rebuild"}
    if getattr(args, "project", None):
        payload["project"] = args.project
    try:
        resp = send_command(payload, port=port, timeout=30, socket_path=sock)
    except ConnectionError:
        print("错误: 无法连接 Daemon")
        sys.exit(1)
    if not resp.get("ok"):
        print(f"错误: {resp.get('error')}")
        sys.exit(1)
    job_id = resp.get("job_id")
    if job_id is None:
        print("错误: 未返回 job_id")
        sys.exit(1)
    since = 0
    while True:
        try:
            pr = send_command(
                {"cmd": "rebuild_progress", "job_id": job_id, "since": since},
                port=port,
                timeout=120,
                socket_path=sock,
            )
        except ConnectionError:
            print("错误: 轮询进度时断开连接")
            sys.exit(1)
        if not pr.get("ok"):
            print(f"错误: {pr.get('error')}")
            sys.exit(1)
        for line in pr.get("new_lines") or []:
            print(line, flush=True)
        since = pr.get("total_lines", since)
        if pr.get("done"):
            if pr.get("success") is not True:
                err = pr.get("error") or "重建失败"
                print(f"错误: {err}", file=sys.stderr)
                sys.exit(1)
            print("重建全部完成。", flush=True)
            break
        time.sleep(0.15)


def cmd_export(args):
    """导出手机用 .html.zip（独立渲染树，不打包 .NoteManager/html）。"""
    if not _ensure_daemon():
        print("错误: 无法启动 Daemon")
        sys.exit(1)
    root = Path(_resolve_project_dir(args)).resolve()
    out = (
        Path(args.output).expanduser().resolve()
        if getattr(args, "output", None)
        else None
    )
    from notemanager.export_bundle import export_mobile_html_zip

    try:
        path = export_mobile_html_zip(root, out, progress=print)
    except OSError as e:
        print(f"错误: {e}")
        sys.exit(1)
    print(path)


def cmd_view(args):
    """Open a source file in the main (or specified) browser page."""
    if not _ensure_daemon():
        print("错误: 无法启动 Daemon")
        sys.exit(1)
    src = str(Path(args.file).expanduser().resolve())
    port = _get_daemon_port()
    payload = {"cmd": "view", "file": src}
    if getattr(args, "target", None):
        payload["target"] = args.target
    if getattr(args, "new", False):
        payload["new"] = True
    try:
        resp = send_command(
            payload,
            port=port,
            timeout=8,
            socket_path=_get_daemon_socket_path(),
        )
    except ConnectionError:
        print("错误: 无法连接 Daemon")
        sys.exit(1)
    if not resp.get("ok"):
        print(f"错误: {resp.get('error')}")
        _print_web_clients_hint(resp.get("clients"))
        sys.exit(1)

    web_port = resp.get("web_port") or get_daemon_web_port() or DEFAULT_WEB_PORT
    if resp.get("new_client_id"):
        url = resp.get("new_url") or resp.get("url")
        full_url = f"http://localhost:{web_port}{url}"
        webbrowser.open_new_tab(full_url)
        print(f"已在新页面打开: {full_url}")
        print(f"新页面 id: {resp['new_client_id']}（之后可用 --id 指定）")
        return

    if resp.get("delivered"):
        print(resp.get("message", "已发送查看请求"))
        return

    if getattr(args, "target", None):
        print(resp.get("message", f"页面 {args.target} 不可用"))
        _print_web_clients_hint(resp.get("clients"))
        return

    # 没有可用目标（例如 daemon 刚启动、页面还没重连）时，退回直接开新页面。
    url = resp.get("url")
    if url:
        full_url = f"http://localhost:{web_port}{url}"
        webbrowser.open_new_tab(full_url)
        print(f"已在浏览器打开: {full_url}")
        if resp.get("message"):
            print(resp.get("message"))
    else:
        print(resp.get("message", "没有可用的 Web 页面"))


def cmd_main(args):
    """Show or change the main page used by view/search by default."""
    if not is_daemon_running():
        print("Daemon 未运行")
        sys.exit(1)
    port = _get_daemon_port()
    try:
        resp = send_command(
            {"cmd": "main", "client": args.client or ""},
            port=port,
            timeout=3,
            socket_path=_get_daemon_socket_path(),
        )
    except ConnectionError:
        print("错误: 无法连接 Daemon")
        sys.exit(1)
    if not resp.get("ok"):
        print(f"错误: {resp.get('error')}")
        _print_web_clients_hint(resp.get("clients"))
        sys.exit(1)
    print(resp.get("message", ""))
    if not args.client:
        _print_web_clients_hint(resp.get("clients"))
        if resp.get("main_client") and not resp.get("main_connected"):
            print("  主页面正在重连宽限期内。")


def cmd_check(args):
    """Check a Markdown/RST file for renderer-visible issues and exit nonzero on problems."""
    fmt = args.format.lower()
    scope = args.only.lower()
    if scope not in CHECK_SCOPES:
        print(f"错误: 不支持的检查范围: {scope}")
        sys.exit(1)

    if args.stdin:
        if args.file:
            print("错误: 使用 --stdin 时不能同时提供文件路径")
            sys.exit(1)
        if fmt == "auto":
            print("错误: 使用 --stdin 时必须通过 --format 指定输入格式（md/rst/mermaid/math）")
            sys.exit(1)
        if fmt not in INPUT_FORMATS:
            print(f"错误: 不支持的输入格式: {fmt}")
            sys.exit(1)
        content = sys.stdin.read()
        from notemanager.checker import check_text

        diags = check_text(content, input_format=fmt, source_name="<stdin>", scope=scope)
        source_label = "<stdin>"
    else:
        if not args.file:
            print("错误: 请提供要检查的文件路径，或使用 --stdin 从标准输入读取")
            sys.exit(1)
        path = Path(args.file).expanduser()
        if not path.exists():
            print(f"错误: 文件不存在: {path}")
            sys.exit(1)
        if fmt not in {"auto", *INPUT_FORMATS}:
            print(f"错误: 不支持的输入格式: {fmt}")
            sys.exit(1)
        diags = check_file_with_options(path, input_format=fmt, scope=scope)
        source_label = str(path)

    if not diags:
        print(f"OK: {source_label} 没有检测到渲染语法问题")
        return

    for diag in diags:
        print(diag.format())
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="notemanager",
        description="本地笔记管理工具",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"NoteManager {__version__}",
        help="显示 NoteManager 版本并退出",
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    start_p = subparsers.add_parser("start", help="启动后台 daemon")
    start_p.add_argument("--port", type=int, default=0,
                         help="TCP 端口（默认 19832；--restart 时默认沿用当前 daemon）")
    start_p.add_argument("--web-port", type=int, default=0,
                         help="Web UI 端口（默认 19833；--restart 时默认沿用当前 daemon）")
    start_p.add_argument("--restart", "-r", action="store_true",
                         help="先停止当前 daemon，再用相同端口启动新实例")
    subparsers.add_parser("stop", help="停止后台 daemon")
    status_p = subparsers.add_parser("status", help="查看 daemon、项目与 Web 页面状态")
    status_p.add_argument("--json", action="store_true", help="以 JSON 输出完整状态")

    add_p = subparsers.add_parser("add", help="添加目录到监控")
    add_p.add_argument("directory", help="要监控的目录路径")
    add_p.add_argument("--port", type=int, default=0, help="TCP 端口")
    add_p.add_argument("--web-port", type=int, default=0, help="Web UI 端口")

    rm_p = subparsers.add_parser("remove", help="移除目录监控")
    rm_p.add_argument("directory", help="要移除的目录路径")

    subparsers.add_parser("list", help="列出所有监控中的目录")

    rebuild_p = subparsers.add_parser(
        "rebuild",
        help="强制全量重建（更新 HTML、索引、notemanager.js 等），等待完成并显示进度",
    )
    rebuild_p.add_argument(
        "--project",
        "-p",
        help="仅重建指定工程（编号或目录名）；省略则重建全部监控目录",
    )

    export_p = subparsers.add_parser(
        "export",
        help="导出手机本地管家用 .html.zip（仅阅读与离线索引查找，不含编辑与 grep）",
    )
    export_p.add_argument(
        "--project",
        "-p",
        help="指定工程（编号或目录名）；省略则用默认工程或列表第 0 项",
    )
    export_p.add_argument(
        "--output",
        "-o",
        dest="output",
        help="输出 zip 路径；默认当前目录下 <工程目录名>.html.zip",
    )

    search_p = subparsers.add_parser("search", help="在 Web UI 中搜索并显示结果")
    search_p.add_argument(
        "query",
        help="搜索关键词（正则）；grep: 为全局正文块搜索，chapter:/label:/ 等仅当前工程",
    )
    search_p.add_argument("--project", "-p", help="指定工程 (编号或目录名)")
    search_target = search_p.add_mutually_exclusive_group()
    search_target.add_argument("--id", dest="target", help="发送到指定页面 id（可用唯一前缀）")
    search_target.add_argument("--new", action="store_true",
                               help="在新页面中打开搜索结果，不占用主页面")
    searche_p = subparsers.add_parser("searche", help="搜索并交互选择后进入目录或编辑")
    searche_p.add_argument(
        "query",
        help="搜索关键词（正则）；grep: 为当前工程正文块全局搜索",
    )
    searche_p.add_argument("--project", "-p", help="指定工程 (编号或目录名)")
    view_p = subparsers.add_parser("view", help="在 Web UI 查看指定 md/rst/txt 文件")
    view_p.add_argument("file", help="要查看的 md/rst/txt 文件路径")
    view_target = view_p.add_mutually_exclusive_group()
    view_target.add_argument("--id", dest="target", help="发送到指定页面 id（可用唯一前缀）")
    view_target.add_argument("--new", action="store_true",
                             help="在新页面中打开，不占用主页面")

    check_p = subparsers.add_parser("check", help="检查文件或标准输入中的渲染语法问题")
    check_p.add_argument("file", nargs="?", help="要检查的文件路径（md/rst）。若使用 --stdin 可省略")
    check_p.add_argument("--stdin", action="store_true", help="从标准输入读取内容进行检查")
    check_p.add_argument(
        "--format",
        default="auto",
        choices=["auto", "md", "rst", "mermaid", "math"],
        help="输入格式：auto(仅文件)、md、rst、mermaid(纯 mermaid 文本)、math(纯数学片段)",
    )
    check_p.add_argument(
        "--only",
        default="all",
        choices=["all", "math", "mermaid"],
        help="检查范围：all(默认)、math(仅数学)、mermaid(仅 mermaid)。仅对 md 输入生效。",
    )

    default_p = subparsers.add_parser("default", help="设置或查看默认工程")
    default_p.add_argument("project", nargs="?", default="",
                           help="工程编号或目录名 (省略则查看当前默认)")

    main_p = subparsers.add_parser("main", help="查看或设置主页面（view/search 默认目标）")
    main_p.add_argument("client", nargs="?", default="",
                        help="页面 id（可用唯一前缀）；省略则查看当前主页面")

    args = parser.parse_args()
    commands = {
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "add": cmd_add,
        "remove": cmd_remove,
        "list": cmd_list,
        "rebuild": cmd_rebuild,
        "export": cmd_export,
        "search": cmd_search,
        "searche": cmd_searche,
        "view": cmd_view,
        "check": cmd_check,
        "default": cmd_default,
        "main": cmd_main,
    }
    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
