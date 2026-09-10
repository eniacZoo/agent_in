#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan19.0：组合式 prompt + 五件套 md；工具表 7 个（plan21 加 glob/grep）。"""
import os
import sys
import unittest
from pathlib import Path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import loop
import tools


class Plan19Tests(unittest.TestCase):
    def test_five_skills_exist(self):
        root = (Path(_ROOT) / "skills")
        for name in ("xlsx", "docx", "pptx", "pdf", "网页"):
            self.assertTrue((root / (name + ".md")).is_file(), name)

    def test_old_names_redirect(self):
        root = (Path(_ROOT) / "skills")
        text = (root / "读xlsx.md").read_text(encoding="utf-8")
        self.assertIn("xlsx.md", text)

    def test_prompt_index(self):
        p = loop.DEFAULT_SYSTEM_PROMPT
        self.assertIn("xlsx.md", p)
        self.assertIn("docx.md", p)
        self.assertIn("pptx.md", p)
        self.assertIn("pdf.md", p)
        self.assertIn("网页.md", p)
        self.assertIn("task_notes.md", p)
        self.assertIn("一份脚本", p)

    def test_office_overlay(self):
        self.assertIn("思考保持简短", loop._OFFICE_DISCIPLINE)
        self.assertIn("立即停止", loop._OFFICE_DISCIPLINE)

    def test_tools_are_seven(self):
        names = [t["function"]["name"] for t in tools.TOOLS]
        self.assertEqual(
            names,
            ["read_file", "write_file", "edit_file", "shell", "view_image", "glob", "grep"],
        )


if __name__ == "__main__":
    unittest.main()
