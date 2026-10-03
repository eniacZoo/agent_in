#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确认空回车再问、读项目源码不计入无产出、同轮 read_file 复用结果。"""
import builtins
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import approval
import loop
import ui


class ConfirmEmptyRetryTests(unittest.TestCase):
    def test_empty_then_y_accepts(self):
        answers = ["", "y"]

        def fake(prompt=""):
            return answers.pop(0)

        with patch.object(builtins, "input", fake):
            self.assertTrue(ui.confirm("覆盖已有文件"))
        self.assertEqual(ui.LAST_CONFIRM_RAW, "y")
        self.assertEqual(ui.confirm.last_answer, "y")

    def test_empty_then_empty_rejects(self):
        answers = ["", ""]

        def fake(prompt=""):
            return answers.pop(0)

        with patch.object(builtins, "input", fake):
            self.assertFalse(ui.confirm("覆盖已有文件"))
        self.assertEqual(ui.LAST_CONFIRM_RAW, "")

    def test_n_rejects_once(self):
        with patch.object(builtins, "input", lambda p="": "n"):
            self.assertFalse(ui.confirm("覆盖"))
        self.assertEqual(ui.LAST_CONFIRM_RAW, "n")

    def test_audit_keeps_raw_answer(self):
        recorded = []

        def fake_log(**kw):
            recorded.append(kw)

        def yes(_p):
            yes.last_answer = "Y"
            return True

        with patch.object(approval.audit, "log_security", fake_log):
            r = approval.resolve(
                {"action": "confirm", "severity": "MEDIUM", "reason": "覆盖已有文件"},
                yes, None, {"tool": "write_file", "detail": "parse.py"},
            )
        self.assertTrue(r["approved"])
        self.assertEqual(recorded[0].get("answer"), "Y")


class StallReadProjectTests(unittest.TestCase):
    def test_read_project_does_not_tick(self):
        self.assertFalse(loop.stall_tick_this_round(False, True, True))

    def test_mutated_without_read_ticks(self):
        self.assertTrue(loop.stall_tick_this_round(False, True, False))

    def test_wrote_product_no_tick(self):
        self.assertFalse(loop.stall_tick_this_round(True, True, False))

    def test_inspect_only_no_tick(self):
        self.assertFalse(loop.stall_tick_this_round(False, False, False))

    def test_stop_still_30(self):
        self.assertFalse(loop.should_stall_stop(True, 29))
        self.assertTrue(loop.should_stall_stop(True, 30))


class RereadCacheSourceTests(unittest.TestCase):
    def test_cache_in_loop_source(self):
        src = (Path(_ROOT) / "loop.py").read_text(encoding="utf-8")
        self.assertIn("read_cache", src)
        self.assertIn("if tool_name == \"read_file\" and _sig in read_cache", src)


if __name__ == "__main__":
    unittest.main()
