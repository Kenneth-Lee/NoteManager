"""CLI entry point for NoteManager."""

import argparse
import functools
import http.server
import json
import sys
import time
import webbrowser
from pathlib import Path

from notemanager.daemon import is_daemon_running, NoteManagerDaemon, DAEMON_FILE
from notemanager.protocol import send_command, DEFAULT_PORT


def _ensure_daemon(port: int = DEFAULT_PORT) -> bool:
    """Make sure daemon is running. Start it if not. Returns True if running."""
    # Already running?
    if is_daemon_running():
        return True

    # Start daemon
    print("Daemon 未运行，正在启动...")
    daemon = NoteManagerDaemon(port=port)
    daemon.run_background()

    # Wait for daemon to be ready (retry for up to 10 seconds)
    for _ in range(20):
        time.sleep(0.5)
        if is_daemon_running():
            # Also verify TCP is accepting connections
            try:
                send_command({"cmd": "status"}, port=port, timeout=2)
                return True
            except ConnectionError:
                continue
    return False


def _get_daemon_port() -> int:
    """Read daemon port from daemon.json or return default."""
    if DAEMON_FILE.exists():
        try:
            info = json.loads(DAEMON_FILE.read_text(encoding="utf-8"))
            return info.get("port", DEFAULT_PORT)
        except (json.JSONDecodeError, KeyError):
            pass
    return DEFAULT_PORT


def cmd_start(args):
    pid = is_daemon_running()
    if pid:
        print(f"Daemon 已在运行 (PID: {pid})")
        return
    daemon = NoteManagerDaemon(port=args.port)
    daemon.run_background()


def cmd_stop(args):
    pid = is_daemon_running()
    if not pid:
        print("Daemon 未运行")
        return
    port = _get_daemon_port()
    try:
        resp = send_command({"cmd": "stop"}, port=port)
        print(resp.get("message", "已发送停止命令"))
    except ConnectionError:
        # Daemon may have stopped already, force cleanup
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
        resp = send_command({"cmd": "status"}, port=port)
        if not resp.get("ok"):
            print(f"错误: {resp.get('error')}")
            return
        data = resp["data"]
        print(f"Daemon PID: {data['pid']}")
        print(f"监听端口: {data['port']}")
        print(f"监控目录数: {data['directory_count']}")
        for d in data["directories"]:
            detail = data.get("details", {}).get(d, {})
            files = detail.get("file_count", "?")
            labels = detail.get("label_count", "?")
            last = detail.get("last_build", "?")
            err = detail.get("error")
            if err:
                print(f"  {d} — {err}")
            else:
                print(f"  {d} — {files} 文件, {labels} 标签, 最近构建: {last}")
    except ConnectionError:
        print("无法连接 Daemon（PID 存在但连接失败）")


def cmd_add(args):
    directory = str(Path(args.directory).resolve())
    if not Path(directory).is_dir():
        print(f"错误: 目录不存在: {directory}")
        sys.exit(1)
    port = args.port if args.port else _get_daemon_port()
    if not _ensure_daemon(port):
        print("错误: 无法启动 Daemon")
        sys.exit(1)
    try:
        resp = send_command({"cmd": "add", "directory": directory}, port=port)
        if resp.get("ok"):
            print(resp["message"])
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
        resp = send_command({"cmd": "remove", "directory": directory}, port=port)
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
        resp = send_command({"cmd": "list"}, port=port)
        if not resp.get("ok"):
            print(f"错误: {resp.get('error')}")
            return
        data = resp["data"]
        if data["count"] == 0:
            print("没有监控中的目录")
            return
        print(f"监控目录 ({data['count']}):")
        for d in data["directories"]:
            print(f"  {d}")
    except ConnectionError:
        print("错误: 无法连接 Daemon")


def cmd_serve(args):
    serve_dir = Path(args.directory) / ".NoteManager" / "html"
    if not serve_dir.exists():
        print(f"错误: {serve_dir} 不存在，请先 notemanager add 该目录")
        sys.exit(1)
    port = args.port
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(serve_dir),
    )
    with http.server.HTTPServer(("", port), handler) as httpd:
        url = f"http://localhost:{port}"
        print(f"服务已启动: {url}")
        print("按 Ctrl+C 停止")
        webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n服务已停止")


def main():
    parser = argparse.ArgumentParser(
        prog="notemanager",
        description="本地笔记管理工具",
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    start_p = subparsers.add_parser("start", help="启动后台 daemon")
    start_p.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口")

    subparsers.add_parser("stop", help="停止后台 daemon")

    subparsers.add_parser("status", help="查看 daemon 和监控状态")

    add_p = subparsers.add_parser("add", help="添加目录到监控")
    add_p.add_argument("directory", help="要监控的目录路径")
    add_p.add_argument("--port", type=int, default=0, help="daemon 端口（默认自动检测）")

    rm_p = subparsers.add_parser("remove", help="移除目录监控")
    rm_p.add_argument("directory", help="要移除的目录路径")

    subparsers.add_parser("list", help="列出所有监控中的目录")

    serve_p = subparsers.add_parser("serve", help="启动 HTTP 服务器查看笔记")
    serve_p.add_argument("directory", help="项目目录路径")
    serve_p.add_argument("--port", type=int, default=8000, help="HTTP 端口号")

    args = parser.parse_args()
    commands = {
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "add": cmd_add,
        "remove": cmd_remove,
        "list": cmd_list,
        "serve": cmd_serve,
    }
    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
