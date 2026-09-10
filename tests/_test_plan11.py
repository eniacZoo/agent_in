#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan11.0：shell 字节解码、view_image 缩图、Error 工具行、打满轮次警告。"""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import llm
import loop
import tools
import ui
import vision


def _tiny_png(path, w=32, h=24):
    vision._try_pil()
    from PIL import Image
    Image.new("RGB", (w, h), (200, 30, 30)).save(path, "PNG")


class Plan11Tests(unittest.TestCase):
    def test_decode_bytes_never_raises(self):
        self.assertEqual(tools._decode_bytes(b"hello"), "hello")
        self.assertEqual(tools._decode_bytes(b""), "")
        # 非法 GBK/UTF-8 混合：替换，不抛
        out = tools._decode_bytes(b"ok\xae\xff listing")
        self.assertIn("ok", out)
        self.assertIsInstance(out, str)

    def test_shell_reads_bytes_not_text(self):
        captured = {}

        class Proc:
            returncode = 0
            stdout = b"name \xae dir"
            stderr = b""

        def fake_run(*_a, **kw):
            captured.update(kw)
            return Proc()

        old = subprocess.run
        subprocess.run = fake_run
        try:
            r = tools._exec_shell({"command": "dir"})
        finally:
            subprocess.run = old
        self.assertTrue(captured.get("capture_output"))
        self.assertNotEqual(captured.get("text"), True)
        self.assertIn("$ dir", r)
        self.assertNotIn("Error:", r)

    def test_maybe_downscale_passthrough_small(self):
        td = tempfile.mkdtemp(prefix="agent_in_p11_")
        p = os.path.join(td, "s.png")
        _tiny_png(p)
        data, mime, note = vision.maybe_downscale(p)
        self.assertEqual(note, "")
        self.assertEqual(len(data), os.path.getsize(p))
        self.assertIn("png", mime)
        url, note2 = vision.encode_for_llm(p)
        self.assertTrue(url.startswith("data:image/"))
        self.assertEqual(note2, "")

    def test_maybe_downscale_when_over_limit(self):
        td = tempfile.mkdtemp(prefix="agent_in_p11_")
        p = os.path.join(td, "big.bmp")
        vision._try_pil()
        from PIL import Image
        Image.new("RGB", (240, 180), (10, 80, 160)).save(p, "BMP")
        raw_len = os.path.getsize(p)
        self.assertGreater(raw_len, 10000)
        data, mime, note = vision.maybe_downscale(p, max_bytes=8000, max_edge=64)
        self.assertEqual(mime, "image/jpeg")
        self.assertTrue(note)
        self.assertIn("已缩到", note)
        self.assertLessEqual(len(data), 8000)
        self.assertEqual(data[:2], b"\xff\xd8")

    def test_view_image_comma_hint(self):
        tools.PENDING_IMAGES.clear()
        r = tools.execute(
            "view_image",
            {"path": r"D:\no_such\6月23日洪湖公园花鸟，DSC01171.JPG"},
        )
        self.assertTrue(r.startswith("Error:"), r)
        self.assertIn("中文逗号", r)

    def test_print_tool_status_shows_error(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ui.print_tool_status(
                "view_image",
                {"path": "x.jpg"},
                "allow",
                "Error: 图片过大: 15.5MB（上限 10MB）",
            )
        out = buf.getvalue().strip()
        self.assertEqual(out.count("\n"), 0)
        self.assertIn("Error:", out)
        self.assertIn(ui.ICO_FAIL, out)
        out.encode("gbk")

    def test_max_rounds_prints_warning(self):
        n = {"i": 0}

        def fake_chat(messages, tools=None, stream=True, model=None, max_tokens=None, images=None):
            n["i"] += 1
            if n["i"] == 1:
                yield {
                    "type": "tool_call",
                    "id": "c1",
                    "name": "read_file",
                    "arguments": {"path": "missing_plan11.txt"},
                }
                yield {"type": "usage", "data": {"prompt_tokens": 3, "completion_tokens": 1}}
                yield {"type": "done"}
            else:
                yield {"type": "text", "content": "should-not-need"}
                yield {"type": "done"}

        old_chat, old_max = llm.chat, loop.MAX_TOOL_ITERATIONS
        llm.chat = fake_chat
        loop.MAX_TOOL_ITERATIONS = 0
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                text, *_rest = loop.agent_loop(
                    [{"role": "user", "content": "x"}],
                    _ROOT,
                    verbose=True,
                    interactive=False,
                )
        finally:
            llm.chat = old_chat
            loop.MAX_TOOL_ITERATIONS = old_max
        self.assertIn("最大工具调用轮数", text)
        self.assertIn("最大工具调用轮数", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
