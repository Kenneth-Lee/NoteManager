from pathlib import Path

from notemanager.indexer import extract_dtags_from_sources
from notemanager.renderer.md_renderer import render_md
from notemanager.renderer.rst_renderer import render_rst
from notemanager.renderer.txt_renderer import render_txt
from notemanager.scanner import FileInfo


def test_txt_preserves_text_except_comments():
    source = (
        "  保留前导空格\n"
        "第二行  \n"
        "nMr: 这是注释 #标签一, 标签二# #另一个#\n"
        "\n"
        "空行后\n"
    )
    result = render_txt(source)
    body = result["body"]
    assert 'pre class="nm-plaintext"' in body
    assert "  保留前导空格" in body
    assert "第二行  " in body
    assert "这是注释" not in body
    assert "空行后" in body
    assert result["dtags"] == ["标签一", "标签二", "另一个"]
    assert any("TXT 注释" in w for w in result["warnings"])


def test_txt_dtags_reach_source_indexer(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("nMr: #alpha, beta#\ntext\n", encoding="utf-8")
    fi = FileInfo(path, Path("a.txt"), ".txt", path.stat().st_mtime, path.stat().st_size)
    tags = extract_dtags_from_sources([fi])
    assert tags["a.txt"] == ["alpha", "beta"]


def test_unknown_rst_directive_is_reported():
    result = render_rst(".. unknown-directive::\n\n   content\n")
    assert any("Unknown directive type" in w for w in result["warnings"])


def test_markdown_cross_document_anchor_is_converted():
    body = render_md("[x](other.md#section)\n", "note.md")["body"]
    assert 'href="other.html#section"' in body


def test_markdown_yaml_frontmatter_tags_and_diagnostics():
    source = (
        "---\n"
        "tags: [Linux, 内核]\n"
        "title: Should be metadata\n"
        "---\n\n"
        "# 正文\n\n"
        "内容 #inline\n"
    )
    result = render_md(source, "a.md")
    assert result["body"].lstrip().startswith("<h1")
    assert "Should be metadata" not in result["body"]
    assert result["dtags"] == ["Linux", "内核", "inline"]
    assert any("YAML 元数据" in w and "title" in w for w in result["warnings"])


def test_txt_yaml_frontmatter_tags_and_preserved_body():
    source = (
        "---\n"
        "tags:\n"
        "  - alpha\n"
        "  - beta\n"
        "author: Me\n"
        "---\n"
        "\n"
        "  保留缩进\n"
        "nMr: 注释 #x, y#\n"
    )
    result = render_txt(source)
    assert "  保留缩进" in result["body"]
    assert "作者" not in result["body"]
    assert result["dtags"] == ["alpha", "beta", "x", "y"]
    assert any("YAML 元数据" in w for w in result["warnings"])


def test_txt_yaml_tags_reach_source_indexer(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("---\ntags: [one, two]\n---\n\ntext\n", encoding="utf-8")
    fi = FileInfo(path, Path("a.txt"), ".txt", path.stat().st_mtime, path.stat().st_size)
    assert extract_dtags_from_sources([fi])["a.txt"] == ["one", "two"]
