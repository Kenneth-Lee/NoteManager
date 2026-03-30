"""CLI entry point for NoteManager."""

import argparse
import sys
import signal


def cmd_build(args):
    from notemanager.builder import full_build

    print(f"扫描目录: {args.directory}")
    nm_dir = full_build(args.directory)
    from pathlib import Path
    status_file = Path(nm_dir) / "status.json"
    import json
    status = json.loads(status_file.read_text(encoding="utf-8"))
    print(f"构建完成: {status['file_count']} 个文件, {status['label_count']} 个标签")
    print(f"输出目录: {nm_dir}")


def cmd_watch(args):
    from notemanager.builder import full_build
    from notemanager.watcher import start_watcher

    # Do initial build if needed
    from pathlib import Path
    project_root = Path(args.directory).resolve()
    nm_dir = project_root / ".NoteManager"
    if not nm_dir.exists() or args.force:
        print("首次构建...")
        full_build(args.directory)
        print()

    # Start watching
    observer = start_watcher(args.directory, debounce=args.debounce)
    observer.start()
    print(f"正在监控: {args.directory}")
    print("按 Ctrl+C 停止")

    def stop(signum, frame):
        print("\n停止监控...")
        observer.stop()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    try:
        while observer.is_alive():
            observer.join(1)
    except KeyboardInterrupt:
        print("\n停止监控...")
        observer.stop()
    observer.join()


def cmd_serve(args):
    import functools
    import http.server
    import webbrowser
    from pathlib import Path

    serve_dir = Path(args.directory) / ".NoteManager" / "html"
    if not serve_dir.exists():
        print(f"错误: {serve_dir} 不存在，请先运行 notemanager build 或 notemanager watch")
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


_DEFAULT_CSS = """\
/* NoteManager default stylesheet */
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    max-width: 900px;
    margin: 0 auto;
    padding: 20px;
    line-height: 1.6;
    color: #333;
}
.nm-nav {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 8px 0;
    border-bottom: 1px solid #ddd;
    margin-bottom: 20px;
}
.nm-nav a { text-decoration: none; color: #0066cc; }
.nm-nav a:hover { text-decoration: underline; }
#nm-search {
    padding: 4px 8px;
    border: 1px solid #ccc;
    border-radius: 4px;
    width: 200px;
}
.nm-content { max-width: 100%; }
pre {
    background: #f5f5f5;
    padding: 12px;
    border-radius: 4px;
    overflow-x: auto;
}
code {
    background: #f5f5f5;
    padding: 2px 4px;
    border-radius: 2px;
}
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
th { background: #f5f5f5; }
.admonition {
    padding: 12px;
    margin: 12px 0;
    border-left: 4px solid #0066cc;
    background: #f0f7ff;
}
.admonition-title { font-weight: bold; margin-bottom: 4px; }
#nm-search-results {
    position: absolute;
    background: white;
    border: 1px solid #ccc;
    max-height: 300px;
    overflow-y: auto;
    display: none;
    z-index: 100;
    width: 400px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.15);
}
#nm-search-results a {
    display: block;
    padding: 8px;
    text-decoration: none;
    color: #333;
    border-bottom: 1px solid #eee;
}
#nm-search-results a:hover { background: #f0f7ff; }
img { max-width: 100%; }
"""

_DEFAULT_JS = """\
/* NoteManager search functionality */
(function() {
    var searchInput = document.getElementById('nm-search');
    var resultsDiv = document.getElementById('nm-search-results');
    if (!searchInput || !resultsDiv) return;

    var searchData = null;
    var debounceTimer = null;

    function loadIndex() {
        if (searchData) return Promise.resolve(searchData);
        var pathParts = window.location.pathname.split('/');
        var depth = pathParts.length - 2;
        var prefix = '';
        for (var i = 0; i < depth; i++) prefix += '../';
        return fetch(prefix + 'index/search_index.json')
            .then(function(r) { return r.json(); })
            .then(function(d) { searchData = d; return d; });
    }

    function search(query) {
        if (!searchData || !query) return [];
        var q = query.toLowerCase();
        var results = [];
        for (var i = 0; i < searchData.documents.length; i++) {
            var doc = searchData.documents[i];
            if (doc.title.toLowerCase().indexOf(q) >= 0 ||
                doc.text.toLowerCase().indexOf(q) >= 0) {
                results.push(doc);
                if (results.length >= 20) break;
            }
        }
        return results;
    }

    function getPath(doc) {
        var pathParts = window.location.pathname.split('/');
        var depth = pathParts.length - 2;
        var prefix = '';
        for (var i = 0; i < depth; i++) prefix += '../';
        return prefix + doc.path;
    }

    searchInput.addEventListener('input', function() {
        clearTimeout(debounceTimer);
        var query = this.value.trim();
        if (!query) { resultsDiv.style.display = 'none'; return; }
        debounceTimer = setTimeout(function() {
            loadIndex().then(function() {
                var results = search(query);
                if (!results.length) {
                    resultsDiv.innerHTML = '<div style="padding:8px">无搜索结果</div>';
                } else {
                    var html = '';
                    for (var i = 0; i < results.length; i++) {
                        html += '<a href="' + getPath(results[i]) + '">' +
                                results[i].title + '</a>';
                    }
                    resultsDiv.innerHTML = html;
                }
                resultsDiv.style.display = 'block';
            });
        }, 300);
    });

    document.addEventListener('click', function(e) {
        if (!resultsDiv.contains(e.target) && e.target !== searchInput) {
            resultsDiv.style.display = 'none';
        }
    });
})();
"""


def main():
    parser = argparse.ArgumentParser(
        prog="notemanager",
        description="本地笔记管理工具",
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    build_parser = subparsers.add_parser("build", help="扫描并构建 HTML")
    build_parser.add_argument("directory", help="要管理的目录路径")
    build_parser.add_argument("--force", action="store_true", help="强制全量重建")

    watch_parser = subparsers.add_parser("watch", help="监控目录变化并自动更新")
    watch_parser.add_argument("directory", help="要监控的目录路径")
    watch_parser.add_argument("--force", action="store_true", help="强制全量重建后再开始监控")
    watch_parser.add_argument("--debounce", type=float, default=0.3,
                              help="防抖时间（秒），默认 0.3")

    serve_parser = subparsers.add_parser("serve", help="启动测试 HTTP 服务器")
    serve_parser.add_argument("directory", help="项目目录路径")
    serve_parser.add_argument("--port", type=int, default=8000, help="端口号")

    args = parser.parse_args()
    if args.command == "build":
        cmd_build(args)
    elif args.command == "watch":
        cmd_watch(args)
    elif args.command == "serve":
        cmd_serve(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
