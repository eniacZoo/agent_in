#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan12.0：shell 带 vendor、禁 pip install、办公流程 md 可发现。"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import commands
import loop
import tool_guard
import tools
import usage


class Plan12Tests(unittest.TestCase):
    def setUp(self):
        tool_guard.load_shell_rules.cache_clear()
        tools.WORK_DIR = os.path.dirname(os.path.abspath(__file__))
        tools.SAFE_MODE = True

    def test_shell_env_puts_vendor_first(self):
        env = tools._shell_env()
        vendor = tools._vendor_dir()
        self.assertTrue(env["PYTHONPATH"].split(os.pathsep)[0] == vendor)
        self.assertEqual(env.get("PYTHONIOENCODING"), "utf-8")

    def test_exec_shell_imports_pptx(self):
        r = tools._exec_shell({
            "command": 'python -c "import pptx; print(pptx.__version__)"',
        })
        self.assertNotIn("ModuleNotFoundError", r)
        self.assertNotIn("[exit code:", r)
        self.assertRegex(r, r"\d+\.\d+")

    def test_pip_install_blocked_safe_mode(self):
        v = tool_guard.assess_shell("pip install python-pptx --quiet", safe_mode=True)
        self.assertIn("pkg_install", " ".join(v["findings"]))
        self.assertEqual(v["action"], tool_guard.ACTION_BLOCK)
        self.assertIn("vendor", v["reason"])
        v2 = tool_guard.assess_shell("python -m pip install foo", safe_mode=True)
        self.assertEqual(v2["action"], tool_guard.ACTION_BLOCK)
        v3 = tool_guard.assess_shell("pip list", safe_mode=True)
        self.assertEqual(v3["action"], tool_guard.ACTION_ALLOW)
        r = tools.execute(
            "shell", {"command": "pip install python-pptx"},
            confirm_fn=lambda _p: True,
        )
        self.assertIn("拒绝", r)
        self.assertIn("vendor", r)

    def test_office_markdown_listed(self):
        root = Path(__file__).with_name("skills")
        for name in ("读pptx", "读xlsx", "读docx", "读pdf"):
            self.assertTrue((root / (name + ".md")).is_file(), name)
        buf = io.StringIO()
        st = commands.CliState(".", None, "deadbeef", [], 0, 0, 0, usage.Tracker("deadbeef"))
        with redirect_stdout(buf):
            self.assertTrue(commands.handle("/ls skills", st))
        text = buf.getvalue()
        self.assertIn("xlsx", text)
        self.assertIn("docx", text)
        self.assertIn("pptx", text)
        self.assertIn("pdf", text)
        self.assertIn("周报转docx", text)

    def test_system_prompt_indexes_pptx_skill(self):
        self.assertIn("xlsx.md", loop.DEFAULT_SYSTEM_PROMPT)
        self.assertIn("禁止 pip", loop.DEFAULT_SYSTEM_PROMPT)

    def test_tools_still_five(self):
        names = [t["function"]["name"] for t in tools.TOOLS]
        self.assertEqual(
            names,
            ["read_file", "write_file", "edit_file", "shell", "view_image"],
        )


if __name__ == "__main__":
    unittest.main()
