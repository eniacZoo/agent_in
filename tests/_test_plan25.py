#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""shell 真写出才算产出；带上界的 max_column 放行；改脚本/失败后再跑不报重复。"""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import loop
import tools


class ProductMtimeTests(unittest.TestCase):
    def test_mention_existing_xlsx_is_not_write(self):
        td = tempfile.mkdtemp(prefix="p25_xlsx_")
        loop_wd = tools.WORK_DIR
        try:
            tools.WORK_DIR = td
            xlsx = os.path.join(td, "表.xlsx")
            Path(xlsx).write_text("x", encoding="utf-8")
            old = os.path.getmtime(xlsx)
            time.sleep(0.05)
            cmd = 'echo "%s"' % xlsx
            before = loop._snapshot_shell_products(cmd, td)
            self.assertIn(os.path.abspath(xlsx), before)
            self.assertEqual(before[os.path.abspath(xlsx)], old)
            self.assertFalse(loop._shell_wrote_product(cmd, xlsx, td, before=before))
        finally:
            tools.WORK_DIR = loop_wd
            shutil.rmtree(td, ignore_errors=True)

    def test_mtime_change_counts_as_write(self):
        td = tempfile.mkdtemp(prefix="p25_wr_")
        old_wd = tools.WORK_DIR
        try:
            tools.WORK_DIR = td
            xlsx = os.path.join(td, "out.xlsx")
            Path(xlsx).write_text("old", encoding="utf-8")
            cmd = 'echo "%s"' % xlsx
            before = loop._snapshot_shell_products(cmd, td)
            time.sleep(0.05)
            Path(xlsx).write_text("new", encoding="utf-8")
            self.assertTrue(loop._shell_wrote_product(cmd, "", td, before=before))
        finally:
            tools.WORK_DIR = old_wd
            shutil.rmtree(td, ignore_errors=True)

    def test_new_file_counts_as_write(self):
        td = tempfile.mkdtemp(prefix="p25_new_")
        old_wd = tools.WORK_DIR
        try:
            tools.WORK_DIR = td
            script = os.path.join(td, "temp", "mk.py")
            os.makedirs(os.path.join(td, "temp"), exist_ok=True)
            xlsx = os.path.join(td, "out.xlsx")
            Path(script).write_text(
                'open(r"%s", "w").write("x")\n' % xlsx.replace("\\", "\\\\"),
                encoding="utf-8",
            )
            cmd = 'python "%s"' % script
            before = loop._snapshot_shell_products(cmd, td)
            self.assertIsNone(before.get(os.path.abspath(xlsx)))
            Path(xlsx).write_text("x", encoding="utf-8")
            self.assertTrue(loop._shell_wrote_product(cmd, "", td, before=before))
        finally:
            tools.WORK_DIR = old_wd
            shutil.rmtree(td, ignore_errors=True)


class MaxColumnCapTests(unittest.TestCase):
    def _run(self, src):
        td = tempfile.mkdtemp(prefix="p25_col_")
        old_wd = tools.WORK_DIR
        tools.WORK_DIR = td
        tools.SAFE_MODE = True
        script = os.path.join(td, "s.py")
        Path(script).write_text(src, encoding="utf-8")
        try:
            return tools._reject_max_column_script('python "%s"' % script)
        finally:
            tools.WORK_DIR = old_wd
            shutil.rmtree(td, ignore_errors=True)

    def test_uncapped_still_rejected(self):
        r = self._run("for c in range(1, ws.max_column + 1):\n    pass\n")
        self.assertIsNotNone(r)
        self.assertTrue(r.startswith("Error"))
        self.assertIn("min(ws.max_column, 32)", r)

    def test_min_32_allowed(self):
        self.assertIsNone(self._run(
            "for c in range(1, min(ws.max_column, 32) + 1):\n    pass\n"
        ))

    def test_min_swapped_allowed(self):
        self.assertIsNone(self._run(
            "for c in range(1, min(32, ws.max_column)+1):\n    pass\n"
        ))

    def test_min_over_80_rejected(self):
        r = self._run("for c in range(1, min(ws.max_column, 100) + 1):\n    pass\n")
        self.assertIsNotNone(r)

    def test_helpers(self):
        self.assertTrue(tools._max_column_range_capped("range(1, min(ws.max_column, 32)+1)"))
        self.assertFalse(tools._max_column_range_capped("range(1, ws.max_column + 1)"))


class RepeatWarnTests(unittest.TestCase):
    def test_first_call_silent(self):
        self.assertFalse(loop.should_warn_tool_repeat(1))

    def test_same_ok_warns(self):
        self.assertTrue(loop.should_warn_tool_repeat(2, prev_ok=True, script_changed=False))

    def test_prev_error_silent(self):
        self.assertFalse(loop.should_warn_tool_repeat(2, prev_ok=False, script_changed=False))

    def test_script_changed_silent(self):
        self.assertFalse(loop.should_warn_tool_repeat(2, prev_ok=True, script_changed=True))

    def test_script_hash_changes(self):
        td = tempfile.mkdtemp(prefix="p25_hash_")
        old_wd = tools.WORK_DIR
        try:
            tools.WORK_DIR = td
            script = os.path.join(td, "temp", "hdr.py")
            os.makedirs(os.path.dirname(script), exist_ok=True)
            Path(script).write_text("print(1)\n", encoding="utf-8")
            cmd = 'python "%s"' % script
            a = loop._shell_script_hash(cmd, td)
            Path(script).write_text("print(2)\n", encoding="utf-8")
            b = loop._shell_script_hash(cmd, td)
            self.assertNotEqual(a, b)
        finally:
            tools.WORK_DIR = old_wd
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
