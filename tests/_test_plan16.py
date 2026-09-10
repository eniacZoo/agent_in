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

    def test_reject_high(self):
        st = commands.CliState(".", None, "deadbeef", [], 0, 0, 0, usage.Tracker("deadbeef"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertTrue(commands.handle("/model high", st))
        self.assertIn("low / medium / xhigh", buf.getvalue())
        self.assertEqual(config.get("reasoning_effort"), "low")


if __name__ == "__main__":
    unittest.main()
