# NoteManager

本地笔记管理工具。自动监控目录中的 .rst 和 .md 文件，渲染成可浏览的 HTML，支持全文搜索和交叉引用跳转。

## 特性

- 同时支持 reStructuredText (.rst) 和 Markdown (.md)
- 自动监控文件变化，增量更新索引
- 浏览器内全文搜索，不依赖后端服务
- 支持 `:ref:` / `:doc:` 交叉引用
- 生成的 HTML 目录可独立拷贝，有浏览器就能查看
- Web 控制面板统一管理所有项目

## 安装

```bash
pip install -e .
```

依赖：Python >= 3.10，自动安装 docutils、Markdown、pygments、watchdog。

## 使用

### 启动

```bash
notemanager start
```

Daemon 启动后自动开启 Web 控制面板，默认地址：http://localhost:19833

### 添加监控目录

```bash
notemanager add /path/to/your/notes
```

添加后自动执行首次构建并开始监控文件变化。

### 其他命令

```bash
notemanager status          # 查看 daemon 状态和所有项目信息
notemanager list            # 列出所有监控中的目录
notemanager remove <目录>   # 停止监控并删除生成的文件
notemanager stop            # 停止 daemon
```

### Web 控制面板

启动 daemon 后访问 http://localhost:19833 ，可以：

- 查看所有监控目录及其状态
- 添加/移除目录
- 手动触发重建
- 直接浏览各项目的 HTML 输出

### 可移植性

每个被管理的目录下会生成 `.NoteManager/` 子目录，其中的 `html/` 目录是完整的可浏览站点。将整个 `.NoteManager/` 拷贝到任何有浏览器的地方都能使用，包括搜索功能。

## 目录结构

被管理目录下的 `.NoteManager/`：

```
.NoteManager/
├── html/                   # 渲染后的 HTML，保持原目录结构
├── index/
│   ├── search_index.json   # 全文搜索索引
│   └── ref_labels.json     # 交叉引用标签映射
├── static/
│   ├── notemanager.css
│   └── notemanager.js
└── status.json
```

全局配置在 `~/.NoteManager/`：

```
~/.NoteManager/
├── config.json     # 监控目录列表和端口配置
├── daemon.json     # daemon 运行时信息（PID、端口）
└── daemon.log      # 后台日志
```
