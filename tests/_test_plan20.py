#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan20.0：轮次与成本治理。按阶段追加 TestCase。"""
import json
import os
import sys
import unittest
from pathlib import Path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import config
import context
import llm
import loop
import commands
import tools
import ui
import io
from contextlib import redirect_stdout


def _asst_write(n, content):
    args = json.dumps({"path": "temp/a.py", "content": content}, ensure_ascii=False)
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": f"c{n}",
            "type": "function",
            "function": {"name": "write_file", "arguments": args},
        }],
    }


class Phase1Tests(unittest.TestCase):
    def test_sanitize_strips_reasoning(self):
        msgs = [{
            "role": "assistant",
            "content": "ok",
            "reasoning_content": "long think",
            "tool_calls": [{"id": "1", "type": "function", "function": {"name": "shell", "arguments": "{}"}}],
        }]
        out = llm._sanitize_messages(msgs)
        self.assertNotIn("reasoning_content", out[0])
        self.assertEqual(out[0]["role"], "assistant")
        self.assertEqual(out[0]["content"], "ok")
        self.assertIn("tool_calls", out[0])

    def test_sanitize_keeps_none_content(self):
        msgs = [{
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "1", "type": "function", "function": {"name": "shell", "arguments": "{}"}}],
        }]
        out = llm._sanitize_messages(msgs)
        self.assertIn("content", out[0])
        self.assertIsNone(out[0]["content"])

    def test_trim_args_shrinks_old(self):
        blob = "x" * 5000
        msgs = [_asst_write(i, blob) for i in range(4)]
        out, saved = context.trim_tool_call_args(msgs, keep_last=2)
        self.assertGreater(saved, 0)
        for i in range(2):
            args = json.loads(out[i]["tool_calls"][0]["function"]["arguments"])
            self.assertIsInstance(args["content"], dict)
            self.assertTrue(args["content"].get("_omitted"))
            self.assertEqual(args["content"].get("chars"), 5000)
            self.assertIn("temp/a.py", args["content"].get("path", ""))
            self.assertNotIn("x" * 50, json.dumps(args["content"]))
        for i in range(2, 4):
            args = json.loads(out[i]["tool_calls"][0]["function"]["arguments"])
            self.assertEqual(len(args["content"]), 5000)

    def test_trim_args_bad_json(self):
        msgs = [{
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "c0",
                "type": "function",
                "function": {"name": "write_file", "arguments": "{not json"},
            }],
        }]
        out, saved = context.trim_tool_call_args(msgs, keep_last=0)
        self.assertEqual(saved, 0)
        self.assertEqual(out[0]["tool_calls"][0]["function"]["arguments"], "{not json")

    def test_apply_over_budget(self):
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]
        _, action_hi = context.apply(msgs, used_tokens=50000, limit=196000, budget=40000)
        self.assertIn("over_budget", action_hi)
        _, action_lo = context.apply(msgs, used_tokens=10000, limit=196000, budget=40000)
        self.assertNotIn("over_budget", action_lo)

    def test_compact_at_tokens_default(self):
        config._config_cache = None
        self.assertEqual(config._DEFAULTS["compact_at_tokens"], 40000)
        self.assertEqual(config.load()["compact_at_tokens"], 40000)


class Phase2Tests(unittest.TestCase):
    def setUp(self):
        loop.ledger_clear()

    def tearDown(self):
        loop.ledger_clear()

    def test_ledger_dedupe(self):
        loop.ledger_add("write_file a.py")
        loop.ledger_add("write_file a.py")
        loop.ledger_add("write_file a.py")
        self.assertEqual(len(loop.TURN_LEDGER), 1)

    def test_ledger_cap(self):
        for i in range(60):
            loop.ledger_add(f"write_file {i}.py")
        self.assertEqual(len(loop.TURN_LEDGER), loop.LEDGER_MAX_LINES)
        self.assertEqual(loop.TURN_LEDGER[0], "write_file 20.py")
        self.assertEqual(loop.TURN_LEDGER[-1], "write_file 59.py")

    def test_ledger_block_empty(self):
        self.assertEqual(loop.ledger_block(), "")

    def test_ledger_block_content(self):
        loop.ledger_add("write_file temp/a.py")
        loop.ledger_add("shell python temp/a.py -> ok")
        block = loop.ledger_block()
        self.assertIn("不要重复做", block)
        self.assertIn("write_file temp/a.py", block)
        self.assertIn("shell python temp/a.py -> ok", block)

    def test_ledger_clear(self):
        loop.ledger_add("x")
        loop.ledger_clear()
        self.assertEqual(loop.TURN_LEDGER, [])

    def test_cmd_new_clears_ledger(self):
        loop.ledger_add("write_file z.py")
        state = commands.CliState(".", None, "abc", [], 0, 0, 1, None)
        commands._cmd_new(state, "")
        self.assertEqual(loop.TURN_LEDGER, [])


class Phase3Tests(unittest.TestCase):
    def test_shell_name(self):
        self.assertTrue(tools.shell_name())

    def test_prompt_has_shell(self):
        p = loop.DEFAULT_SYSTEM_PROMPT.format(work_dir="X", shell="powershell")
        self.assertIn("powershell", p)
        self.assertIn(";", p)

    def test_discipline_always_on(self):
        self.assertIn("立即停止", loop._OFFICE_DISCIPLINE)
        src = (Path(_ROOT) / "loop.py").read_text(encoding="utf-8")
        self.assertNotIn('== "office"', src)

    def test_prompt_no_ampersand_advice(self):
        self.assertIn("不要用 `&&`", loop.DEFAULT_SYSTEM_PROMPT)


class Phase4Tests(unittest.TestCase):
    def test_tool_sig_stable(self):
        a = loop._tool_sig("shell", {"command": "dir"})
        b = loop._tool_sig("shell", {"command": "dir"})
        c = loop._tool_sig("shell", {"command": "ls"})
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(len(a[1]), 12)

    def test_status_line_in_source(self):
        src = (Path(_ROOT) / "loop.py").read_text(encoding="utf-8")
        self.assertIn('round {tool_iterations}/{MAX_TOOL_ITERATIONS}', src)
        self.assertIn("产出", src)

    def test_stall_stop_in_source(self):
        src = (Path(_ROOT) / "loop.py").read_text(encoding="utf-8")
        self.assertIn("rounds_since_write >= STALL_STOP_ROUNDS", src)
        self.assertIn("连续 {STALL_STOP_ROUNDS} 轮无文件产出", src)
        self.assertIn("STALL_STOP_ROUNDS = 30", src)
        self.assertIn("STALL_WARN_ROUNDS = 10", src)

    def test_repeat_warn_in_source(self):
        src = (Path(_ROOT) / "loop.py").read_text(encoding="utf-8")
        self.assertIn("tool_repeat", src)
        self.assertIn("args_hash", src)


class Phase5Tests(unittest.TestCase):
    def test_max_iter_default(self):
        config._config_cache = None
        self.assertEqual(config.load()["max_tool_iterations"], 80)
        src = (Path(_ROOT) / "agent.py").read_text(encoding="utf-8")
        self.assertNotIn('"max_tool_iterations", 20', src)
        self.assertIn('"max_tool_iterations", 80', src)

    def test_turn_footer_sig(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ui.turn_footer()
            ui.turn_footer({"prompt_tokens": 1000, "completion_tokens": 10}, 0.5)
            ui.turn_footer(
                {"prompt_tokens": 2400000, "completion_tokens": 10},
                1.0, rounds=52, peak=86564,
            )
        out = buf.getvalue()
        self.assertIn("累计 52 轮", out)
        self.assertIn("峰值", out)

    def test_multiline_marker(self):
        src = (Path(_ROOT) / "agent.py").read_text(encoding="utf-8")
        self.assertIn("_read_multiline", src)
        self.assertIn("/paste", src)
        self.assertIn("_drain_pasted_lines", src)

    def test_loop_off_by_one(self):
        src = (Path(_ROOT) / "loop.py").read_text(encoding="utf-8")
        self.assertIn("while tool_iterations < MAX_TOOL_ITERATIONS:", src)
        self.assertIn("if tool_iterations >= MAX_TOOL_ITERATIONS:", src)


if __name__ == "__main__":
    unittest.main()
