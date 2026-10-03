#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan7.0 验收：Windows 规则、SAFE_MODE、覆盖确认、工具表钉死。零依赖 unittest。"""
import os
import sys
import tempfile
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import config
import tool_guard
import tools


class Plan7GuardTests(unittest.TestCase):
    def test_safe_mode_default_true(self):
        cfg = config.load(force_reload=True)
        self.assertTrue(cfg.get("safe_mode"))

    def test_tools_fixed_seven(self):
        names = [t["function"]["name"] for t in tools.TOOLS]
        self.assertEqual(
            names,
            ["read_file", "write_file", "edit_file", "shell", "view_image", "glob", "grep",
             "python", "job", "todo_write", "ask_user"],
        )
        self.assertFalse(any(n.startswith("skill_") for n in names))

    def test_remove_item_recurse_high(self):
        v = tool_guard.assess_shell("Remove-Item -Recurse -Force tmp", safe_mode=False)
        self.assertIn("ps_rm_recurse", " ".join(v["findings"]))
        self.assertEqual(v["action"], tool_guard.ACTION_STRONG_CONFIRM)
        v2 = tool_guard.assess_shell("Remove-Item -Recurse -Force tmp", safe_mode=True)
        self.assertEqual(v2["action"], tool_guard.ACTION_BLOCK)

    def test_windows_system_write_blocked(self):
        wd = tempfile.mkdtemp(prefix="agent_in_wd_")
        v = tool_guard.assess_write(r"C:\Windows\notepad.exe", wd, safe_mode=True)
        self.assertEqual(v["action"], tool_guard.ACTION_BLOCK)

    def test_new_write_allow_overwrite_confirm(self):
        wd = tempfile.mkdtemp(prefix="agent_in_wd_")
        tools.WORK_DIR = wd
        tools.SAFE_MODE = True
        newp = os.path.join(wd, "n.txt")
        r = tools.execute(
            "write_file", {"path": "n.txt", "content": "a"},
            confirm_fn=lambda _p: False,
        )
        self.assertTrue(r.startswith("OK:"), r)
        self.assertTrue(os.path.isfile(newp))
        r2 = tools.execute(
            "write_file", {"path": "n.txt", "content": "b"},
            confirm_fn=lambda _p: False,
        )
        self.assertIn("拒绝", r2)
        with open(newp, encoding="utf-8") as f:
            self.assertEqual(f.read(), "a")

    def test_outside_write_confirms(self):
        wd = tempfile.mkdtemp(prefix="agent_in_wd_")
        tools.WORK_DIR = wd
        tools.SAFE_MODE = True
        outside = os.path.join(os.path.dirname(wd), "agent_in_outside_plan7.txt")
        try:
            r = tools.execute(
                "write_file", {"path": outside, "content": "x"},
                confirm_fn=lambda _p: False,
            )
            self.assertIn("拒绝", r)
            self.assertFalse(os.path.exists(outside))
            r2 = tools.execute(
                "write_file", {"path": outside, "content": "x"},
                confirm_fn=lambda _p: True,
            )
            self.assertTrue(r2.startswith("OK:"), r2)
            self.assertTrue(os.path.isfile(outside))
        finally:
            if os.path.exists(outside):
                os.remove(outside)

    def test_multi_replace_confirm(self):
        wd = tempfile.mkdtemp(prefix="agent_in_wd_")
        tools.WORK_DIR = wd
        tools.SAFE_MODE = True
        p = os.path.join(wd, "m.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("aa aa")
        r = tools.execute(
            "edit_file",
            {"path": "m.txt", "old_text": "aa", "new_text": "bb"},
            confirm_fn=lambda _p: False,
        )
        self.assertIn("Error", r)
        with open(p, encoding="utf-8") as f:
            self.assertEqual(f.read(), "aa aa")
        r2 = tools.execute(
            "edit_file",
            {"path": "m.txt", "old_text": "aa", "new_text": "bb", "replace_all": True},
            confirm_fn=lambda _p: False,
        )
        self.assertIn("拒绝", r2)
        with open(p, encoding="utf-8") as f:
            self.assertEqual(f.read(), "aa aa")

    def test_no_created_lie_in_source(self):
        src = os.path.join(_ROOT, "tools.py")
        with open(src, encoding="utf-8") as f:
            self.assertNotIn("Created.", f.read())


if __name__ == "__main__":
    unittest.main()
