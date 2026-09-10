#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan15.0：心跳 DNS 失败可识别；交互模式连通失败不退出。"""
import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent
import heartbeat
import llm


class Plan15Tests(unittest.TestCase):
    def test_ping_empty_url(self):
        r = heartbeat.ping("")
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "dns")

    def test_ping_dns_fail(self):
        import socket
        from unittest.mock import patch

        def boom(*_a, **_k):
            raise socket.gaierror(11001, "getaddrinfo failed")

        with patch("heartbeat.socket.getaddrinfo", boom):
            r = heartbeat.ping("http://office.example:8088/api/v1", timeout=1)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "dns")
        self.assertTrue(r.get("error"))
        self.assertEqual(heartbeat.LAST.get("stage"), "dns")

    def test_interactive_no_exit_on_disconnect(self):
        src = inspect.getsource(agent.run_interactive)
        self.assertNotIn("sys.exit(1)", src)
        src_single = inspect.getsource(agent.run_single)
        self.assertIn("sys.exit(1)", src_single)

    def test_connected_flag_exists(self):
        self.assertTrue(hasattr(llm, "CONNECTED"))
        self.assertTrue(hasattr(llm, "LAST_TTFB_MS"))


if __name__ == "__main__":
    unittest.main()
