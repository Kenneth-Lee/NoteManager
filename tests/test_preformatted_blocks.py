import unittest
from pathlib import Path

from notemanager.renderer.md_renderer import render_md
from notemanager.renderer.preformatted import is_ascii_diagram, mark_diagram_code_blocks


class TestPreformattedBlocks(unittest.TestCase):
    def test_is_ascii_diagram_detects_box_drawing(self):
        sample = """ Replica 侧                         Master 侧
 ┌─────────┐    请求/响应/数据     ┌─────────┐
 │ Replica │                      │ Master  │
 └─────────┘    响应/RDB/stream    └─────────┘"""
        self.assertTrue(is_ascii_diagram(sample))

    def test_is_ascii_diagram_rejects_short_snippet(self):
        self.assertFalse(is_ascii_diagram("print('hello')"))
        self.assertFalse(is_ascii_diagram("→ only arrow"))

    def test_mark_diagram_code_blocks_adds_class(self):
        html = (
            '<div class="highlight"><pre><span></span><code> Replica 侧\n'
            " ┌─────┐    请求/响应\n"
            " │ foo │\n"
            " └─────┘</code></pre></div>"
        )
        marked = mark_diagram_code_blocks(html)
        self.assertIn('class="highlight nm-diagram"', marked)

    def test_example_md_marks_all_diagram_blocks(self):
        content = Path(__file__).resolve().with_name("example.md").read_text(
            encoding="utf-8"
        )
        result = render_md(content, "example.md")
        self.assertEqual(result["body"].count("nm-diagram"), 4)


if __name__ == "__main__":
    unittest.main()
