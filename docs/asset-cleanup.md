# 图片资源索引 / 独占图片清理 / 回收站恢复（P1）

状态：已实现（P1）
日期：2026-11-24
范围：图片引用索引、删除时的独占图片处理、回收站列表与恢复

## 1. 图片引用索引

构建时新增：

```text
_index/assets.json
```

结构：

```json
{
  "version": 1,
  "assets": {
    "sub/img.png": {
      "path": "sub/img.png",
      "exists": true,
      "size": 12345,
      "referenced_by": [
        {
          "source_path": "sub/note.md",
          "html_path": "sub/note.html",
          "title": "note"
        }
      ]
    }
  },
  "orphans": ["old.png"],
  "missing": ["missing.png"]
}
```

支持 Markdown 图片、HTML `<img src>` 和 reST `.. image::` / `.. figure::`。
外部 URL、data URL、绝对路径和工程外路径会被忽略。

`_build_assets_safe` 在 `full_build`、`update_file`、`remove_file` 中调用。
资源索引失败只记录日志，不影响构建。

## 2. 删除时的图片判断

删除确认信息额外返回：

- 本文引用图片数；
- 独占图片数（只被本文引用）；
- 共享图片数；
- 对应的图片路径。

删除弹窗中：

- 有独占图片时显示勾选项：「同时删除仅被本文引用的图片（N 张）」；
- 默认勾选；
- 共享图片永远不会被自动删除；
- 取消勾选则只删除笔记，图片保留为孤儿资源，等待后续清理。

后端在删除时重新读取 `assets.json` 校验，只移动确实为独占且存在的图片。
图片和笔记放在同一个回收站批次中，便于一起恢复。

## 3. 回收站结构

新格式：

```text
.NoteManager/trash/<batch-id>/
  meta.json
  source/<原相对路径>
  assets/<图片相对路径>
```

`meta.json` 记录源文件、HTML 路径、一起删除的图片和时间。
删除后生成的 HTML 图片副本也会删除，避免站点里残留不可见资源。

兼容旧格式：

```text
.NoteManager/trash/<timestamp>/<原相对路径>
```

旧格式没有 meta，列表时根据文件推断源文件；恢复时只恢复源文件。

## 4. 回收站 UI / API

侧边栏首页区域增加「回收站」按钮，打开列表：

- 标题、源路径、删除时间、图片数量、大小；
- 「恢复」按钮；
- 恢复时如果目标文件/图片已存在，拒绝覆盖并提示；
- 恢复后重建页面和索引。

API：

```text
POST /api/trash_list
{ "project": 0 }

POST /api/trash_restore
{ "project": 0, "trash_id": "20261124-110000" }
```

删除 API 增加可选字段：

```json
{ "delete_assets": true }
```

## 5. 当前限制

- 孤儿图片已经写入 `assets.json`，但还没有独立的“清理未引用图片”页面；
- 还没有自动按笔记建立附件目录（例如 `foo.assets/`），粘贴图片仍保存到源文件同目录；
- 回收站没有永久删除/自动清理策略；
- 只识别图片扩展名，不处理 PDF、视频等附件；
- 恢复不覆盖已有文件，遇到冲突需要手动处理。

## 6. 测试

- 图片引用索引区分独占/共享；
- 删除时只移动独占图片，共享图片保留；
- 生成 HTML 中的独占图片副本被删除；
- 回收站列表显示批次；
- 恢复笔记和独占图片，并重新生成 HTML；
- 旧格式回收站条目可列出和恢复；
- 既有测试不回归。
