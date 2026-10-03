#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vendor Python 3.11 resolver + stall 30."""
import os
import sys
import unittest
from unittest.mock import patch

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import loop
import tools


class VendorPythonTests(unittest.TestCase):
    def tearDown(self):
        tools._reset_vendor_python_cache()

    def test_current_311_returns_executable(self):
        tools._reset_vendor_python_cache()
        with patch.object(tools.sys, "version_info", (3, 11, 9, "final", 0)):
            with patch.object(tools.sys, "executable", r"C:\Python311\python.exe"):
                with patch.object(tools.subprocess, "run") as run:
                    self.assertEqual(tools._vendor_python(), r"C:\Python311\python.exe")
                    run.assert_not_called()

    def test_py_311_launcher(self):
        tools._reset_vendor_python_cache()
        fake = r"C:\Users\me\AppData\Local\Programs\Python\Python311\python.exe"

        class R:
            returncode = 0
            stdout = fake + "\n"

        with patch.object(tools.sys, "version_info", (3, 14, 0, "final", 0)):
            with patch.object(tools.subprocess, "run", return_value=R()) as run:
                with patch.object(tools.os.path, "isfile", return_value=True):
                    self.assertEqual(tools._vendor_python(), fake)
                    run.assert_called_once()
                    self.assertEqual(run.call_args[0][0][:2], ["py", "-3.11"])

    def test_missing_311_returns_none(self):
        tools._reset_vendor_python_cache()
        with patch.object(tools.sys, "version_info", (3, 14, 0, "final", 0)):
            with patch.object(tools.subprocess, "run", side_effect=FileNotFoundError):
                self.assertIsNone(tools._vendor_python())

    def test_shell_env_prepends_path(self):
        fake = r"D:\py311\python.exe"
        with patch.object(tools, "_vendor_python", return_value=fake):
            env = tools._shell_env()
            prefix = os.path.dirname(os.path.abspath(fake)) + os.pathsep
            self.assertTrue(env["PATH"].startswith(prefix), env["PATH"][:80])

    def test_abi_hint_when_not_311(self):
        with patch.object(tools.sys, "version_info", (3, 14, 0, "final", 0)):
            h = tools._vendor_abi_hint()
            self.assertIn("CPython 3.11", h)
            self.assertIn("3.14", h)
            self.assertIn("py -3.11", h)

    def test_abi_hint_silent_on_311(self):
        with patch.object(tools.sys, "version_info", (3, 11, 9, "final", 0)):
            self.assertEqual(tools._vendor_abi_hint(), "")


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
