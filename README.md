# NoteManager

## 一句话介绍

NoteManager是一个基于命令行和WebUI的，以个人充分掌控信息，不被任何商业盈利目的绑定的笔记管理软件.

## 初始设想

我大部分时间用Python-Sphinx写我的笔记，在一些项目里，偶尔也会用markdown。Sphinx的好处是格式完整，比较适合写书，缺点是要考虑这是哪个主题的一部分，要多写不少额外的东西，关键是要看之前，还要编译一下，内容多了以后编译时间很长；Markdown的好处是简单快捷，适合临时写个笔记，不用考虑写的这个东西具体属于哪个章节，缺点是章节之间互相独立，除了用grep查找，检索比较麻烦。

我也尝试过用Obsidian一类的工具去自动管理这些关联关系，但我还是觉得不好用，Obsidian那些关联图看着很Cool，其实对于检索内容没啥用。我更希望是：我输入一个关键字，自动帮我找到和这个关键字相关的文档，然后我开始编辑，编辑完了存盘，我就不用管了。我又不是编辑，信息记录进去就行了，其他的尽量我就不想介入。如果要处理这些信息，我也不打算提供插件，我的逻辑是：这不过是个数据库，你爱怎么处理它，你独立处理就是了，我的控制流和那个工具没有关系，这样你才具有所有的主动性。

对于这样一个需求，NoteManager的作用主要就是建立索引，我也不绑定特定的格式，我先支持txt, rst和md格式，以后还可以增加更多的文本格式。NoteManager的工作就是按约定把里面的索引找出来，建立一个索引数据库。而每个文本文件本身，按照格式的约定，都渲染成html文件。这样，一方面你可以通过索引数据库找到文档，另一方面你也可以同时看到文档和它渲染后的结果。这样，作为笔记，它该做的就都做了。其他功能，就不是笔记软件本身的功能了。

为此，我这里尽量不介入你的文件管理。你把你要管理的所有笔记都放同一个目录下，我在这个目录下创建一个.NoteManager子目录，在里面给你生成索引和html文件，不改变你任何内容，你不需要NoteManager了，你把这个目录删掉就是了。NoteManager后台可以动态监控你的修改，你用其他工具更新了目录中的笔记文件，我们就在后代给你动态更新索引。

所以，我们不使用Sphinx那样的一次次重新生成静态索引的机制。我们把每个笔记都看作是独立的，里面有一组可以被别人索引的引用，都可以作为被搜索的对象，而其他文件，要不直接链接这个文件的某个位置，要不请求动态索引，我直接根据关键字动态计算是什么页面的什么位置，直接给你找出来。其他文件搜索，除了支持原来rst/md等文件的静态索引，我们也支持动态索引语法。也就是说，如果你没有指定确切的位置，索引某个关键字，我们的搜索是一段搜索NoteManager索引数据库的代码，你总可以从最新的更新中找到对应的定义。

我们整个设想是一个用户的笔记，全部都在一个目录下，你分简单的目录建构以便拷贝就行了，其他随便放，反正最后我们都是通过索引找到它的。但用于有时会引入多个目录，以便实现不同的功能，比如有些目录是日常笔记，有些目录是某本Sphinx写的书。这样用户不会希望这两者的索引是在一起的，所以，我们还是有“Project（项目）”的概念，但我们不希望用户需要进入或者打开那个项目，然后才能控制那些笔记，因为用户的思路一闪即逝，笔记这种东西，还是尽快打开就开始记录，记录完了扔在那里，慢慢整理才好。所以，你可以创建很多项目，但其中有一个是默认项目，默认的操作都是在这个项目上，但你也可以立即指定项目进行查找或者修改。

这样我们就有两种控制目录：一种是针对项目的.NoteManager，另一种是针对用户的.NoteManager。前者的用途我们介绍过了，而后者主要就是记住有哪些项目，以及一些全局的控制信息。

## 用户界面

NoteManager的用户界面是这样构思的：首先，我们对每个用户，创建了一个管理项目的后台，这个称为NoteManager Daemon，简称nmrd。这样，所有命令，无论你从哪里发出，都是由这个后台执行的，不会互相冲突。我们的NoteManager命令（简称nmr），都直接发到nmrd上，由nmrd执行。nmrd同时会启动一个webui界面，允许你在本机，或者在其他机器上直接管理所有工程。

这样，我们实际上就有两个接口：命令行接口和WebUI接口。两个接口功能基本上是类似的，只是设计得更适合那个接口执行。比如，你要在命令行上立即编辑某个笔记，你用nmr search去找某个关键字，在命令行上会返回每个笔记文件的目录，允许你直接编辑那个文件。但同时，webui上也会显示所有的搜索结果，你可以直接在webui上点击这个链接，直接查看对应的文件。进入特定的笔记，在webui上你可以直接选择编辑，在webui上更新文件。而同时，如果你在命令行上编辑那个文件，监控功能也能在你保存以后，在webui上把你新的编辑更新进来。当然，有些功能只能在命令行上做，比如关掉nmrd。也有写功能只适合在webui上做，比如你在编辑某个笔记的时候，用Ctrl-V把某幅截图拷贝到笔记上。这个在命令行上要你把截图先保存成文件，然后在笔记上写对应的链接。这种工作就不适合在命令行上一步到位，而webui上就可以。

用户可以灵活组合这两种操作，更快完成工作。

我还写了一个手机软件：[本地管家](https://atomgit.com/Kenneth-Lee-2025/local_manager)，支持把html目录压缩成zip文件，可以在手机上直接查看。

我们前面说了，你可以把工程的.NoteManager/html直接打包，这个目录就是可以当作一个完整的html网站使用。但这个在本地管家显示出来效果并不好，因为我们为了让PC好用，会增加越来越复杂的界面元素，而在手机上我们最多只是为了容易看，不是为了编辑它，所以我们会额外做了一个export功能（同样在命令行和webui上都提供），让你生成一个简单版本的.html.zip文件，可以在本地管家上完成除了编辑以外的所有阅读和查找功能。

顺便说一句，如果你只是要分享几个md或者rst文件，而不是要生成html，本地管家是支持.md.zip和.rst.zip格式的。那种情况下，你甚至都不需要NoteManager的支持，可以直接把几个md或者rst文件压缩一下，直接在本地管家就可以查看渲染结果。

我原来做了另一个rst/md的渲染工具：[markdown_quickview](https://atomgit.com/Kenneth-Lee-2025/markdown_quickview)，那个可以独立渲染某个md/rst文件，这个功能现在我合并到本工具上了。你可以用nmr view命令渲染某个rst/md文件，如果这个文件在管理的项目之内，这会直接使用渲染好的结果，如果不在，也能临时渲染一个文件供查看。后者和前者的唯一区别是后者不是个工程，所以你引用其他rst/md文件可能引用不了。

## 安装

### 方式一：正式安装（推荐）

如果你已经把这个项目发布到 PyPI（或私有索引）：

```bash
pip install notemanager
```

### 方式二：本地开发安装（editable）

在源码目录下：

```bash
pip install -e .
```

依赖：Python >= 3.10，自动安装 `docutils`、`Markdown`、`pygments`、`watchdog`。

### 从源码生成安装包（wheel / sdist）

在仓库根目录（含 `pyproject.toml`）执行：

```bash
pip install build
python -m build
```

完成后在 `dist/` 目录会得到：

| 文件 | 说明 |
|------|------|
| `notemanager-<版本>-py3-none-any.whl` | Wheel，可直接分发或 `pip install` |
| `notemanager-<版本>.tar.gz` | 源码发行包（sdist），供索引或再次构建 |

本地安装刚生成的 wheel 做验证：

```bash
pip install dist/notemanager-*-py3-none-any.whl
```

若需上传到 PyPI（需 [PyPI](https://pypi.org/) 账号与 API token）：

```bash
pip install twine
twine check dist/*
twine upload dist/*
```

上传前通常会先清理旧产物：`rm -rf dist/ build/ *.egg-info`，再重新执行 `python -m build`。

## 使用

### 启动

```bash
notemanager start
# 或
nmr start

# 重启（默认沿用当前 daemon 的端口）
nmr start --restart
```

Daemon 启动后自动开启 Web 控制面板，默认地址：http://localhost:19833

### 添加监控目录

```bash
notemanager add /path/to/your/notes
# 或
nmr add /path/to/your/notes
```

添加后自动执行首次构建并开始监控文件变化。若目录较大，`nmr add` 会持续输出构建进度。

### 可选界面美化插件（UI Plugin）

默认不配置时，界面保持当前基础风格。

若希望启用内置美化主题，请在项目根目录创建 `.nmrui.json`：

```json
{
  "plugin": "modern"
}
```

然后执行一次 `nmr rebuild` 即可生效。

可通过下述配置关闭插件：

```json
{
  "enabled": false
}
```

### 其他命令

```bash
nmr status                  # 查看 daemon、项目信息与所有 Web 页面 id
nmr list                    # 列出所有监控中的目录
nmr remove <目录>           # 停止监控并删除生成的文件
nmr default [工程]          # 设置/查看默认工程
nmr main [页面id]           # 查看/设置主页面（view/search 的默认目标）
nmr start --restart         # 重启 daemon，默认沿用当前端口
nmr stop                    # 停止 daemon
```

### 导出手机用 .html.zip（export）

面向 [本地管家](https://atomgit.com/Kenneth-Lee-2025/local_manager) 等应用：在**临时目录独立渲染**工程内 `.md`/`.rst`，再打 zip，**不**打包工程下已有的 `.NoteManager/html`（避免与控制台复杂页混用，便于以后分别演进）。

**页面特点**：仅保留渲染结果（KaTeX、Mermaid 等与控制台一致）；无侧栏、无「源文件 / 诊断」、不调用 Daemon。**首页（`index.html`）** 独占「查找」与文档树；**笔记页** 顶栏只有 **首页**（回到首页再查）和标题，本页目录用 `<details>` 折叠在同一页内，无弹层。**再查其他笔记**时请点 **首页**，保证手机上每次只呈现一个完整页面层次。

**命令行**（需 Daemon 在线以解析 `-p` 工程，导出在本地执行）：

```bash
nmr export [-p 工程] [--output 路径.html.zip]
```

未指定 `--output` / `-o` 时，默认写入**当前工作目录**下 `<工程目录名>.html.zip`。

**Web 控制台**：各项目行的「**导出ZIP**」按钮会向 `POST /api/export_zip`（JSON：`{"project": <工程下标>}`）请求 zip，浏览器下载得到同名 `.html.zip`。

### 搜索、编辑与联动

```bash
nmr search <关键词> [-p 工程]
```

- 将搜索请求推送到 WebUI（自动跳到目标项目并显示结果）
- 同时在命令行输出所有匹配结果对应的源文件绝对路径，便于复制后自行 `cd`

```bash
nmr searche <关键词> [-p 工程]
```

- 交互式选择搜索结果
- 输入数字后直接用 `$EDITOR` 打开对应源文件（例如 `export EDITOR=vim` 或 `export EDITOR="code -w"`）

```bash
nmr view <文件路径.md|.rst|.txt>
```

- 若文件属于已管理项目：跳转到该文件对应的项目 HTML 页面
- 若文件未纳入管理：临时渲染单文件页面（`/temp/...`）并跳转显示
- 默认只影响**主页面**；用 `--id <页面id>` 指定其他页面，或 `--new` 新开一个页面

### 多页面与主页面

每个浏览器 tab 会在 `window.name` 里保存一个稳定的页面 id（`nm-...`），刷新/跳转后不变；新开的 tab 是新的从页面。

- **主页面**：第一个打开的页面。`nmr view` / `nmr search` 默认只发给它，不再影响其他页面。
- **从页面**：后续打开的页面，默认不接收命令；可用 `nmr view --id <id>`、`nmr search --id <id>` 精确指定。
- **新页面**：`nmr view --new` / `nmr search --new` 会新开 tab，并打印新页面 id，后续可用 `--id` 继续控制它。
- **主页面关闭/刷新**：刷新或跳转有 3 秒宽限期，同一 id 重连后仍是主页面；真正关闭后，最近活动的从页面自动继任。也可用 `nmr main <id>` 手动切换。
- `nmr status` 会列出所有页面 id、所属工程、当前路径和主页面标记；`nmr status --json` 输出机器可读格式。

```bash
nmr status
nmr view --id a1b2c3 notes/design.md
nmr view --new notes/design.md
nmr search --id a1b2c3 关键词
nmr main d4e5f6
```

> 升级后已有的工程页需要执行一次 `nmr rebuild` 才会加载新的页面 id 脚本；临时页会自动刷新。

### Web 控制面板

启动 daemon 后访问 http://localhost:19833 ，可以：

- 查看所有监控目录及其状态
- 添加/移除目录
- 手动触发重建
- **导出ZIP**：生成上述手机用 `.html.zip`
- 进入项目页面浏览 HTML 输出（侧边栏 Home 回到该项目站点主页）
- 需要回到管理界面时，在浏览器地址栏打开 http://localhost:19833/ 即可
- 进入具体文档后，在「源文件」页签可用内置编辑器修改 `.md`/`.rst`/`.txt`，**保存并刷新渲染**；粘贴剪贴板中的截图可自动保存到源文件同目录并插入引用（详见 `docs/web-source-editor.md`）

### 扫描格式与忽略规则

- 受管理项目默认扫描并渲染：`.md`、`.rst`、`.txt`（txt 按段落简单渲染）。
- 可在项目根创建 `.nmrignore` 跳过目录或文件（gitignore 风格子集）：
  - `build/` 忽略目录及其下所有内容
  - `*.draft.md` 忽略匹配文件
  - `!notes/keep.txt` 反向包含（覆盖前面的忽略）

### 可移植性

每个被管理的目录下会生成 `.NoteManager/` 子目录，其中的 `html/` 目录是完整的可浏览站点。将整个 `.NoteManager/` 拷贝到任何有浏览器的地方都能使用，包括搜索功能。

拷贝后的页面若**不是**通过控制台 Web 的 `/p/<工程号>/…` URL 打开，「源文件」页签会自动变为**只读纯文本**（不调用保存 API，避免报错）；在线编辑仅在本机通过控制台进入工程页面时可用。

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
