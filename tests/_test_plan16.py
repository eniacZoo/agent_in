#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan16.0：reasoning_effort 默认 low；/model 可切；DeepSeek 不带该字段。"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import commands
import config
import llm
import usage


class Plan16Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        Path(self.tmp.name).write_text("{}", encoding="utf-8")
        config.set_path(self.tmp.name)
        config.load(force_reload=True)

    def tearDown(self):
        config.set_path(None)
        config.load(force_reload=True)
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_default_effort_low(self):
        self.assertEqual(config.get("reasoning_effort"), "low")

    def test_attach_office_only(self):
        payload = {}
        with patch.object(llm.providers, "get_active_name", return_value="office"):
            llm._attach_thinking(payload)
        self.assertEqual(payload.get("reasoning_effort"), "low")
        self.assertTrue(payload.get("enable_thinking"))
        payload_o2 = {}
        with patch.object(llm.providers, "get_active_name", return_value="office2"):
            llm._attach_thinking(payload_o2)
        self.assertEqual(payload_o2.get("reasoning_effort"), "low")
        self.assertTrue(payload_o2.get("enable_thinking"))
        payload2 = {}
        with patch.object(llm.providers, "get_active_name", return_value="default"):
            llm._attach_thinking(payload2)
        self.assertNotIn("reasoning_effort", payload2)

    def test_model_effort_only(self):
        st = commands.CliState(".", None, "deadbeef", [], 0, 0, 0, usage.Tracker("deadbeef"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertTrue(commands.handle("/model medium", st))
        self.assertEqual(config.get("reasoning_effort"), "medium")
        self.assertIn("medium", buf.getvalue())

    def test_home_openrouter_request(self):
        Path(self.tmp.name).write_text(json.dumps({
            "providers": {
                "home": {
                    "base_url": "https://openrouter.ai/api/v1",
                    "api_key": "test",
                    "model": "qwen/qwen3.8-27b",
                    "kind": "qwen",
                    "request": {
                        "reasoning": {"enabled": True},
                        "provider": {"only": ["wafer"], "allow_fallbacks": False},
                    },
                }
            }
        }), encoding="utf-8")
        config.load(force_reload=True)
        self.assertEqual(config.profile_kind("home"), "qwen")
        self.assertEqual(config.profile_for("home")["context_limit"], 128000)
        self.assertEqual(config.profile_for("home")["max_tool_iterations"], 200)
        payload = {}
        with patch.object(llm.providers, "get_active_name", return_value="home"):
            llm._attach_thinking(payload)
        self.assertTrue(payload["reasoning"]["enabled"])
        self.assertNotIn("effort", payload["reasoning"])
        self.assertEqual(payload["provider"]["only"], ["wafer"])
        self.assertFalse(payload["provider"]["allow_fallbacks"])
        self.assertNotIn("enable_thinking", payload)
        off = {}
        with patch.object(llm.providers, "get_active_name", return_value="home"):
            llm._attach_thinking(off, think=False)
        self.assertFalse(off["reasoning"]["enabled"])
        kept = llm._sanitize_messages([{
            "role": "assistant",
            "content": "a",
            "reasoning_content": "think",
            "reasoning_details": [{"type": "reasoning.text", "text": "think", "index": 0}],
            "_keep_reasoning": True,
        }])
        self.assertEqual(kept[0]["reasoning_details"][0]["text"], "think")
        self.assertNotIn("reasoning_content", kept[0])
        merged = llm._merge_reasoning_details(
            [{"type": "reasoning.text", "text": "ab", "index": 0}],
            [{"type": "reasoning.text", "text": "c", "index": 0}],
        )
        self.assertEqual(merged[0]["text"], "abc")

    def test_openrouter_credit_cap_lowers_max_tokens(self):
        body = "You requested up to 16384 tokens, but can only afford 6446."
        self.assertEqual(llm._affordable_tokens(body), 6446)
        req = llm.urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps({"max_tokens": 16384, "model": "qwen/qwen3.8-27b"}).encode("utf-8"),
            method="POST",
        )
        self.assertTrue(llm._lower_max_tokens(req, 6446))
        self.assertEqual(json.loads(req.data.decode("utf-8"))["max_tokens"], 6190)
        self.assertIn("6446", llm._credit_fail_text(body))

    def test_reject_high(self):
        st = commands.CliState(".", None, "deadbeef", [], 0, 0, 0, usage.Tracker("deadbeef"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertTrue(commands.handle("/model high", st))
        self.assertIn("low / medium / xhigh", buf.getvalue())
        self.assertEqual(config.get("reasoning_effort"), "low")


if __name__ == "__main__":
    unittest.main()
