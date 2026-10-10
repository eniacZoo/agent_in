#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan28：技能子进程解码、后台首包、网页验收失败一次就停。"""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import skill_manager
import tools


class SkillDecodeTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="p28_skill_")
        self.old_wd = tools.WORK_DIR
        self.old_sid = tools.SESSION_ID
        self.old_skills = skill_manager.SKILLS_DIR
        self.old_gate = skill_manager._skill_security_gate
        tools.WORK_DIR = self.td
        tools.SESSION_ID = "p28"
        os.environ["WORK_DIR"] = self.td
        skill_dir = os.path.join(self.td, "skills", "emit_bytes")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "skill.json"), "w", encoding="utf-8") as f:
            json.dump({
                "name": "emit_bytes",
                "entry": "main.py",
                "language": "python",
                "params": {},
            }, f)
        script = (
            "import os, sys\n"
            "sys.stdout.buffer.write(b'ping\\x80\\n')\n"
            "io = os.environ.get('PYTHONIOENCODING', '')\n"
            "temp = os.environ.get('TASK_TEMP', '')\n"
            "sys.stdout.buffer.write(('IO=%s\\nTEMP=%s\\n' % (io, temp)).encode('ascii'))\n"
        )
        with open(os.path.join(skill_dir, "main.py"), "w", encoding="utf-8", newline="\n") as f:
            f.write(script)
        skill_manager.SKILLS_DIR = skill_manager.Path(self.td) / "skills"
        skill_manager._skill_security_gate = lambda *a, **k: {"approved": True, "message": ""}

    def tearDown(self):
        skill_manager.SKILLS_DIR = self.old_skills
        skill_manager._skill_security_gate = self.old_gate
        tools.WORK_DIR = self.old_wd
        tools.SESSION_ID = self.old_sid
        os.environ.pop("WORK_DIR", None)
        shutil.rmtree(self.td, ignore_errors=True)

    def test_non_gbk_bytes_return_text_without_decode_error(self):
        err = io.StringIO()
        with redirect_stderr(err):
            out = skill_manager.execute_skill("emit_bytes", {})
        self.assertIn("ping", out)
        self.assertIn("IO=utf-8", out)
        temp = tools.task_temp()
        self.assertIn("TEMP=" + temp, out)
        self.assertNotIn("UnicodeDecodeError", out)
        self.assertNotIn("UnicodeDecodeError", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())


class BackgroundFirstByteTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="p28_job_")
        self.old_wd = tools.WORK_DIR
        self.old_sid = tools.SESSION_ID
        self.old_jobs = dict(tools.JOBS)
        tools.WORK_DIR = self.td
        tools.SESSION_ID = "p28"
        tools.JOBS.clear()

    def tearDown(self):
        tools.kill_all_jobs()
        tools.JOBS.clear()
        tools.JOBS.update(self.old_jobs)
        tools.WORK_DIR = self.old_wd
        tools.SESSION_ID = self.old_sid
        shutil.rmtree(self.td, ignore_errors=True)

    def test_wait_first_output_returns_when_bytes_arrive(self):
        log = os.path.join(self.td, "early.log")
        open(log, "wb").close()
        job = tools._Job("jx", "x", None, log)

        def write_later():
            time.sleep(0.2)
            with open(log, "wb") as f:
                f.write(b"ready\n")

        threading.Thread(target=write_later, daemon=True).start()
        t0 = time.time()
        tools._wait_first_output(job, 2.0)
        self.assertLess(time.time() - t0, 1.0)
        self.assertGreaterEqual(os.path.getsize(log), 5)

    def test_wait_first_output_caps_at_about_one_second(self):
        log = os.path.join(self.td, "empty.log")
        open(log, "wb").close()
        job = tools._Job("jy", "y", None, log)
        t0 = time.time()
        tools._wait_first_output(job, tools._FIRST_OUTPUT_WAIT)
        elapsed = time.time() - t0
        self.assertGreaterEqual(elapsed, 0.9)
        self.assertLess(elapsed, 1.6)

    def test_empty_handoff_mentions_wait_sec(self):
        log = os.path.join(self.td, "hand.log")
        open(log, "wb").close()
        job = tools._Job("jz", "sleep", None, log)
        text = tools._format_running(job, "sleep")
        self.assertIn("日志可能还没写入", text)
        self.assertIn("wait_sec", text)
        self.assertNotIn("没有新输出", text)

    def test_background_shell_waits_for_first_byte_then_hints(self):
        t0 = time.time()
        r = tools._exec_shell({"command": "Start-Sleep -Seconds 20", "background": True})
        elapsed = time.time() - t0
        self.assertIn("已转入后台", r)
        self.assertIn("日志可能还没写入", r)
        self.assertIn("wait_sec", r)
        self.assertNotIn("没有新输出", r)
        self.assertGreaterEqual(elapsed, 0.9)
        self.assertLess(elapsed, 3.0)
        jid = r.split("任务 ", 1)[1].split(" ", 1)[0]
        t1 = time.time()
        out = tools._exec_job({"action": "output", "job_id": jid})
        self.assertLess(time.time() - t1, 0.4)
        self.assertIn("没有新输出", out)


class WebAcceptSkillTests(unittest.TestCase):
    def test_fail_once_rule_is_written(self):
        path = os.path.join(_ROOT, "skills", "网页验收.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("preview_page", text)
        self.assertIn("报告原因并停", text)
        self.assertIn("不要自己写 Playwright", text)
        self.assertIn("1440", text)
        self.assertIn("390", text)


if __name__ == "__main__":
    unittest.main()
