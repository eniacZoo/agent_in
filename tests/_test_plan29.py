#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan29：cp314 轮子、任务脚本算产出并保留、preview_page、文档 3.3。"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import config
import loop
import taskdir
import tools
import ui
import web_preview


class VendorAbiTests(unittest.TestCase):
    def test_no_cp311_extensions(self):
        bad = []
        good = []
        vendor = os.path.join(_ROOT, "vendor")
        for dirpath, _dirs, files in os.walk(vendor):
            for name in files:
                if not name.endswith(".pyd"):
                    continue
                if "cp311" in name:
                    bad.append(name)
                if "cp314" in name:
                    good.append(name)
        self.assertEqual(bad, [])
        for needle in ("etree.cp314", "_greenlet.cp314", "_imaging.cp314", "_pydantic_core.cp314"):
            self.assertTrue(any(needle in name for name in good), needle)
        self.assertTrue(any(name.startswith("_multiarray_umath.cp314") for name in good))

    @unittest.skipUnless(sys.version_info[:2] == (3, 14), "wheels are cp314")
    def test_import_native_on_314(self):
        vendor = os.path.join(_ROOT, "vendor")
        sys.path.insert(0, vendor)
        try:
            import greenlet
            import lxml
            import numpy
            import PIL
            import pydantic_core
        finally:
            if sys.path and sys.path[0] == vendor:
                sys.path.pop(0)
        self.assertTrue(lxml.__file__)
        self.assertTrue(greenlet.__file__)
        self.assertTrue(numpy.__file__)
        self.assertTrue(PIL.__file__)
        self.assertTrue(pydantic_core.__file__)

    def test_python_tool_uses_sys_executable(self):
        seen = {}

        def boom(argv, **_kwargs):
            seen["argv"] = list(argv)
            raise OSError("stop")

        td = tempfile.mkdtemp(prefix="p29_py_")
        old_wd, old_sid = tools.WORK_DIR, tools.SESSION_ID
        tools.WORK_DIR = td
        tools.SESSION_ID = "p29py"
        try:
            with patch.object(tools.subprocess, "Popen", side_effect=boom):
                result = tools._exec_python({"code": "print(1)\n"})
            self.assertEqual(seen["argv"][0], sys.executable)
            self.assertTrue(seen["argv"][1].endswith(".py"))
            self.assertNotIn("-3.11", " ".join(seen["argv"]))
            self.assertIn("脚本保留至本任务结束", result)
            self.assertTrue(loop.python_script_counts(result))
        finally:
            tools.WORK_DIR = old_wd
            tools.SESSION_ID = old_sid
            shutil.rmtree(td, ignore_errors=True)


class ScriptOutputTests(unittest.TestCase):
    def test_temp_script_counts_json_does_not(self):
        wd = tempfile.mkdtemp(prefix="p29_out_")
        try:
            py = os.path.join(wd, "temp", "a.py")
            js = os.path.join(wd, "temp", "notes.json")
            self.assertTrue(loop.counts_as_output("write_file", py, True, wd))
            self.assertTrue(loop.counts_as_output("edit_file", os.path.join(wd, "temp", "run.ps1"), True, wd))
            self.assertFalse(loop.counts_as_output("write_file", js, True, wd))
            self.assertFalse(loop.counts_as_output("write_file", py, False, wd))
            self.assertFalse(loop._is_product_write(py, wd))
            self.assertTrue(loop.counts_as_output("write_file", os.path.join(wd, "out.xlsx"), True, wd))
        finally:
            shutil.rmtree(wd, ignore_errors=True)

    def test_qwen_cap_stays_200(self):
        self.assertEqual(config._PROFILES["qwen"]["max_tool_iterations"], 200)

    def test_success_keeps_script_until_clean_task(self):
        td = tempfile.mkdtemp(prefix="p29_keep_")
        old_wd, old_sid = tools.WORK_DIR, tools.SESSION_ID
        tools.WORK_DIR = td
        tools.SESSION_ID = "p29keep"
        try:
            result = tools._exec_python({"code": "print(1 + 1)\n"})
            self.assertIn("2", result)
            self.assertIn("脚本保留至本任务结束", result)
            run = os.path.join(taskdir.task_dir(td, "p29keep", create=False), "run")
            left = [n for n in os.listdir(run) if n.endswith(".py")]
            self.assertEqual(len(left), 1)
            taskdir.clean_task(td, "p29keep")
            left = [n for n in os.listdir(run) if n.endswith(".py")] if os.path.isdir(run) else []
            self.assertEqual(left, [])
        finally:
            tools.WORK_DIR = old_wd
            tools.SESSION_ID = old_sid
            shutil.rmtree(td, ignore_errors=True)


class PreviewPageTests(unittest.TestCase):
    def test_connection_copy_stops_without_patch_phrase(self):
        msg = web_preview.connection_message("http://127.0.0.1:9/")
        self.assertIn("停止", msg)
        self.assertNotIn("改 Playwright", msg)
        self.assertNotIn("改 Playwright", web_preview.edge_missing_message())

    def test_tool_uses_fixed_script(self):
        seen = {}
        payload = json.dumps({
            "ok": False,
            "stop": True,
            "error": web_preview.connection_message("http://127.0.0.1:9/"),
        }, ensure_ascii=False)

        def fake_spawn(argv, command, script=None):
            seen["argv"] = list(argv)
            job = tools._Job("j9", command, None, os.devnull)
            job.ended = time.time()
            job.started = job.ended - 1
            job.returncode = 1
            return job

        with patch.object(tools, "_spawn_process", fake_spawn), \
                patch.object(tools, "_wait_job"), \
                patch.object(tools, "_read_job_new", return_value=payload):
            result = tools._exec_preview_page({"url": "http://127.0.0.1:9/"})
        self.assertEqual(seen["argv"][0], sys.executable)
        self.assertTrue(os.path.basename(seen["argv"][1]) == "web_preview.py")
        self.assertIn("停止", result)
        self.assertNotIn("改 Playwright", result)

    def test_rejects_non_http_url(self):
        result = tools._exec_preview_page({"url": "file:///tmp/a.html"})
        self.assertTrue(result.startswith("Error"))

    def test_no_vision_includes_excerpt(self):
        payload = {
            "ok": True,
            "title": "Hi",
            "errors": [],
            "excerpt": "hello body",
            "shots": {},
        }
        with patch.object(tools, "_vision_enabled", return_value=False):
            text = tools._format_preview_payload(payload)
        self.assertIn("hello body", text)
        self.assertIn("不支持图像分析", text)


class DocSyncTests(unittest.TestCase):
    def test_version_and_docs(self):
        self.assertEqual(ui.APP_VERSION, "3.3")

        def read(path):
            with open(path, encoding="utf-8") as f:
                return f.read()

        readme = read(os.path.join(_ROOT, "README.md"))
        tree = read(os.path.join(_ROOT, "docs", "目录结构.md"))
        vendor_doc = read(os.path.join(_ROOT, "vendor", "README.md"))
        log = read(os.path.join(_ROOT, "docs", "CHANGELOG.md"))
        self.assertIn("3.3", readme)
        self.assertIn("preview_page", readme)
        self.assertIn("3.3", tree)
        self.assertIn("preview_page", tree)
        self.assertIn("plan29.0.md", tree)
        self.assertIn("3.14", vendor_doc)
        self.assertNotIn("py -3.11", vendor_doc)
        self.assertIn("## 3.3", log)
        self.assertTrue(os.path.isfile(os.path.join(_ROOT, "docs", "plan29.0.md")))


if __name__ == "__main__":
    unittest.main()
