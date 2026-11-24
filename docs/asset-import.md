# 粘贴图片 / 导入本地文件 / 导入 URL

状态：已实现
日期：2026-11-24
范围：Web 源文件编辑器的资源导入

## 1. 按钮

源文件工具栏新增：

- **粘贴图片**：读取剪贴板中的图片，保存到当前笔记目录的 `_static/`，并插入图片语法；
- **导入资源**：打开导入弹窗，可选择本地文件或填写 URL。

原来的编辑区 Ctrl+V 粘贴图片仍然可用。

## 2. 保存位置

源文件：

```text
学习笔记/foo.md
```

资源保存到：

```text
学习笔记/_static/
```

图片插入：

```markdown
![]( _static/name.png)
```

reST：

```rst
.. image:: _static/name.png
```

非图片文件插入为链接：

```markdown
[report.pdf](_static/report.pdf)
```

```rst
`report.pdf <_static/report.pdf>`_
```

## 3. 文件名与安全

- 保留原文件名，冲突时追加时间戳和随机串；
- 清理路径分隔符和特殊字符；
- 单文件最大 32 MB；
- 禁止导入 `.html`、`.htm`、`.xhtml`、`.js`、`.mjs`、`.svg`，避免同源脚本/HTML 风险；
- 资源仍然参与 `_index/assets.json` 引用索引和清理逻辑。

## 4. URL 导入

URL 由 daemon 下载，再按本地文件规则保存：

- 只支持 `http` / `https`；
- 超时 20 秒；
- 最大 32 MB；
- 文件名优先取 `Content-Disposition`，否则取 URL 路径；
- 没有扩展名时尝试按 Content-Type 推断。

## 5. API

```text
POST /api/import_asset   (multipart: project, source_rel_path, file)
POST /api/import_url     (JSON: project, source_rel_path, url)
```

## 6. 限制

- URL 下载不校验内网/外网地址，属于本地可信工具行为；
- 不导入 HTML/JS/SVG；
- 不做图片压缩或格式转换；
- 导入的资源在保存源文件并重建后进入引用索引；未引用时会出现在「资源」清理中。
