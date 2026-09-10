#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan17.0：80 轮、temp 覆盖免确认、工具结果 keep_last=6。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import config
import context
import loop
import tool_guard
import tools


class Plan17Tests(unittest.TestCase):
    def test_default_iterations_80(self):
        self.assertEqual(config._DEFAULTS["max_tool_iterations"], 80)
        self.assertEqual(loop.MAX_TOOL_ITERATIONS, 80)

    def test_temp_overwrite_no_confirm(self):
        wd = tempfile.mkdtemp(prefix="agent_in_p17_")
        tools.WORK_DIR = wd
        tools.SAFE_MODE = True
        tools.ensure_temp_dir(wd)
        p = os.path.join(wd, "temp", "foo.py")
        Path(p).write_text("a=1\n", encoding="utf-8")
        asked = []
        r = tools.execute(
            "write_file", {"path": p, "content": "a=2\n"},
            confirm_fn=lambda prompt: asked.append(prompt) or False,
        )
        self.assertFalse(asked, asked)
        self.assertIn("OK: Wrote", r)
        self.assertEqual(Path(p).read_text(encoding="utf-8"), "a=2\n")

    def test_root_overwrite_still_confirms(self):
        wd = tempfile.mkdtemp(prefix="agent_in_p17_")
        tools.WORK_DIR = wd
        tools.SAFE_MODE = True
        p = os.path.join(wd, "keep.py")
        Path(p).write_text("x\n", encoding="utf-8")
        r = tools.execute(
            "write_file", {"path": p, "content": "y\n"},
            confirm_fn=lambda _p: False,
        )
        self.assertIn("拒绝", r)

    def test_trim_keep_last_default_in_apply(self):
        self.assertEqual(context.TOOL_KEEP_CHARS, 2000)
        msgs = [{"role": "system", "content": "s"}]
        for i in range(8):
            msgs.append({"role": "tool", "content": "x" * 3000, "tool_call_id": str(i)})
        out, saved = context.trim_tool_results(msgs, keep_last=6)
        truncated = [m for m in out if m.get("role") == "tool" and "truncated" in m.get("content", "")]
        self.assertEqual(len(truncated), 2)
        self.assertGreater(saved, 0)


if __name__ == "__main__":
    unittest.main()
