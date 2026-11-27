# 文档关系 / 反向链接设计

状态：已实现（Phase 1）
日期：2026-11-23
范围：每篇文档页底部的「文档关系」面板

## 1. 目标

- 显示「同目录」「被引用」「引用」「相关文档」；
- 标记没有任何内部链接的「孤立文档」；
- 完全基于现有链接、标签、章节标题和文本词项，不依赖 LLM；
- 增量更新后自动刷新。

## 2. 数据生成

新增 `relations.py`：

- 读取 `_index/search_index.json` 和已经渲染的 HTML；
- 解析内部 `<a href>`，得到文档出链/入链；
- 从 `dtag`、`chapter`、`index`、`grep` 条目提取标签、标题、索引词和摘录；
- 计算相关度：

```text
相关度 = 共享标签 * 5
       + 共享章节标题 * 2
       + 共享索引词 * 3
       + 直接链接 * 3
       + 词项余弦相似度 * 8
```

- 每篇文档最多保留 5 篇相关文档；
- 被引用/引用列表最多 8 条，同时保留总数；
- 没有入链也没有出链的文档标记为孤立文档。

输出 `_index/relations.json`：

```json
{
  "version": 1,
  "docs": {
    "a/one.html": {
      "title": "One",
      "backlinks": [...],
      "outgoing": [...],
      "related": [...],
      "backlink_count": 1,
      "outgoing_count": 1,
      "related_count": 2,
      "orphan": false
    }
  },
  "orphans": [...]
}
```

## 3. 页面集成

- `wrap_html` 在渲染面板末尾放一个空的 `#nm-related` 容器；
- 容器带 `data-nm-related-src`、`data-nm-related-path`、`data-nm-related-prefix`；
- `_static/nm-relations.js` 根据当前页面路径 fetch `relations.json` 并渲染；
- 页面路径、标题、摘录全部通过 `textContent` 写入，避免注入；
- fetch 失败时静默隐藏，不影响文档正文；
- 不修改源文件，不新增 nmr 命令。

## 4. 构建接入

- `full_build()`、`update_file()`、`remove_file()` 在搜索索引生成后调用
  `relations.build_and_write()`；
- 生成失败只记录日志，不阻断构建；
- 关系数据由所有页面共享，因此链接变化后不需要重写所有 HTML。

## 5. 测试

- 入链、出链、同目录笔记和相关文档；
- 孤立文档；
- 全量构建写出 `relations.json`，文档页含关系容器和脚本；
- 页面通过本地 HTTP 服务加载并显示关系面板；
- 既有测试不回归。

## 6. 限制

- 关系面板依赖 fetch；直接以 `file://` 打开时可能被浏览器阻止，此时面板隐藏，
  正文和其他功能仍可用；
- 相关度是启发式，标签/链接稀疏时可能出现空列表；
- 每个列表最多显示 8 条，页面只显示总数；
- 孤立文档目前只在面板中提示，尚未做独立的健康检查页面。
