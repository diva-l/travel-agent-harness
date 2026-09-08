from __future__ import annotations

import unittest

from travel_agent_harness.markdown import json2md, truncate_text


class MarkdownTests(unittest.TestCase):
    def test_json2md_renders_headings_and_values(self):
        md = json2md({"city": "杭州", "casts": [{"date": "2026-04-16", "dayweather": "晴"}]})
        self.assertIn("city: 杭州", md)
        self.assertIn("## Casts", md)
        self.assertIn("date: 2026-04-16", md)

    def test_truncate_text_keeps_head_and_tail(self):
        text = "头" * 100 + " " + "中" * 4800 + " " + "尾" * 100
        out = truncate_text(text, 500)
        self.assertTrue(out.startswith("头"))
        self.assertTrue(out.endswith("尾" * 100))
        self.assertIn("内容已截断", out)

    def test_truncate_text_short_passthrough(self):
        self.assertEqual("abc", truncate_text("abc", 500))


if __name__ == "__main__":
    unittest.main()
