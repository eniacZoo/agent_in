#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan13.0：读网页 skill、temp/ 可删、根目录递归删仍拦。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import loop
import tool_guard
import tools


class Plan13Tests(unittest.TestCase):
    def test_temp_recurse_delete_allowed(self):
        wd = tempfile.mkdtemp(prefix="agent_in_p13_")
        temp = Path(wd) / "temp"
        temp.mkdir()
        target = temp / "foo"
        cmd = 'Remove-Item -Recurse -Force "{}"'.format(target)
        v = tool_guard.assess_call("shell", {"command": cmd}, wd, safe_mode=True)
        self.assertEqual(v["action"], tool_guard.ACTION_ALLOW, v)
        cmd_rel = r"Remove-Item -Recurse -Force temp\foo"
        v2 = tool_guard.assess_call("shell", {"command": cmd_rel}, wd, safe_mode=True)
        self.assertEqual(v2["action"], tool_guard.ACTION_ALLOW, v2)

    def test_root_tmp_recurse_still_blocked(self):
        wd = tempfile.mkdtemp(prefix="agent_in_p13_")
        v = tool_guard.assess_call(
            "shell",
            {"command": "Remove-Item -Recurse -Force tmp"},
            wd,
            safe_mode=True,
        )
        self.assertEqual(v["action"], tool_guard.ACTION_BLOCK)
        v2 = tool_guard.assess_shell("Remove-Item -Recurse -Force tmp", safe_mode=True)
        self.assertEqual(v2["action"], tool_guard.ACTION_BLOCK)

    def test_web_skill_and_prompt(self):
        p = Path(__file__).with_name("skills") / "读网页.md"
        self.assertTrue(p.is_file())
        self.assertIn("网页", loop.DEFAULT_SYSTEM_PROMPT)
        self.assertIn("temp/", loop.DEFAULT_SYSTEM_PROMPT)

    def test_ensure_temp_dir(self):
        wd = tempfile.mkdtemp(prefix="agent_in_p13_")
        d = tools.ensure_temp_dir(wd)
        self.assertTrue(os.path.isdir(d))
        self.assertEqual(os.path.basename(d), "temp")


if __name__ == "__main__":
    unittest.main()
