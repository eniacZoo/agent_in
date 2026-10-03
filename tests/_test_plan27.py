#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan27：截断拒绝执行、append-only 窗口、结构化压缩、按进展停机、python/后台任务、temp 清理。"""
import io
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import config
import context
import llm
import loop
import taskdir
import telemetry
import tools


class TruncationTests(unittest.TestCase):
    def test_parse_tool_args_rejects_broken_json(self):
        args, bad = llm.parse_tool_args('{"path": "a.py", "content": "x')
        self.assertTrue(bad)
        self.assertIn("_raw", args)
        args, bad = llm.parse_tool_args("")
        self.assertFalse(bad)
        self.assertEqual(args, {})

    def test_truncated_call_is_not_executed(self):
        td = tempfile.mkdtemp(prefix="p27_trunc_")
        tools.WORK_DIR = td
        n = {"i": 0}

        def fake_chat(messages, tools=None, stream=True, model=None, max_tokens=None, images=None, think=None):
            n["i"] += 1
            if n["i"] == 1:
                yield {
                    "type": "tool_call", "id": "c1", "name": "write_file",
                    "arguments": {"_raw": "{\"path\": \"a.txt\""}, "truncated": True,
                }
                yield {"type": "finish", "reason": "length"}
                yield {"type": "done"}
            else:
                yield {"type": "text", "content": "stopped"}
                yield {"type": "finish", "reason": "stop"}
                yield {"type": "done"}

        old = llm.chat
        llm.chat = fake_chat
        try:
            with redirect_stdout(io.StringIO()):
                text, *_ = loop.agent_loop(
                    [{"role": "user", "content": "write"}], td, verbose=False, interactive=False,
                )
        finally:
            llm.chat = old
            shutil.rmtree(td, ignore_errors=True)
        self.assertFalse(os.path.exists(os.path.join(td, "a.txt")))
        self.assertIn("stopped", text)
        self.assertGreaterEqual(loop.LAST_TELEMETRY.c["truncations"], 1)
        self.assertGreaterEqual(loop.LAST_TELEMETRY.c["finish_length"], 1)

    def test_sanitize_keeps_turn_reasoning_only_when_flagged(self):
        kept = llm._sanitize_messages([{
            "role": "assistant", "content": "a", "reasoning_content": "think", "_keep_reasoning": True,
        }])
        self.assertEqual(kept[0].get("reasoning_content"), "think")
        self.assertNotIn("_keep_reasoning", kept[0])
        dropped = llm._sanitize_messages([{
            "role": "assistant", "content": "a", "reasoning_content": "think",
        }])
        self.assertNotIn("reasoning_content", dropped[0])


class WindowTests(unittest.TestCase):
    def _msg(self, role, content, mid, parent=None):
        m = {"role": role, "content": content, "id": mid, "parent": parent}
        return m

    def test_derive_window_keeps_full_history_until_compact(self):
        tr = [
            self._msg("user", "goal", "1"),
            self._msg("assistant", "step", "2", "1"),
            self._msg("user", "go on", "3", "2"),
        ]
        win = context.derive_window(tr, "sys")
        self.assertEqual([m.get("content") for m in win], ["sys", "goal", "step", "go on"])

    def test_compact_keeps_recent_and_writes_marker(self):
        tr = []
        parent = None
        for i in range(8):
            role = "user" if i % 2 == 0 else "assistant"
            m = self._msg(role, "m" * 40 + str(i), str(i), parent)
            tr.append(m)
            parent = m["id"]

        def fake_chat(messages, tools=None, stream=True, model=None, max_tokens=None, images=None, think=None):
            self.assertFalse(think)
            yield {"type": "text", "content": "## 目标\n保留目标\n## 下一步\n继续"}

        marker, info = context.compact(tr, fake_chat, keep_recent_tokens=30)
        self.assertIsNotNone(marker)
        self.assertIn("保留目标", marker["content"])
        self.assertIn("文件清单", marker["content"])
        self.assertTrue(info["used_llm"])
        tr.append(marker)
        _prev, live = context.live_slice(tr)
        self.assertLess(len(live), len(tr) - 1)
        self.assertEqual(live[-1]["id"], tr[-2]["id"])


class ProfileTests(unittest.TestCase):
    def test_qwen_and_deepseek_budgets(self):
        qwen = config.profile_for("office")
        ds = config.profile_for("default")
        self.assertEqual(qwen["context_limit"], 128000)
        self.assertEqual(qwen["max_tokens"], 32768)
        self.assertEqual(qwen["max_tool_iterations"], 200)
        self.assertTrue(qwen["keep_turn_reasoning"])
        self.assertEqual(ds["max_tokens"], 16384)
        self.assertEqual(ds["max_tool_iterations"], 300)
        self.assertFalse(ds["keep_turn_reasoning"])
        self.assertLess(config.compact_trigger(qwen), qwen["context_limit"] - qwen["max_tokens"])

    def test_cache_hit_tokens(self):
        self.assertEqual(telemetry.cache_hit_tokens({"prompt_cache_hit_tokens": 5}), 5)
        self.assertEqual(telemetry.cache_hit_tokens({"prompt_tokens_details": {"cached_tokens": 3}}), 3)
        self.assertEqual(telemetry.cache_hit_tokens({}), 0)


class StopTests(unittest.TestCase):
    def test_repeat_same_result_stops(self):
        td = tempfile.mkdtemp(prefix="p27_rep_")
        tools.WORK_DIR = td
        n = {"i": 0}

        def fake_chat(messages, tools=None, stream=True, model=None, max_tokens=None, images=None, think=None):
            n["i"] += 1
            yield {
                "type": "tool_call", "id": "c" + str(n["i"]), "name": "read_file",
                "arguments": {"path": "missing_p27.txt"},
            }
            yield {"type": "done"}

        old = llm.chat
        llm.chat = fake_chat
        try:
            with redirect_stdout(io.StringIO()):
                text, *_ = loop.agent_loop(
                    [{"role": "user", "content": "read"}], td, verbose=False, interactive=False,
                )
        finally:
            llm.chat = old
            shutil.rmtree(td, ignore_errors=True)
        self.assertIn("同一调用", text)
        self.assertLess(n["i"], 10)

    def test_error_streak_stops(self):
        td = tempfile.mkdtemp(prefix="p27_err_")
        tools.WORK_DIR = td
        n = {"i": 0}

        def fake_chat(messages, tools=None, stream=True, model=None, max_tokens=None, images=None, think=None):
            n["i"] += 1
            yield {
                "type": "tool_call", "id": "e" + str(n["i"]), "name": "read_file",
                "arguments": {"path": "missing_%d.txt" % n["i"]},
            }
            yield {"type": "done"}

        old = llm.chat
        llm.chat = fake_chat
        try:
            with redirect_stdout(io.StringIO()):
                text, *_ = loop.agent_loop(
                    [{"role": "user", "content": "read"}], td, verbose=False, interactive=False,
                )
        finally:
            llm.chat = old
            shutil.rmtree(td, ignore_errors=True)
        self.assertIn("连续 5 次工具错误", text)
        self.assertLess(n["i"], 12)

    def test_continue_reaches_the_model(self):
        import commands

        class _State:
            quit = False

        self.assertFalse(commands.handle("/continue", _State()))


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="p27_tool_")
        self.old_wd = tools.WORK_DIR
        self.old_sid = tools.SESSION_ID
        tools.WORK_DIR = self.td
        tools.SESSION_ID = "p27"

    def tearDown(self):
        tools.kill_all_jobs()
        tools.WORK_DIR = self.old_wd
        tools.SESSION_ID = self.old_sid
        shutil.rmtree(self.td, ignore_errors=True)

    def test_python_deletes_script_on_success(self):
        r = tools._exec_python({"code": "print(1 + 1)\n"})
        self.assertIn("2", r)
        self.assertNotIn("Error", r)
        run = os.path.join(taskdir.task_dir(self.td, "p27", create=False), "run")
        left = os.listdir(run) if os.path.isdir(run) else []
        self.assertEqual(left, [])

    def test_python_keeps_script_on_failure(self):
        r = tools._exec_python({"code": "raise SystemExit(2)\n"})
        self.assertIn("exit code", r)
        self.assertIn("脚本已保留", r)

    def test_shell_moves_slow_command_to_background(self):
        r = tools._exec_shell({"command": "Start-Sleep -Seconds 4", "timeout": 1})
        self.assertIn("已转入后台", r)
        jid = r.split("任务 ", 1)[1].split(" ", 1)[0]
        killed = tools._exec_job({"action": "kill", "job_id": jid})
        self.assertIn("已停止", killed)

    def test_write_append_and_edit_batch(self):
        path = os.path.join(self.td, "a.txt")
        tools._exec_write_file({"path": path, "content": "one\n", "mode": "overwrite"})
        tools._exec_write_file({"path": path, "content": "two\n", "mode": "append"})
        r = tools._exec_edit_file({
            "path": path,
            "edits": [
                {"old_text": "one", "new_text": "ONE"},
                {"old_text": "two", "new_text": "TWO"},
            ],
        })
        self.assertIn("OK:", r)
        self.assertIn("ONE", r)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "ONE\nTWO\n")
        denied = tools._exec_edit_file({"path": path, "old_text": "\n", "new_text": " "})
        self.assertIn("Error", denied)
        self.assertIn("replace_all", denied)


class TempTests(unittest.TestCase):
    def test_sweep_removes_expired_and_protects_current(self):
        td = tempfile.mkdtemp(prefix="p27_temp_")
        try:
            old = taskdir.task_dir(td, "old")
            cur = taskdir.task_dir(td, "cur")
            with open(os.path.join(td, "temp", "legacy.txt"), "w", encoding="utf-8") as f:
                f.write("x")
            past = time.time() - 10 * 86400
            os.utime(os.path.join(old, ".active"), (past, past))
            os.utime(os.path.join(td, "temp", "legacy.txt"), (past, past))
            res = taskdir.sweep(td, ttl_days=7, protect=["cur"])
            self.assertFalse(os.path.isdir(old))
            self.assertTrue(os.path.isdir(cur))
            self.assertFalse(os.path.exists(os.path.join(td, "temp", "legacy.txt")))
            self.assertGreaterEqual(res["task_dirs"], 1)
            self.assertGreaterEqual(res["legacy"], 1)
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_baseline_session_counts(self):
        p = os.path.join(_ROOT, "sessions", "bc657821.json")
        if not os.path.exists(p):
            self.skipTest("baseline session not present")
        sys.path.insert(0, os.path.join(_ROOT, "tests", "regression"))
        import score_session
        got = score_session.score(p)
        self.assertEqual(got["tool_calls"], 153)
        self.assertEqual(got["hit_round_cap_notes"], 1)
        self.assertGreaterEqual(got["reread_reads"], 20)
        self.assertEqual(got["telemetry"], {})


if __name__ == "__main__":
    unittest.main()
