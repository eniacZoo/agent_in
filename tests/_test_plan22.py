#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan22：分析草稿不误刹 + 桌面产物可确认写入。"""
import os
import sys
import tempfile
import time
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import loop
import tool_guard
import tools


class Plan22StallTests(unittest.TestCase):
    def test_temp_is_scratch_not_product(self):
        wd = tempfile.mkdtemp(prefix="p22_wd_")
        try:
            self.assertTrue(loop._is_temp_path("temp/analyze.py", wd))
            self.assertFalse(loop._is_product_write("temp/analyze.py", wd))
            self.assertTrue(loop._is_product_write("report.html", wd))
            desk = os.path.join(os.path.expanduser("~"), "Desktop", "报告", "a.html")
            self.assertTrue(loop._is_product_write(desk, wd))
            self.assertFalse(loop._is_temp_path(desk, wd))
        finally:
            pass

    def test_scratch_does_not_hard_stop(self):
        loop_py = os.path.join(_ROOT, "loop.py")
        with open(loop_py, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("连续 40 轮只在 temp/", src)
        self.assertIn("还在 temp/ 里打转", src)
        self.assertTrue(loop.should_stall_stop(True, 12))
        self.assertFalse(loop.should_stall_stop(False, 40))

    def test_shell_recent_html_counts(self):
        wd = tempfile.mkdtemp(prefix="p22_wd_")
        out = os.path.join(os.path.dirname(wd), "p22_report.html")
        old = os.path.join(os.path.dirname(wd), "p22_old.xlsx")
        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write("<html></html>")
            self.assertTrue(loop._shell_wrote_product(
                "python temp/gen_html.py", "OK " + out + " 100 bytes", wd))
            with open(old, "w", encoding="utf-8") as f:
                f.write("x")
            os.utime(old, (time.time() - 3600, time.time() - 3600))
            self.assertFalse(loop._shell_wrote_product(
                "python temp/probe.py", old, wd))
        finally:
            for p in (out, old):
                if os.path.exists(p):
                    os.remove(p)

    def test_outside_write_is_confirm_not_block(self):
        wd = tempfile.mkdtemp(prefix="p22_wd_")
        desk = os.path.join(os.path.dirname(wd), "desktop_report.html")
        v = tool_guard.assess_write(desk, wd, safe_mode=True)
        self.assertEqual(v["action"], tool_guard.ACTION_CONFIRM)
        self.assertIn("outside_workdir", v["findings"])
        v2 = tool_guard.assess_write(r"C:\Windows\notepad.exe", wd, safe_mode=True)
        self.assertEqual(v2["action"], tool_guard.ACTION_BLOCK)

    def test_trim_leak_rejected(self):
        wd = tempfile.mkdtemp(prefix="p22_wd_")
        tools.WORK_DIR = wd
        tools.SAFE_MODE = True
        bad = "x = 1\n" + "…(trimmed 1318 chars，需要时 read_file 重取)\n"
        r = tools.execute(
            "write_file", {"path": "temp/build.py", "content": bad},
            confirm_fn=lambda _p: True,
        )
        self.assertTrue(r.startswith("Error:"), r)
        self.assertIn("trimmed", r)
        self.assertFalse(os.path.isfile(os.path.join(wd, "temp", "build.py")))


if __name__ == "__main__":
    unittest.main()
