#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""当前解释器跑 vendor + stall 30。"""
import os
import sys
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import loop
import tools


class VendorPythonTests(unittest.TestCase):
    def test_uses_current_interpreter(self):
        self.assertEqual(tools._vendor_python(), sys.executable)

    def test_no_py311_lookup(self):
        with open(tools.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn('["py", "-3.11"]', src)
        self.assertNotIn("_discover_py311", src)
        env = tools._shell_env()
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep)[0], tools._vendor_dir())

    def test_abi_hint_names_current_interpreter(self):
        h = tools._vendor_abi_hint()
        self.assertIn("不要搜索其他 python", h)
        self.assertIn(sys.executable, h)
        self.assertNotIn("py -3.11", h)


class Stall30Tests(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(loop.STALL_STOP_ROUNDS, 30)
        self.assertEqual(loop.STALL_WARN_ROUNDS, 10)
        self.assertEqual(loop.MAX_TOOL_ITERATIONS, 80)

    def test_stop_at_30_not_29(self):
        self.assertFalse(loop.should_stall_stop(True, 29))
        self.assertTrue(loop.should_stall_stop(True, 30))
        self.assertFalse(loop.should_stall_stop(False, 30))
        self.assertFalse(loop.should_stall_stop(False, 80))


if __name__ == "__main__":
    unittest.main()
