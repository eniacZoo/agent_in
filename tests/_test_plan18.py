#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan18.0：debug 报告含 TOOL_CALL / SKILL_LOAD；reasoning_content 回传。"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import debug


class Plan18Tests(unittest.TestCase):
    def setUp(self):
        self.sid = "testdbg01"
        d = debug.dir_for(self.sid)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    def tearDown(self):
        debug.stop()
        d = debug._ROOT / self.sid
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    def test_report_timeline(self):
        debug.start(self.sid)
        debug.emit("USER", {"content": "扩表"})
        debug.emit("THINK", {"content": "先读 skill"})
        debug.emit("TOOL_CALL", {"name": "read_file", "args": "skills/xlsx.md"})
        debug.emit("SKILL_LOAD", {"name": "xlsx", "path": "skills/xlsx.md"})
        debug.emit("TOOL_CALL", {"name": "shell", "args": "py temp/a.py"})
        debug.emit("TOOL_CALL", {"name": "shell", "args": "py temp/a.py"})
        p = debug.write_report(self.sid)
        text = Path(p).read_text(encoding="utf-8")
        self.assertIn("USER", text)
        self.assertIn("THINK", text)
        self.assertIn("TOOL_CALL", text)
        self.assertIn("SKILL_LOAD", text)
        self.assertIn("xlsx", text)
        self.assertIn("shell: 2", text)

    def test_skill_path(self):
        self.assertTrue(debug.is_skill_path("skills/xlsx.md"))
        self.assertTrue(debug.is_skill_path(r"E:\x\skills\docx.md"))
        self.assertFalse(debug.is_skill_path("temp/foo.py"))

    def test_off_no_write(self):
        debug.ENABLED = False
        debug.SESSION_ID = ""
        debug.emit("USER", {"content": "nope"})
        self.assertFalse((debug._ROOT / "unknown" / "events.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
