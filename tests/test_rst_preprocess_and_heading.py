import re
import unittest
from pathlib import Path


from notemanager.renderer import rst_renderer


def _is_rst_adornment_line(line: str) -> bool:
    """
    A "separator/adornment" line in RST: a whole line consisting of the same
    repeated *non-alphanumeric* character (length >= 2).
    """
    s = line.strip()
    if len(s) < 2:
        return False
    if s[0].isalnum():
        return False
    return all(ch == s[0] for ch in s)


def _strip_leading_section_number(text: str) -> str:
    # Match the same pattern used elsewhere: "1. " / "8.1. " etc.
    return re.sub(r"^\d+(?:\.\d+)*\.\s*", "", text).strip()


class TestRstPreprocessAndHeading(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parents[1]
        cls.alloy_path = (
            cls.repo_root
            / "test_projects"
            / "MySummary.Prv"
            / "学习笔记"
            / "alloy.rst"
        )
        if not cls.alloy_path.is_file():
            raise FileNotFoundError(f"Missing alloy.rst: {cls.alloy_path}")
        cls.source = cls.alloy_path.read_text(encoding="utf-8", errors="replace")

    def test_preprocess_does_not_modify_adornment_lines(self) -> None:
        processed, _ignored = rst_renderer._strip_unsupported_roles(self.source)

        src_lines = self.source.splitlines()
        proc_lines = processed.splitlines()
        self.assertEqual(
            len(src_lines),
            len(proc_lines),
            "Preprocess changed line count; unexpected for heading parsing debugging.",
        )

        # Compare all "pure adornment" lines.
        for i, line in enumerate(src_lines):
            if _is_rst_adornment_line(line):
                self.assertEqual(
                    proc_lines[i].strip(),
                    line.strip(),
                    f"Adornment line changed at L{i+1}: {line!r} -> {proc_lines[i]!r}",
                )

        # Additionally, compare the first '*' adornment title + underline neighborhood.
        src_lines = self.source.splitlines()
        star_idx = None
        for i, line in enumerate(src_lines):
            s = line.strip()
            if s and s[0] == "*" and _is_rst_adornment_line(line):
                star_idx = i
                break
        self.assertIsNotNone(star_idx, "Did not find a '*' adornment line in alloy.rst.")
        self.assertGreater(star_idx, 0, "Star adornment line is at top; cannot check title line.")
        self.assertEqual(
            proc_lines[star_idx - 1].strip(),
            src_lines[star_idx - 1].strip(),
            "Title line above '*' adornment changed after preprocess.",
        )
        self.assertEqual(
            proc_lines[star_idx].strip(),
            src_lines[star_idx].strip(),
            "The '*' adornment underline line changed after preprocess.",
        )

    def test_render_first_star_title_should_be_h1(self) -> None:
        """
        Desired behavior for the issue: the first title under '*' underline should be treated as
        the document-level header (h1). Currently we expect this to fail until we adapt to
        docutils' behavior.
        """
        processed, _ignored = rst_renderer._strip_unsupported_roles(self.source)
        rendered = rst_renderer.render_rst(processed, str(self.alloy_path))
        body = rendered["body"]

        # Extract headings as our renderer does.
        headings = rst_renderer._extract_headings(body)

        src_lines = self.source.splitlines()
        star_idx = None
        for i, line in enumerate(src_lines):
            if line.strip().startswith("*") and _is_rst_adornment_line(line):
                star_idx = i
                break
        self.assertIsNotNone(star_idx, "Did not find '*' adornment line in alloy.rst.")
        self.assertGreater(star_idx, 0)
        expected_title = src_lines[star_idx - 1].strip()

        normalized = [
            {**h, "norm_text": _strip_leading_section_number(h["text"])} for h in headings
        ]
        matches = [h for h in normalized if h["norm_text"] == expected_title]
        self.assertTrue(matches, f"No rendered heading matched expected title {expected_title!r}")

        # We expect h1 level == 1 after all mapping.
        self.assertEqual(matches[0]["level"], 1)


if __name__ == "__main__":
    unittest.main()

