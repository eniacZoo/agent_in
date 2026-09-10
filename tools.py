#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools.py — Agent 工具定义与执行

7 个工具：read_file, write_file, edit_file, shell, view_image, glob, grep
零依赖，跨平台（Windows / Linux / macOS）
"""
import os
import re
import shutil
import subprocess
import sys
import time
import locale
import fnmatch
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import logger
import skill_manager
import vision
import tool_guard
import approval
import audit


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
WORK_DIR = os.environ.get("WORK_DIR", os.getcwd())
SAFE_MODE = False  # 由 agent 入口赋值（v6.0 陷阱 E）
SHELL_TIMEOUT = 60  # 默认 shell 超时
MAX_READ_LINES = 500  # read_file 最大行数
MAX_OUTPUT_CHARS = 10_000  # shell 输出上限
OFFICE_MAX_COLS = 80
OFFICE_SUMMARY_MAX = 8000
GLOB_MAX = 200
GREP_PER_FILE = 20
GREP_MAX = 100
_SKIP_DIRS = frozenset({"vendor", ".git", "__pycache__", "node_modules"})
_PY_SCRIPT = re.compile(
    r'(?i)(?:python(?:w)?(?:\d+(?:\.\d+)*)?|py)(?:\.exe)?'
    r'["\']?(?:\s+-[^\s"\']+)*\s+["\']?([^\s"\']+\.py)'
)
_MAX_COL_RANGE = re.compile(r"range\s*\([^)]*max_column")
_ITER_ROWS_BARE = re.compile(r"iter_rows\s*\(\s*\)")


def ensure_temp_dir(work_dir=None):
    """保证 {work_dir}/temp 存在，返回绝对路径。"""
    wd = os.path.abspath(work_dir or WORK_DIR)
    d = os.path.join(wd, "temp")
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# OpenAI Function Calling Schema
# ---------------------------------------------------------------------------
BASE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file. Text files return numbered lines. xlsx/docx/pptx/pdf return a bounded structure summary, not raw bytes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path (absolute, or relative to working directory)",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to read (1-based, inclusive). Optional.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to read (1-based, inclusive). Optional.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file or overwrite an existing file with the given content. Parent directories are created automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path (absolute, or relative to working directory)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full content to write to the file",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Find and replace text in a file. ALL occurrences of old_text are replaced with new_text. old_text must match exactly (including whitespace).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Exact text to find (must be unique enough to identify the target)",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Replacement text",
                    },
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "Execute a shell command and return its output. Use for: running scripts, listing files (ls/dir), git operations, grep/find, package management, etc. On Windows the shell is PowerShell: separate commands with ';', not '&&' or '&'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 60, max 300)",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_image",
            "description": (
                "Load an image file so you can visually analyze it. "
                "Use when the user asks to look at, describe, OCR, read text from, "
                "or analyze an image (screenshot, diagram, chart, photo). "
                "Supported formats: png, jpg, jpeg, gif, webp, bmp. Max 10MB per image."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Image file path (absolute, or relative to working directory)",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "List files under the working directory matching a glob pattern (e.g. *.xlsx, **/*.py). Does not leave the work dir.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern (required)",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search (default: working directory)",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents under the working directory. pattern is a regex; invalid regex is treated as a literal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex or literal to search for",
                    },
                    "glob": {
                        "type": "string",
                        "description": "File glob (default *.{py,md,txt,json,csv})",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search (default: working directory)",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
]


def _load_tools():
    """构建 TOOLS 列表 = 固定基础工具（技能不注册为 function）。"""
    return list(BASE_TOOLS)


# 初始加载（agent 启动时调用 reload_tools 刷新）
TOOLS = _load_tools()


def reload_tools():
    """刷新 TOOLS。v7.0 起技能不再进入工具表，结果仍是 BASE_TOOLS。"""
    global TOOLS
    TOOLS = _load_tools()


# ---------------------------------------------------------------------------
# 多模态：待发送图片缓冲（v3.0）
# ---------------------------------------------------------------------------
# view_image 执行后，data_url 暂存于此，由 agent 在下一轮 LLM 调用时取出并清空。
PENDING_IMAGES = []
LAST_VERDICT = "allow"  # 最近一次工具审批：allow / confirmed / rejected / blocked


def drain_pending_images():
    """取出并清空所有待发送图片（agent 每轮 LLM 调用前调用）。"""
    imgs = list(PENDING_IMAGES)
    PENDING_IMAGES.clear()
    return imgs


# ---------------------------------------------------------------------------
# Shell 检测（跨平台）
# ---------------------------------------------------------------------------
def _detect_shell():
    """
    返回 (shell_cmd_prefix: list[str], is_windows: bool)
    例：Linux → ["bash", "-c"], Windows → ["powershell", "-NoProfile", "-Command"]
    """
    if sys.platform == "win32":
        # 优先 pwsh (PowerShell 7)，其次 powershell (5.x)，最后 cmd
        if shutil.which("pwsh"):
            return ["pwsh", "-NoProfile", "-Command"]
        elif shutil.which("powershell"):
            return ["powershell", "-NoProfile", "-Command"]
        else:
            return ["cmd", "/c"]
    else:
        if shutil.which("bash"):
            return ["bash", "-c"]
        elif shutil.which("sh"):
            return ["sh", "-c"]
        else:
            return ["/bin/sh", "-c"]


def shell_name():
    """返回当前 shell 可执行名，用于写进 system prompt。"""
    return _detect_shell()[0]


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------
def _resolve_path(path):
    """将相对路径解析为绝对路径（相对于 WORK_DIR）。"""
    p = Path(path)
    if not p.is_absolute():
        p = Path(WORK_DIR) / p
    return p.resolve()


# ---------------------------------------------------------------------------
# 工具执行
# ---------------------------------------------------------------------------
def execute(tool_name, args, confirm_fn=None, input_fn=None, session_id=None):
    """
    执行工具，返回结果字符串（喂回给 LLM）。
    confirm_fn: 确认函数 (prompt: str) -> bool，用于安全审批。
    input_fn:   输入函数 () -> str，用于强确认键入 token。
    session_id: 审计"谁"；非交互/未知 → "-"。
    """
    t0 = time.time()
    global LAST_VERDICT
    LAST_VERDICT = "allow"
    ensure_temp_dir(WORK_DIR)
    if not isinstance(args, dict):
        args = {}
    logger.info("tool_call", {"name": tool_name, "args_keys": list(args.keys())})

    blocked = _security_gate(tool_name, args, confirm_fn, input_fn, session_id)
    if blocked is not None:
        return blocked

    # ---- 分发（原 try/except 体）----
    try:
        if tool_name == "read_file":
            result = _exec_read_file(args)
        elif tool_name == "write_file":
            result = _exec_write_file(args)
        elif tool_name == "edit_file":
            result = _exec_edit_file(args)
        elif tool_name == "shell":
            result = _exec_shell(args)
        elif tool_name == "view_image":
            result = _exec_view_image(args)
        elif tool_name == "glob":
            result = _exec_glob(args)
        elif tool_name == "grep":
            result = _exec_grep(args)
        elif tool_name.startswith("skill_"):
            skill_name = tool_name[len("skill_"):]
            result = skill_manager.execute_skill(
                skill_name, args, session_id=session_id,
                confirm_fn=confirm_fn, input_fn=input_fn)
            logger.info("skill_executed", {"name": skill_name,
                                            "elapsed_ms": int((time.time() - t0) * 1000)})
        else:
            result = f"Error: Unknown tool '{tool_name}'"
    except Exception as e:
        result = f"Error: {type(e).__name__}: {e}"
        logger.warn("tool_error", {"name": tool_name, "error": str(e)[:200]})

    elapsed_ms = int((time.time() - t0) * 1000)
    output_size = len(result) if result else 0
    logger.info("tool_result", {"name": tool_name, "elapsed_ms": elapsed_ms,
                                "output_size": output_size})

    return result


def _apply_verdict(verdict, tool_name, source, confirm_fn, input_fn, session_id):
    """审批网关。ALLOW 返回 None；拒绝返回给 LLM 的说明字符串。"""
    global LAST_VERDICT
    if verdict.get("action") == tool_guard.ACTION_ALLOW:
        LAST_VERDICT = "allow"
        return None
    ctx = {
        "session_id": session_id,
        "tool": tool_name,
        "source": source,
        "rule_id": ";".join(verdict.get("findings", [])),
    }
    decision = approval.resolve(verdict, confirm_fn, input_fn, ctx)
    LAST_VERDICT = decision.get("action") or "rejected"
    if not decision["approved"]:
        msg = "操作被拒绝（{}）：{}".format(decision["action"], decision["reason"])
        logger.warn("tool_blocked", {"name": tool_name, "reason": decision["reason"]})
        return msg
    return None


def _security_gate(tool_name, args, confirm_fn, input_fn, session_id):
    """路径/命令守卫 + 覆盖确认 + 多处替换确认。拒绝时返回字符串。"""
    if tool_name in ("shell", "write_file", "edit_file"):
        guard_args = args
        if tool_name in ("write_file", "edit_file"):
            # 相对路径按 WORK_DIR 解析后再判定，避免相对 cwd 误判出界
            guard_args = dict(args)
            guard_args["path"] = str(_resolve_path(args.get("path", "")))
        verdict = tool_guard.assess_call(tool_name, guard_args, WORK_DIR, SAFE_MODE)
        source = {"shell": "shell", "write_file": "file_write",
                  "edit_file": "file_edit"}[tool_name]
        blocked = _apply_verdict(verdict, tool_name, source, confirm_fn, input_fn, session_id)
        if blocked is not None:
            return blocked

    if tool_name == "write_file":
        path = _resolve_path(args.get("path", ""))
        if not tool_guard.is_under_temp(str(path), WORK_DIR):
            blocked = _apply_verdict(
                tool_guard.assess_overwrite(str(path)),
                tool_name, "file_write", confirm_fn, input_fn, session_id)
            if blocked is not None:
                return blocked

    if tool_name == "edit_file":
        path = _resolve_path(args.get("path", ""))
        if tool_guard.is_under_temp(str(path), WORK_DIR):
            return None
        old_text = args.get("old_text", "")
        if path.exists() and path.is_file() and old_text:
            content, _enc = _read_text(path)
            count = content.count(old_text)
            blocked = _apply_verdict(
                tool_guard.assess_multi_replace(count, str(path)),
                tool_name, "file_edit", confirm_fn, input_fn, session_id)
            if blocked is not None:
                return blocked

    return None


# ---------------------------------------------------------------------------
# view_image（v3.0 多模态）
# ---------------------------------------------------------------------------
def _exec_view_image(args):
    """
    加载图片，编码为 base64 data URL，存入 PENDING_IMAGES 供下一轮 LLM 使用。
    返回给 LLM 的确认文本。
    """
    path = args.get("path", "")
    resolved = _resolve_path(path)

    # H4: vision 能力降级（读 llm.CAPABILITY，函数内延迟 import 避免加载期耦合）
    try:
        import llm as _llm
        if _llm.CAPABILITY.get("vision") is False:
            logger.warn("view_image_degraded", {"path": str(resolved)})
            return (
                "当前模型不支持图像分析（已自动降级）。"
                "无法查看/描述该图片，请改用其他文本方式处理。"
            )
    except Exception:
        pass

    # 获取图片信息
    try:
        info = vision.image_info(str(resolved))
    except FileNotFoundError as e:
        msg = str(e)
        if "，" in str(path) or "，" in str(resolved):
            msg += "。路径含中文逗号，可能是「目录 + 文件名」而不是单个文件名。"
        return "Error: {}".format(msg)
    except ValueError as e:
        return "Error: {}".format(str(e))

    # 检查是否超过单请求上限
    if len(PENDING_IMAGES) >= vision.MAX_IMAGES_PER_REQUEST:
        return (
            "Error: 已达到单次请求图片上限 ({} 张)。"
            "请分析当前图片后再加载新图片。".format(vision.MAX_IMAGES_PER_REQUEST)
        )

    # 编码（超限时自动缩图）
    try:
        data_url, note = vision.encode_for_llm(str(resolved))
    except (FileNotFoundError, ValueError) as e:
        return "Error: {}".format(str(e))

    # 存入缓冲
    PENDING_IMAGES.append(data_url)
    logger.info(
        "image_loaded",
        {"name": info["name"], "size_kb": info["size_kb"], "pending": len(PENDING_IMAGES)},
    )

    extra = "（{}）".format(note) if note else ""
    return (
        "图片已加载: {} ({}KB, {}格式){}。"
        "我已看到这张图片，可以继续描述或分析。".format(
            info["name"], info["size_kb"], info["ext"].lstrip(".").upper(), extra
        )
    )


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------
def _ensure_vendor():
    v = _vendor_dir()
    if v not in sys.path:
        sys.path.insert(0, v)


def _col_letter(n):
    s = ""
    n = int(n)
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s or "A"


def _xlsx_used_bounds(ws):
    cells = getattr(ws, "_cells", None) or {}
    max_r, max_c = 0, 0
    for key in cells:
        r, c = key[0], key[1]
        if r > max_r:
            max_r = r
        if c > max_c:
            max_c = c
    return max_r, max_c


def _summarize_xlsx(path):
    _ensure_vendor()
    try:
        import openpyxl
    except ImportError:
        return "Error: openpyxl 不可用，无法读取 xlsx（请用 vendor，不要 pip）。"
    try:
        wb = openpyxl.load_workbook(str(path), data_only=False, read_only=False)
    except Exception as e:
        return f"Error: 无法打开 xlsx: {type(e).__name__}: {e}"
    try:
        lines = [f"xlsx: {path.name}", f"sheets: {', '.join(wb.sheetnames)}"]
        for name in wb.sheetnames:
            ws = wb[name]
            used_r, used_c_raw = _xlsx_used_bounds(ws)
            used_c = min(used_c_raw, OFFICE_MAX_COLS) if used_c_raw else 0
            merges = list(ws.merged_cells.ranges) if getattr(ws, "merged_cells", None) else []
            lines.append(
                f"sheet '{name}': used_rows={used_r} used_cols={used_c}"
                + (f" (occupied {used_c_raw}, capped {OFFICE_MAX_COLS})" if used_c_raw > OFFICE_MAX_COLS else "")
                + f" merges={len(merges)}"
            )
            if merges:
                lines.append("  merge ranges: " + ", ".join(str(x) for x in merges[:30]))
            header_n = min(8, used_r) if used_r else 0
            shown = 0
            for r in range(1, header_n + 1):
                cells = []
                for c in range(1, (used_c or 0) + 1):
                    val = ws.cell(r, c).value
                    if val is None or val == "":
                        continue
                    cells.append(f"{_col_letter(c)}{r}={val}")
                if cells:
                    lines.append("  " + "; ".join(cells)[:400])
                    shown += 1
                if shown >= 15:
                    break
        lines.append("不要用 max_column 扫全表，本摘要列已封顶。")
        text = "\n".join(lines)
        if len(text) > OFFICE_SUMMARY_MAX:
            text = text[:OFFICE_SUMMARY_MAX] + "\n…(truncated)"
        return text
    finally:
        try:
            wb.close()
        except Exception:
            pass


def _summarize_docx(path):
    _ensure_vendor()
    try:
        from docx import Document
        doc = Document(str(path))
    except Exception as e:
        return f"Error: 无法打开 docx: {type(e).__name__}: {e}"
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()][:40]
    text = f"docx: {path.name}\nparagraphs: {len(doc.paragraphs)}\n" + "\n".join(paras)
    return text[:OFFICE_SUMMARY_MAX]


def _summarize_pptx(path):
    _ensure_vendor()
    try:
        from pptx import Presentation
        prs = Presentation(str(path))
    except Exception as e:
        return f"Error: 无法打开 pptx: {type(e).__name__}: {e}"
    lines = [f"pptx: {path.name}", f"slides: {len(prs.slides)}"]
    for i, slide in enumerate(prs.slides, 1):
        bits = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                t = shape.text_frame.text.strip()
                if t:
                    bits.append(t.replace("\n", " ")[:120])
            if len(bits) >= 3:
                break
        title = bits[0] if bits else "(empty)"
        lines.append(f"  {i}. {title}")
        if i >= 15:
            break
    return "\n".join(lines)[:OFFICE_SUMMARY_MAX]


def _summarize_pdf(path):
    _ensure_vendor()
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
    except Exception as e:
        return f"Error: 无法打开 pdf: {type(e).__name__}: {e}"
    n = len(reader.pages)
    lines = [f"pdf: {path.name}", f"pages: {n}"]
    for i, page in enumerate(reader.pages[:3], 1):
        try:
            t = (page.extract_text() or "").strip().replace("\n", " ")[:500]
        except Exception:
            t = ""
        lines.append(f"  p{i}: {t or '(no text)'}")
    return "\n".join(lines)[:OFFICE_SUMMARY_MAX]


def _exec_read_file(args):
    path = _resolve_path(args["path"])
    if not path.exists():
        return f"Error: File not found: {path}"
    if not path.is_file():
        return f"Error: Not a file: {path}"

    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        return _summarize_xlsx(path)
    if suffix == ".docx":
        return _summarize_docx(path)
    if suffix == ".pptx":
        return _summarize_pptx(path)
    if suffix == ".pdf":
        return _summarize_pdf(path)

    try:
        content, _enc = _read_text(path)
    except PermissionError:
        return f"Error: Permission denied: {path}"

    lines = content.split("\n")
    total = len(lines)

    start = args.get("start_line", 1)
    end = args.get("end_line", total)
    start = max(1, start)
    end = min(total, end)

    if end - start + 1 > MAX_READ_LINES:
        end = start + MAX_READ_LINES - 1
        truncated = True
    else:
        truncated = False

    numbered = []
    for i in range(start, end + 1):
        numbered.append(f"{i:5d}: {lines[i - 1]}")

    result = "\n".join(numbered)
    header = f"[{path} | lines {start}-{end}/{total}]"
    if truncated:
        result += f"\n... (truncated at {MAX_READ_LINES} lines, file has {total} lines)"

    return f"{header}\n{result}"


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------
_TRIM_LEAK = "需要时 read_file 重取"


def _reject_trim_leak(*chunks):
    for s in chunks:
        if isinstance(s, str) and _TRIM_LEAK in s:
            return ("Error: 内容含窗口裁剪占位（trimmed），不要写进文件。"
                    "请重新生成完整内容。")
    return None


def _exec_write_file(args):
    path = _resolve_path(args["path"])
    content = args.get("content", "")
    leaked = _reject_trim_leak(content)
    if leaked:
        return leaked

    # 出界/敏感判定已在 execute() 网关完成；此处只执行写入。
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(content, encoding="utf-8")
    lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    return f"OK: Wrote {path} ({lines} lines, {len(content)} bytes)"


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------
def _exec_edit_file(args):
    path = _resolve_path(args["path"])
    old_text = args.get("old_text", "")
    new_text = args.get("new_text", "")
    leaked = _reject_trim_leak(old_text, new_text)
    if leaked:
        return leaked

    if not path.exists():
        return f"Error: File not found: {path}"

    content, enc = _read_text(path)

    if old_text not in content:
        # 尝试给出提示
        return (
            f"Error: old_text not found in {path.name}.\n"
            f"Make sure it matches exactly (including whitespace and indentation).\n"
            f"First 200 chars of file: {repr(content[:200])}"
        )

    count = content.count(old_text)
    new_content = content.replace(old_text, new_text)
    path.write_text(new_content, encoding=enc)

    return f"OK: Replaced {count} occurrence(s) in {path}"


# ---------------------------------------------------------------------------
# shell
# ---------------------------------------------------------------------------
def _vendor_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")


def _shell_env():
    """子进程环境：vendor 在 PYTHONPATH 最前，stdout 用 UTF-8。"""
    env = os.environ.copy()
    vendor = _vendor_dir()
    old = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = vendor + os.pathsep + old if old else vendor
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _reject_max_column_script(command):
    """跑 python 脚本前：含 max_column 全表循环则不启动。"""
    m = _PY_SCRIPT.search(command or "")
    if not m:
        return None
    script = m.group(1)
    try:
        p = _resolve_path(script)
        src = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    bad = bool(_MAX_COL_RANGE.search(src))
    if _ITER_ROWS_BARE.search(src) and "max_col" not in src:
        bad = True
    if not bad:
        return None
    return (
        "Error: 列循环须有上界（≤80 或最后有内容的列），xlsx 结构用 read_file。"
        " 已拒绝启动含 range(...max_column) 或无界 iter_rows() 的脚本。"
    )


def _powershell_ampersand_error(command):
    shell = _detect_shell()[0].lower()
    if "powershell" not in shell and shell != "pwsh":
        return None
    if "&&" not in (command or ""):
        return None
    return "Error: PowerShell 请用 `;` 分隔命令，不要用 `&&`。"


def _exec_shell(args, confirm_fn=None):
    """执行 shell 命令。危险/逃逸检测 + 审批已上提到 execute() 统一安全链。"""
    command = args.get("command", "").strip()
    timeout = min(int(args.get("timeout", SHELL_TIMEOUT)), 300)

    if not command:
        return "Error: Empty command"

    amp = _powershell_ampersand_error(command)
    if amp:
        return amp
    blocked = _reject_max_column_script(command)
    if blocked:
        return blocked

    shell_cmd = _detect_shell()
    full_cmd = shell_cmd + [command]

    t0 = time.time()
    try:
        proc = subprocess.run(
            full_cmd,
            capture_output=True,
            timeout=timeout,
            cwd=WORK_DIR,
            env=_shell_env(),
        )
        elapsed = time.time() - t0
    except subprocess.TimeoutExpired:
        return (
            f"Error: Command timed out after {timeout}s: {command}\n"
            "缩小扫描范围 / 用 read_file 看 xlsx。"
        )
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"

    stdout = _decode_bytes(proc.stdout)
    stderr = _decode_bytes(proc.stderr)

    # 组装输出
    parts = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"[stderr]\n{stderr}")

    output = "\n".join(parts).strip() if parts else "(no output)"

    # 截断
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + f"\n... (truncated, {len(output)} chars total)"

    result = f"$ {command}\n"
    if proc.returncode != 0:
        result += f"[exit code: {proc.returncode}]\n"
    result += output
    result += f"\n[elapsed: {elapsed:.1f}s]"

    return result


# ---------------------------------------------------------------------------
# glob / grep
# ---------------------------------------------------------------------------
def _within_workdir(path):
    wd = Path(os.path.abspath(WORK_DIR)).resolve()
    try:
        Path(path).resolve().relative_to(wd)
        return True
    except ValueError:
        return False


def _expand_braces(pat):
    m = re.match(r"^(.*)\{([^}]+)\}(.*)$", pat or "")
    if not m:
        return [pat]
    return [m.group(1) + p.strip() + m.group(3) for p in m.group(2).split(",")]


def _glob_match(rel, name, pattern):
    rel = rel.replace("\\", "/")
    for pat in _expand_braces(pattern):
        pat = (pat or "").replace("\\", "/")
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel, pat):
            return True
        if pat.startswith("**/"):
            rest = pat[3:]
            if fnmatch.fnmatch(name, rest) or fnmatch.fnmatch(rel, rest):
                return True
            if "/" in rel and fnmatch.fnmatch(rel.split("/")[-1], rest):
                return True
    return False


def _iter_work_files(root):
    root = Path(root)
    wd = Path(os.path.abspath(WORK_DIR)).resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            try:
                rel = str(p.resolve().relative_to(wd)).replace("\\", "/")
            except ValueError:
                continue
            yield p, rel, fn


def _exec_glob(args):
    pattern = (args.get("pattern") or "").strip()
    if not pattern:
        return "Error: pattern required"
    base = _resolve_path(args.get("path") or WORK_DIR)
    if not _within_workdir(base):
        return "Error: path outside working directory"
    if not base.exists():
        return f"Error: Not found: {base}"
    hits = []
    for _p, rel, name in _iter_work_files(base):
        if _glob_match(rel, name, pattern):
            hits.append(rel)
        if len(hits) >= GLOB_MAX:
            break
    return "\n".join(hits) if hits else "(no matches)"


def _exec_grep(args):
    raw = args.get("pattern")
    if raw is None or str(raw) == "":
        return "Error: pattern required"
    raw = str(raw)
    try:
        rx = re.compile(raw)
    except re.error:
        rx = re.compile(re.escape(raw))
    gpat = args.get("glob") or "*.{py,md,txt,json,csv}"
    base = _resolve_path(args.get("path") or WORK_DIR)
    if not _within_workdir(base):
        return "Error: path outside working directory"
    lines = []
    for p, rel, name in _iter_work_files(base):
        if not _glob_match(rel, name, gpat):
            continue
        try:
            if p.stat().st_size > 2_000_000:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        nfile = 0
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                lines.append(f"{rel}:{i}:{line[:200]}")
                nfile += 1
                if nfile >= GREP_PER_FILE or len(lines) >= GREP_MAX:
                    break
        if len(lines) >= GREP_MAX:
            break
    return "\n".join(lines) if lines else "(no matches)"


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _decode_bytes(data):
    """按 utf-8 → gb18030 → locale → replace 解码子进程输出。"""
    if not data:
        return ""
    encodings = ["utf-8", "gb18030"]
    pref = locale.getpreferredencoding(False)
    if pref and pref.lower() not in ("utf-8", "utf8", "gb18030"):
        encodings.append(pref)
    for enc in encodings:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_text(path):
    """按 utf-8 → gb18030 → locale 默认读取，返回 (text, encoding)。"""
    encodings = ["utf-8", "gb18030"]
    pref = locale.getpreferredencoding(False)
    if pref and pref.lower() not in ("utf-8", "utf8", "gb18030"):
        encodings.append(pref)
    for enc in encodings:
        try:
            return path.read_text(encoding=enc), enc
        except UnicodeDecodeError:
            continue
        except PermissionError:
            raise
    return path.read_text(encoding="utf-8", errors="replace"), "utf-8"
