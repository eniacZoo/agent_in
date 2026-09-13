#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan23.0：会话树兼容旧线性文件、分叉保兄弟、window 只含叶路径；折行差分。"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import commands
import session
import ui
import usage


class Plan23TreeTests(unittest.TestCase):
    def setUp(self):
        self._old = session._SESSIONS_DIR
        self.tmp = tempfile.mkdtemp(prefix="p23_sess_")
        session._SESSIONS_DIR = Path(self.tmp)

    def tearDown(self):
        session._SESSIONS_DIR = self._old

    def _state(self, messages=None, leaf_id=None):
        return commands.CliState(
            ".", None, "deadbeef", messages or [], 0, 0, 0, usage.Tracker("deadbeef"),
            leaf_id=leaf_id,
        )

    def test_linear_file_becomes_chain(self):
        sid = "oldlin01"
        raw = {
            "session_id": sid,
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
        }
        p = session._SESSIONS_DIR / f"{sid}.json"
        p.write_text(json.dumps(raw), encoding="utf-8")
        data = session.load(sid)
        msgs = data["messages"]
        self.assertEqual(len(msgs), 2)
        self.assertTrue(msgs[0]["id"])
        self.assertIsNone(msgs[0]["parent"])
        self.assertEqual(msgs[1]["parent"], msgs[0]["id"])
        self.assertEqual(data["meta"]["leaf_id"], msgs[1]["id"])
        path = session.path_to_leaf(msgs, data["meta"]["leaf_id"])
        self.assertEqual([m["role"] for m in path], ["user", "assistant"])

    def test_fork_keeps_siblings_and_window_is_leaf_path(self):
        tree = []
        u1 = {"role": "user", "content": "root"}
        session.stamp_missing([u1], None)
        tree.append(u1)
        a1 = {"role": "assistant", "content": "ok"}
        session.stamp_missing([a1], u1["id"])
        tree.append(a1)
        u2 = {"role": "user", "content": "branch-a"}
        session.stamp_missing([u2], a1["id"])
        tree.append(u2)
        a2 = {"role": "assistant", "content": "a-done"}
        session.stamp_missing([a2], u2["id"])
        tree.append(a2)
        u2b = {"role": "user", "content": "branch-b"}
        session.stamp_missing([u2b], a1["id"])
        tree.append(u2b)
        a2b = {"role": "assistant", "content": "b-done"}
        session.stamp_missing([a2b], u2b["id"])
        tree.append(a2b)

        session.save("fork01", tree, meta={"leaf_id": a2b["id"]})
        data = session.load("fork01")
        self.assertEqual(len(data["messages"]), 6)
        path_b = session.path_to_leaf(data["messages"], a2b["id"])
        self.assertEqual([m["content"] for m in path_b], ["root", "ok", "branch-b", "b-done"])
        self.assertNotIn("branch-a", [m["content"] for m in path_b])
        path_a = session.path_to_leaf(data["messages"], a2["id"])
        self.assertEqual([m["content"] for m in path_a], ["root", "ok", "branch-a", "a-done"])

        st = self._state(data["messages"], a2b["id"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            commands._cmd_fork(st, a2["id"])
        self.assertEqual(st.leaf_id, a2["id"])
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            commands._cmd_tree(st, "")
        text = buf2.getvalue()
        self.assertIn("branch-a", text)
        self.assertIn("branch-b", text)
        self.assertIn(a2["id"], text)

        buf3 = io.StringIO()
        with redirect_stdout(buf3):
            commands._cmd_history(st, "")
        hist = buf3.getvalue()
        self.assertIn("branch-a", hist)
        self.assertNotIn("branch-b", hist)

    def test_mid_save_merge_keeps_sibling(self):
        tree = []
        u1 = {"role": "user", "content": "u"}
        session.stamp_missing([u1])
        tree.append(u1)
        a1 = {"role": "assistant", "content": "a"}
        session.stamp_missing([a1], u1["id"])
        tree.append(a1)
        sib = {"role": "user", "content": "other"}
        session.stamp_missing([sib], a1["id"])
        tree.append(sib)
        session.save("merge01", tree, meta={"leaf_id": sib["id"]})
        path = session.path_to_leaf(tree, a1["id"])
        session.save("merge01", path, meta={"leaf_id": a1["id"]})
        data = session.load("merge01")
        contents = [m["content"] for m in data["messages"]]
        self.assertIn("other", contents)
        self.assertEqual(data["meta"]["leaf_id"], a1["id"])

    def test_help_lists_tree_fork(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ui.print_help()
        text = buf.getvalue()
        self.assertIn("/tree", text)
        self.assertIn("/fork", text)


class Plan23RenderTests(unittest.TestCase):
    def test_wrap_and_diff(self):
        lines = ui.wrap_display_lines("abcdefghij", 4)
        self.assertEqual(lines, ["abcd", "efgh", "ij"])
        self.assertEqual(ui.diff_line_index(["a", "b"], ["a", "c"]), 1)
        self.assertEqual(ui.diff_line_index(["a"], ["a", "b"]), 1)
        wide = ui.wrap_display_lines("你好世界", 4)
        self.assertEqual(wide, ["你好", "世界"])

    def test_stream_fallback_no_tty_logic(self):
        disp = ui.StreamDisplay(show_reasoning=True)
        disp._diff = False
        buf = io.StringIO()
        with redirect_stdout(buf):
            disp.handle({"type": "reasoning", "content": "think"})
            disp.handle({"type": "text", "content": "hi"})
            disp.finish()
        out = buf.getvalue()
        self.assertIn("think", out)
        self.assertIn("hi", out)


if __name__ == "__main__":
    unittest.main()
