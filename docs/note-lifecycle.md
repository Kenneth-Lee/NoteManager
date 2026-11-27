# 目录级新建 / 删除笔记（P0）

状态：已实现（P0；P1 扩展了图片清理和回收站恢复）
日期：2026-11-24
范围：项目首页目录树中的新建和删除操作

## 1. 目标

- 不进入源文件面板也能在指定目录新建笔记；
- 从目录树直接删除笔记；
- 删除默认进入工程回收站，避免误删；
- 删除前显示背链和图片引用影响；
- 图片清理在 P1 中实现，见 `asset-cleanup.md`。

## 2. 新建笔记

入口：

- 侧边栏顶部「＋ 新建」：在**当前文档所在目录**新建；项目首页没有当前文档时在根目录新建；
- 每个目录摘要右侧的 `+`：在该目录新建；
- 新建弹窗包含：目标目录、文件名、类型（Markdown / reST）。

后端：

```text
POST /api/create_note_in_dir
{
  "project": 0,
  "dir_rel_path": "sub",
  "filename": "new note",
  "source_ext": ".md"
}
```

- 目录必须存在，且位于工程根目录内；
- 拒绝 `..`、绝对路径和 `.NoteManager`；
- 文件名不允许路径分隔符和非法字符；
- 已存在同名文件时拒绝；
- 创建后执行 `update_file`，返回 `source_rel_path` / `html_rel_path`；
- 前端创建成功后跳转到新 HTML。

原有的 `/api/create_note`（当前文档同目录）保留。

## 3. 删除笔记

入口：

- 文档页右上角的「删除」按钮；
- 首页目录树文件行右侧的 `×`；
- 源文件面板的「删除当前笔记」（原有入口，行为改为回收站）。

文档页删除完成后会回到项目首页，并自动把目录关系图定位到该文档所在目录
（`index.html#nm-dir=<目录>`），避免删除后重新在根目录里找上下文。

删除前调用：

```text
POST /api/note_delete_info
{ "project": 0, "source_rel_path": "sub/note.md" }
```

返回：

- 背链数量和前几条背链；
- 源码中引用的图片数量和前几条图片；
- 标题、HTML 路径。

删除时：

```text
POST /api/delete_note
```

P0 最初使用：

```text
.NoteManager/trash/<YYYYmmdd-HHMMSS>/<原相对路径>
```

P1 改为带 meta 的批次目录：

```text
.NoteManager/trash/<batch>/
  meta.json
  source/<原相对路径>
  assets/<图片相对路径>
```

旧格式仍然可以列出和恢复。

随后执行 `remove_file`，删除 HTML 并重建索引、关系、activity 和首页。

如果索引更新失败，会尝试把文件从回收站移回原位置。

删除/创建期间前端会设置 `window.__nmSuppressBuildReload`，避免 SSE 的
`build_complete` 事件抢先刷新当前页面。删除完成后由前端主动跳转到项目首页
（并定位原目录），不会刷新已经删除的文档 URL。

## 4. 图片

P1 已增加 `_index/assets.json` 图片引用索引。删除弹窗会区分：

- 独占图片：只被本文引用，可以随笔记一起清理（默认勾选）；
- 共享图片：仍被其他文档引用，永远不会自动删除。

P1 的详情见 `asset-cleanup.md`。

## 5. 限制

- 回收站已有 Web 列表和恢复入口（见 P1）；
- 不删除空目录；
- 不支持重命名、移动、批量操作；
- 新建笔记内容仍是内置模板；
- 目录树操作只在项目首页出现；根目录「＋ 新建」在所有工程页面可用；
- 独立导出 HTML 没有 `/p/N/` Web 上下文，不提供这些操作。

## 6. 测试

- 首页目录树包含 `+` 和 `×` 操作；
- `create_note_in_dir` 在指定目录创建并生成 HTML；
- `note_delete_info` 返回影响信息；
- 删除后源文件和 HTML 消失，源文件出现在 `.NoteManager/trash/` 批次中；
- 既有测试不回归。
