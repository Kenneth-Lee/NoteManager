# Web 源码编辑器与粘贴截图（设计说明）

## 目标

- 在工程内通过 nmrd Web 打开的笔记页面中，将「源文件」页签从只读高亮升级为**可编辑**。
- **保存**后写回磁盘上的 `.md` / `.rst`，并触发与文件监控一致的**增量重建**，刷新「渲染」页签内容。
- 离开源文件时若有未保存修改，对话框提供「保存并退出」「不保存退出」「取消」三种选择。
- 在编辑器内 **粘贴剪贴板中的截图**（`image/png` 等）时，将图片保存到**当前源文件目录的 `_static/`**，并在光标处插入对应引用（Markdown / reST）。
- 工具栏提供「粘贴图片」（读取剪贴板）和「导入资源」（本地文件/URL）入口。
- 文档页快捷键：`Ctrl/⌘+Alt+1/2/3` 切换渲染/源文件/诊断（源文件兼容 `Ctrl/⌘+Alt+C`），`Ctrl/⌘+Alt+S` 切换分屏，`Ctrl/⌘+Alt+E` 请求外部编辑器。
- 「重命名」修改当前源文件名并重建 HTML/索引；不会自动改写正文里的旧链接，引用旧路径时需要手动调整。
- 进入分屏后会自动聚焦源码编辑器，可以直接开始输入。
- 临时渲染页按设计是只读的，没有 CodeMirror；进入分屏会聚焦只读源码面板并提示只读，编辑请使用「外部编辑」。

## 约束与假设

- nmrd 的 HTTP 服务绑定 **`127.0.0.1`**，仅本机访问；不单独实现登录，依赖操作系统对本机端口的访问控制。
- 仅对**已纳入监控的工程**中、通过正常构建生成的页面开启编辑；**临时预览**（`/temp/...`，源路径为绝对路径）**不开启**写盘能力。
- 写入路径必须解析后仍位于该工程根目录之下，禁止 `..` 穿越。

## 架构

| 层级 | 职责 |
|------|------|
| 浏览器 | CodeMirror 5 编辑区；`paste` 拦截图片 → `multipart/form-data` 上传；保存 → `POST /api/save_source` JSON。 |
| `web.py` | 路由：`/api/save_source`、`/api/paste_image`；解析 multipart；调用 daemon。 |
| `daemon` | 校验工程索引与相对路径；写文件；调用 `builder.update_file` 触发重建。 |

保存成功后由前端 **`location.reload()`** 刷新整页，使「渲染」与侧栏索引与最新静态资源一致（实现简单，与现有 `build_complete` 行为兼容）。

从「渲染」切到「源文件」时，编辑器在先前为 `display:none` 的容器中初始化会导致布局宽度为 0，需在页签可见后调用 **`refresh()`**（见 `nm-editor.js` 的 `IntersectionObserver` 与页签点击回调）。离开「源文件」前往「渲染/诊断」且未保存时，应 **`confirm`** 以免误丢编辑。粘贴图片成功后除写入源码目录外，会 **`_copy_project_assets`** 同步到 `.NoteManager/html/`，便于保存前相对路径已指向磁盘上存在的文件；仍须 **保存源码** 后整页刷新，渲染区才会出现 `<img>`。

## 编辑器选型（轻量、无打包）

- 采用 **CodeMirror 5**（UMD 单文件 + mode 脚本），与现有 **KaTeX / highlight.js** 一样放入 `notemanager/renderer/assets/`，由 `builder._copy_static_assets` 复制到各工程 `_static/`。
- **未采用 CodeMirror 6**：其以 ESM 为主，在「无 bundler、纯静态 HTML」下集成成本更高。
- **Markdown**：`mode/markdown` + 依赖的 `mode/xml`。
- **reStructuredText**：无官方 CM5 高亮 mode，使用**无 mode** 的纯文本编辑（仍保留行号、主题、粘贴逻辑）。

主题选用 **dracula**，与原有深色「源文件」面板风格接近。

**Vim 键位（可选）**：通过工具栏「Vim 键位」复选框启用 CodeMirror 5 自带的 `keymap/vim` 子集（非完整 Neovim）；**默认关闭**，勾选后在本机 `localStorage` 记住偏好。依赖 `addon/dialog`、`addon/search/searchcursor` 等脚本，与主编辑器脚本一并由 `_static` 提供。启用 Vim 后约定：`:w` / `:write` 只写盘并留在源文件页签；`:q` / `:quit` 切到「渲染」并退出分屏，若有未保存修改会先确认；`:wq` / `:x` 保存后退出分屏。

Vim 模式提供与工具栏对应的命令：`:new`（新建笔记）、`:paste` / `:img`（粘贴图片）、`:import`（导入资源）、`:ref`（引用资源）、`:object` / `:editobject`（自动识别表格/公式，否则选择新建）、`:table`（编辑/插入表格）、`:math` / `:formula`（编辑/插入公式）、`:delete`（删除当前笔记）、`:rename` / `:mv`（重命名当前笔记）、`:external` / `:ext`（外部编辑器）、`:split`（分屏）、`:sync`（同步滚动）、`:help` / `:vimhelp`（显示命令帮助）。

## API 设计

### `POST /api/save_source`

`Content-Type: application/json`

```json
{
  "project": 0,
  "source_rel_path": "notes/foo.md",
  "content": "……全文……"
}
```

- `project`：与 URL `/p/{project}/...` 一致。
- `source_rel_path`：相对工程根的正斜杠路径，后缀须为 `.md` 或 `.rst`。

成功：`{"ok": true}`；失败：`{"ok": false, "error": "…"}`。

### `POST /api/paste_image`

`Content-Type: multipart/form-data`

字段：

- `project`：字符串或数字，工程索引。
- `source_rel_path`：当前正在编辑的源文件相对路径（用于确定**保存目录** = 该文件所在目录）。
- `file`：二进制图片。

服务端根据 `Content-Type` 限制为 `image/png`、`image/jpeg`、`image/webp`、`image/gif`，大小上限（如 8MiB）。文件名采用 `nm-paste-{时间戳}-{随机}.{ext}`，避免覆盖。

响应示例：

```json
{
  "ok": true,
  "insert_markdown": "![](nm-paste-20260202-120000-a1b2c3d4.png)",
  "insert_rst": ".. image:: nm-paste-20260202-120000-a1b2c3d4.png"
}
```

插入串为**相对于源文件所在目录**的相对路径（POSIX），图片统一为 `_static/...`。

## 粘贴行为说明

- 仅当剪贴板项含 **`image/*`** 时拦截默认粘贴并上传；普通文本/代码粘贴仍走 CodeMirror 默认逻辑。
- 上传成功后**只插入引用行**，不自动调用「保存」：用户可继续编辑后一次保存，减少半成稿状态。
- 图片先落盘；若用户未保存源码，可能出现「磁盘上有图、文中未引用」的孤立文件，属可接受的简化行为。

## 与现有构建链的关系

- `update_file` 会执行 `_copy_project_assets`，故 `_static/` 下新生成的图片会复制到 `html/` 输出树，渲染页可引用。
- 「导入资源」支持本地文件和 HTTP(S) URL；非图片文件插入为普通链接，单文件最大 32 MB，HTML/JS/SVG 等类型会被拒绝。

## 未保存离开：页签切换与刷新/关闭

- 切换到「渲染 / 诊断」时由 **`confirm`** 拦截（捕获阶段）。
- **刷新、关闭标签页**由 **`beforeunload`** 提示（现代浏览器只显示通用文案，无法自定义句子）；有未保存修改时（`__nmDirty`）会触发。

## 保存耗时（瓶颈说明）

每次保存仍会执行 **`update_file` 的完整链路**：扫描工程、**全量** `extract_labels` / dtags / `.. index::`、为**每个**已生成 HTML 读盘并 **`build_index`**、写 `dtag_index`、更新首页与 `status.json` 等。工程越大，这部分越占时间。

Web 保存路径已设 **`refresh_static_assets=False`**，跳过每次把 **整个 `_static`（含 CodeMirror 等大目录）** 再拷入 `html`，通常能明显减少秒级等待；其余全量索引逻辑若需再缩短，需要增量索引等更大改造。

## 拷贝 `html` 目录离线浏览（无 `/p/…`）

承诺「整份 `html` 可拷走分享」时，页面 URL **不再是** `http://127.0.0.1:<端口>/p/<工程号>/...`，浏览器 **`pathname`** 无法匹配 `/p/\\d+/`。

`nm-editor.js` 在此情况下 **不初始化 CodeMirror**，隐藏保存工具栏，仅用内嵌 JSON 中的源码渲染 **`<pre>` 只读** 并显示简短说明；**不请求** `/api/save_source`，避免因 404/跨域产生未捕获异常。侧栏搜索等仍依赖 `_index/search_index.json` 的相对路径；若用 `file://` 打开，部分浏览器对 `fetch` 有限制，属静态站点通用限制。

## 后续可扩展（非本期范围）

- 冲突检测（mtime / 版本号）。
- 自动压缩大图、WebP 转换。
- 仅 `localhost` 以外的部署时增加认证或 CSRF 防护。
