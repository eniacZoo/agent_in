#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan10.0：一行工具状态、/status 上下文、banner 真实 ctx、办公机无 emoji。"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import commands
import loop
import tools
import ui
import usage


CORE_FILES = [
    "agent.py", "ui.py", "loop.py", "commands.py",
    "approval.py", "logger.py", "llm.py",
]


class Plan10UiTests(unittest.TestCase):
    def _root(self):
        return _ROOT

    def test_icons_encode_gbk(self):
        for name in dir(ui):
            if not name.startswith("ICO_"):
                continue
            val = getattr(ui, name)
            val.encode("gbk")
        for frame in ui.Spinner.FRAMES:
            frame.encode("gbk")

    def test_core_has_no_emoji_escapes(self):
        needles = ("\\U0001F", "\\uFE0F", "\\ufe0f", "\\u26a0", "\\u26A0")
        glyphs = "✓✗⚠✅❌❓⚙️📋🚨"
        for fname in CORE_FILES:
            path = os.path.join(self._root(), fname)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            for n in needles:
                self.assertNotIn(n, text, "{} still has {}".format(fname, n))
            for ch in glyphs:
                self.assertNotIn(ch, text, "{} still has {}".format(fname, ch))

    def test_print_tool_status_one_line(self):
        self.assertTrue(callable(ui.print_tool_status))
        buf = io.StringIO()
        with redirect_stdout(buf):
            ui.print_tool_status(
                "shell", {"command": "dir"}, "confirmed", "OK",
            )
        out = buf.getvalue().strip()
        self.assertEqual(out.count("\n"), 0)
        self.assertIn("shell", out)
        self.assertIn("已确认", out)
        self.assertIn("dir", out)
        out.encode("gbk")

    def test_status_mentions_safe_mode(self):
        st = commands.CliState(".", None, "deadbeef", [], 0, 0, 0, usage.Tracker("deadbeef"))
        tools.SAFE_MODE = True
        loop.LAST_TOOL_ROUNDS = 2
        loop.LAST_PROMPT_TOKENS = 19600
        loop.CONTEXT_LIMIT = 196000
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertTrue(commands.handle("/status", st))
        text = buf.getvalue()
        self.assertIn("SAFE_MODE", text)
        self.assertIn("work_dir", text)
        self.assertIn("上下文", text)
        self.assertIn("本轮工具轮次", text)
        self.assertIn("10%", text)
        text.encode("gbk")

    def test_banner_uses_real_context(self):
        src = os.path.join(self._root(), "agent.py")
        with open(src, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("context_limit=loop.CONTEXT_LIMIT", text)
        self.assertIn("capability=llm.CAPABILITY", text)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ui.print_banner(
                "m", "http://x", ".", True, safe_mode=True,
                context_limit=128000, capability={"tool_call": True, "vision": False},
            )
        out = buf.getvalue()
        self.assertIn("v2.0", out)
        self.assertIn(ui.APP_VERSION, out)
        self.assertIn("128.0K", out)
        self.assertNotIn("196K, text", out)
        self.assertIn("tool_call yes", out)
        self.assertIn("vision no", out)
        out.encode("gbk")

    def test_last_verdict_on_reject(self):
        import tempfile
        wd = tempfile.mkdtemp(prefix="agent_in_p10_")
        tools.WORK_DIR = wd
        tools.SAFE_MODE = True
        r = tools.execute(
            "write_file", {"path": "n.txt", "content": "x"},
            confirm_fn=lambda _p: False,
        )
        self.assertTrue(r.startswith("OK:"), r)
        r2 = tools.execute(
            "write_file", {"path": "n.txt", "content": "y"},
            confirm_fn=lambda _p: False,
        )
        self.assertIn("拒绝", r2)
        self.assertEqual(tools.LAST_VERDICT, "rejected")


if __name__ == "__main__":
    unittest.main()
