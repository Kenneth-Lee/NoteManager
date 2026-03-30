"""Simple HTTP server for testing NoteManager output."""

import functools
import http.server
import webbrowser
from pathlib import Path


def serve(directory: str | Path, port: int = 8000) -> None:
    """Serve .NoteManager/html/ on localhost.

    Args:
        directory: Project directory containing .NoteManager/.
        port: Port number to serve on.
    """
    serve_dir = Path(directory) / ".NoteManager" / "html"
    if not serve_dir.exists():
        print(f"错误: {serve_dir} 不存在，请先运行 notemanager build")
        return

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(serve_dir),
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
