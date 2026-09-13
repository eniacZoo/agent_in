#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan22：/config 与运行时 provider 同源；office2 思考字段；连接失败友好文案。"""
import io
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
import logger
import providers
import usage


class Plan22Tests(unittest.TestCase):
    def setUp(self):
        self._prev_active = providers._active_name
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        Path(self.tmp.name).write_text("{}", encoding="utf-8")
        config.set_path(self.tmp.name)
        config.load(force_reload=True)
        providers.set_active_name(None)

    def tearDown(self):
        providers.set_active_name(self._prev_active)
        config.set_path(None)
        config.load(force_reload=True)
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def _state(self):
        return commands.CliState(".", None, "deadbeef", [], 0, 0, 0, usage.Tracker("deadbeef"))

    def test_config_shows_runtime_provider(self):
        self.assertEqual(config.get("provider"), "default")
        providers.set_active_name("office")
        buf = io.StringIO()
        with redirect_stdout(buf):
            commands._cmd_config(self._state(), "")
        text = buf.getvalue()
        self.assertIn("provider             office", text)
        self.assertNotIn("provider             default", text)

    def test_switch_persists_provider(self):
        st = self._state()
        with patch.object(providers, "apply_to_llm"), \
             patch.object(llm, "check_connection", return_value=(True, "ok", {})), \
             patch.object(commands.heartbeat, "ping", return_value={"ok": True, "elapsed_ms": 1}), \
             patch.object(commands.loop, "apply_capability"), \
             patch.object(commands.loop, "print_capability"):
            buf = io.StringIO()
            with redirect_stdout(buf):
                commands._switch_to_provider(st, "office")
        self.assertEqual(providers.get_active_name(), "office")
        self.assertEqual(config.get("provider"), "office")

    def test_office2_in_defaults(self):
        self.assertIn("office2", config.get("providers"))
        self.assertEqual(config.get("providers")["office2"]["model"], "szicbc-claw-01")

    def test_connection_fail_text(self):
        with patch.object(providers, "get_active_name", return_value="default"):
            msg = llm.connection_fail_text(model="deepseek-v4.1-flash-expires-on-0910")
        self.assertEqual(
            msg,
            "无法连接模型：[default][deepseek-v4.1-flash-expires-on-0910]，请尝试使用/model、/provider命令进行切换",
        )
        self.assertNotIn("urlopen", msg)

    def test_check_connection_hides_urlopen(self):
        with patch.object(providers, "get_active_name", return_value="default"), \
             patch("urllib.request.urlopen", side_effect=OSError("getaddrinfo failed")):
            ok, msg, cap = llm.check_connection(model="m", probe=False)
        self.assertFalse(ok)
        self.assertIn("无法连接模型：", msg)
        self.assertNotIn("getaddrinfo", msg)
        self.assertEqual(cap, {})

    def test_logger_skips_connection_events(self):
        with patch.object(logger, "_print_terminal") as printed:
            logger.error("connection_lost", {"error": "urlopen boom"})
            logger.warn("heartbeat_fail", {"error": "dns"})
            logger.error("llm_error", {"error": "x"})
            printed.assert_not_called()
            logger.error("other_error", {"error": "x"})
            printed.assert_called()


if __name__ == "__main__":
    unittest.main()
