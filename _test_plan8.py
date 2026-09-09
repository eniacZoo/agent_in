#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan8.0：分层拆分后命令表与依赖方向。"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import commands
import loop
import usage


class Plan8SplitTests(unittest.TestCase):
    def _state(self):
        return commands.CliState(".", None, "deadbeef", [], 0, 0, 0, usage.Tracker("deadbeef"))

    def test_non_slash_passes_through(self):
        self.assertFalse(commands.handle("帮我看看目录", self._state()))

    def test_help_consumed(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertTrue(commands.handle("/help", self._state()))

    def test_unknown_slash_consumed(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertTrue(commands.handle("/no-such-cmd", self._state()))
        self.assertIn("未知命令", buf.getvalue())
        self.assertIn("/help", buf.getvalue())

    def test_quit_sets_flag(self):
        st = self._state()
        self.assertTrue(commands.handle("/quit", st))
        self.assertTrue(st.quit)

    def test_new_resets_session(self):
        st = self._state()
        st.messages = [{"role": "user", "content": "hi"}]
        st.turn = 3
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertTrue(commands.handle("/new", st))
        self.assertEqual(st.messages, [])
        self.assertEqual(st.turn, 0)
        self.assertNotEqual(st.sid, "deadbeef")

    def test_agent_py_has_no_sessions_branch(self):
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.py")
        with open(src, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn('if prompt == "/sessions"', text)
        self.assertNotIn("记住", text)
        self.assertIn("def run_interactive", text)
        self.assertIn("commands.handle", text)

    def test_loop_does_not_import_commands(self):
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop.py")
        with open(src, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f]
        imports = [ln for ln in lines if ln.startswith("import ") or ln.startswith("from ")]
        self.assertFalse(any(ln.split()[1].split(".")[0] == "commands" for ln in imports))
        self.assertFalse(any(ln.split()[1].split(".")[0] == "agent" for ln in imports))
        self.assertTrue(any("def agent_loop" in ln for ln in lines))

    def test_model_aliases(self):
        self.assertEqual(commands._resolve_model_to_provider("deepseek"), "default")
        self.assertEqual(commands._resolve_model_to_provider("qwen"), "office")
        self.assertEqual(commands._resolve_model_to_provider("AngelOrDevil"), "office")
        self.assertIsNone(commands._resolve_model_to_provider("nope"))

    def test_model_lists_without_network(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertTrue(commands.handle("/model", self._state()))
        text = buf.getvalue()
        self.assertIn("deepseek", text.lower())
        self.assertIn("qwen", text.lower())

    def test_model_unknown(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertTrue(commands.handle("/model nope", self._state()))
        self.assertIn("未知模型", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
