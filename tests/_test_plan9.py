#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan9.0：技能是文件；工具表仍 5 个；vendor 不进核心。"""
import io
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from xml.sax.saxutils import escape

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import commands
import skill_manager
import tools
import usage


def _write_xlsx(path, rows):
    """最小 inlineStr xlsx，供 read_xlsx 测试。"""
    def sheet_xml():
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
            "<sheetData>",
        ]
        for r_i, row in enumerate(rows, 1):
            cells = []
            for c_i, val in enumerate(row):
                ref = "{}{}".format(chr(ord("A") + c_i), r_i)
                cells.append(
                    '<c r="{}" t="inlineStr"><is><t>{}</t></is></c>'.format(
                        ref, escape(str(val))
                    )
                )
            lines.append('<row r="{}">{}</row>'.format(r_i, "".join(cells)))
        lines.append("</sheetData></worksheet>")
        return "\n".join(lines).encode("utf-8")

    ct = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""
    rels = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    wb_rels = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
    wb = b"""<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml())


class Plan9SkillTests(unittest.TestCase):
    def _state(self):
        return commands.CliState(".", None, "deadbeef", [], 0, 0, 0, usage.Tracker("deadbeef"))

    def test_tools_are_seven(self):
        names = [t["function"]["name"] for t in tools.TOOLS]
        self.assertEqual(
            names,
            ["read_file", "write_file", "edit_file", "shell", "view_image", "glob", "grep"],
        )
        self.assertFalse(any(n.startswith("skill_") for n in names))
        self.assertNotIn("load_skill", names)

    def test_markdown_flow_exists(self):
        names = [s["name"] for s in skill_manager.list_markdown()]
        self.assertIn("周报转docx", names)
        text, err = skill_manager.read_markdown("周报转docx")
        self.assertIsNone(err)
        self.assertIn("read_file", text)
        self.assertIn("如何验收", text)

    def test_read_skill_path_traversal(self):
        text, err = skill_manager.read_markdown("../config.py")
        self.assertIsNone(text)
        self.assertIsNotNone(err)

    def test_read_xlsx_script(self):
        wd = tempfile.mkdtemp(prefix="agent_in_xlsx_")
        xlsx = os.path.join(wd, "t.xlsx")
        _write_xlsx(xlsx, [["品名", "数量"], ["钢笔", "3"]])
        entry = str((Path(_ROOT) / "skills") / "read_xlsx" / "main.py")
        old = os.environ.get("WORK_DIR")
        os.environ["WORK_DIR"] = wd
        try:
            out = skill_manager.execute_skill(
                "read_xlsx", {"path": xlsx},
                confirm_fn=lambda _p: True,
                input_fn=lambda _p: entry,
            )
        finally:
            if old is None:
                os.environ.pop("WORK_DIR", None)
            else:
                os.environ["WORK_DIR"] = old
        self.assertNotIn("被拒绝", out)
        self.assertIn("品名", out)
        self.assertIn("钢笔", out)

    def test_pythonpath_includes_vendor(self):
        src = (Path(_ROOT) / "skill_manager.py").read_text(encoding="utf-8")
        self.assertIn("PYTHONPATH", src)
        self.assertIn("VENDOR_DIR", src)
        self.assertTrue(((Path(_ROOT) / "vendor") / "README.md").is_file())

    def test_vendor_office_imports(self):
        vendor = str((Path(_ROOT) / "vendor"))
        sys.path.insert(0, vendor)
        try:
            import openpyxl
            import docx
            import pypdf
            import pandas
            import pptx
            import playwright
            from playwright.sync_api import sync_playwright
        finally:
            if sys.path and sys.path[0] == vendor:
                sys.path.pop(0)
        self.assertTrue(hasattr(openpyxl, "load_workbook"))
        self.assertTrue(hasattr(docx, "Document"))
        self.assertTrue(hasattr(pypdf, "PdfReader"))
        self.assertTrue(hasattr(pandas, "DataFrame"))
        self.assertTrue(hasattr(pptx, "Presentation"))
        self.assertTrue(callable(sync_playwright))

    def test_slash_read_skill(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertTrue(commands.handle("/read-skill 周报转docx", self._state()))
        self.assertIn("周报转 Word", buf.getvalue())

    def test_ls_skills_lists_both(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertTrue(commands.handle("/ls skills", self._state()))
        text = buf.getvalue()
        self.assertIn("周报转docx", text)
        self.assertIn("read_xlsx", text)

    def test_no_auto_write_skill(self):
        src = (Path(_ROOT) / "agent.py").read_text(encoding="utf-8")
        self.assertNotIn("记住", src)
        self.assertNotIn("save_skill", src)


if __name__ == "__main__":
    unittest.main()
