#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools.py — Agent 工具定义与执行

文件/检索：read_file, write_file, edit_file, view_image, glob, grep
执行：shell（慢命令自动转后台）, python（内联代码，脚本留到任务结束）, preview_page（Edge 截图）, job（后台任务）
协作：todo_write（任务清单）, ask_user（选择题式提问）
零依赖，跨平台（Windows / Linux / macOS）
"""
import atexit
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
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
import taskdir
import todo as todo_mod


def _parent_of(pid):
    """返回 pid 的父进程。查不到则为 0。"""
    if sys.platform != "win32":
        if pid == os.getpid():
            try:
                return os.getppid()
            except OSError:
                return 0
        return 0
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        ntdll = ctypes.windll.ntdll
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

        class PROCESS_BASIC_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("Reserved1", ctypes.c_void_p),
                ("PebBaseAddress", ctypes.c_void_p),
                ("Reserved2", ctypes.c_void_p * 2),
                ("UniqueProcessId", ctypes.c_void_p),
                ("InheritedFromUniqueProcessId", ctypes.c_void_p),
            ]

        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return 0
        info = PROCESS_BASIC_INFORMATION()
        status = ntdll.NtQueryInformationProcess(
            handle, 0, ctypes.byref(info), ctypes.sizeof(info), None
        )
        kernel32.CloseHandle(handle)
        if status != 0:
            return 0
        return int(info.InheritedFromUniqueProcessId or 0)
    except Exception:
        return 0


def _protected_pids():
    """本进程以及启动它的父链。这些 PID 不能被 shell 停掉。"""
    found = []
    pid = os.getpid()
    for _ in range(8):
        if not pid or pid in found:
            break
        found.append(pid)
        parent = _parent_of(pid)
        if parent == pid:
            break
        pid = parent
    return found


_KILL_VERB = re.compile(r"(?i)\b(stop-process|taskkill|spps|kill)\b")
_EXPLICIT_PID = re.compile(
    r"(?i)(?:(?:^|[\s;(])(?:-id|/pid)\s*[:=]?\s*|\.id\s+-eq\s+|processid\s+-eq\s+)(\d+)"
)
_IMAGE_KILL = re.compile(
    r"(?i)(?:/im\s+| -name\s+)['\"]?(python|py)(?:\.exe)?\b"
)
_GET_PYTHON = re.compile(r"(?i)\bget-process\s+(?:-name\s+)?['\"]?(python|py)(?:\.exe)?\b")


def _self_kill_error(command):
    """命令会结束本程序或它的父进程时返回错误文案，否则 None。"""
    if not command or not _KILL_VERB.search(command):
        return None
    protected = _protected_pids()
    protected_set = set(protected)
    targeted = []
    for match in _EXPLICIT_PID.finditer(command):
        pid = int(match.group(1))
        if pid not in targeted:
            targeted.append(pid)
    hit = [pid for pid in targeted if pid in protected_set]
    broad = bool(_IMAGE_KILL.search(command))
    if _GET_PYTHON.search(command) and _KILL_VERB.search(command):
        if not targeted or hit:
            broad = True
    if not hit and not broad:
        return None
    me = protected[0] if protected else os.getpid()
    return (
        "Error: 这条命令会结束本程序（PID {}）或启动它的父进程，没有执行。"
        "停服务时只结束 netstat 里 LISTENING 对应的那个 PID，"
        "不要按名字结束 python/py，也不要把本进程的 PID 放进 Stop-Process / taskkill。"
    ).format(me)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
WORK_DIR = os.environ.get("WORK_DIR", os.getcwd())
SAFE_MODE = False  # 由 agent 入口赋值（v6.0 陷阱 E）
SHELL_TIMEOUT = 60  # 默认 shell 超时
MAX_READ_LINES = 500  # read_file 最大行数
MAX_OUTPUT_CHARS = 10_000  # shell 输出上限（超出：保留首尾，全文落盘）
AUTO_BG_SEC = 15  # 前台命令超过这么久自动转后台（不杀进程）
SESSION_ID = ""   # 当前会话 id；execute() 每次刷新，决定 task_temp() 目录
JOBS = {}         # 后台任务注册表 job_id -> dict
_job_seq = 0
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
_MAX_COL_RANGE = re.compile(r"range\s*\(")
_MAX_COL_CAP = re.compile(
    r"min\s*\(\s*(?:[^,]+max_column[^,]*,\s*(\d+)|(\d+)\s*,[^)]*max_column)",
    re.I,
)
_ITER_ROWS_BARE = re.compile(r"iter_rows\s*\(\s*\)")


def ensure_temp_dir(work_dir=None):
    """保证 {work_dir}/temp 存在，返回绝对路径。"""
    wd = os.path.abspath(work_dir or WORK_DIR)
    d = os.path.join(wd, "temp")
    os.makedirs(d, exist_ok=True)
    return d


def task_temp(create=True):
    """当前会话的任务临时目录 {work_dir}/temp/tasks/<session_id>/（P4）。"""
    ensure_temp_dir(WORK_DIR)
    return taskdir.task_dir(WORK_DIR, SESSION_ID or "default", create=create)


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
            "description": "Create a new file or overwrite an existing file with the given content. Parent directories are created automatically. For a large file (> ~150 lines) write the first part, then add the rest with mode=append in several calls, so one call never hits the output limit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path (absolute, or relative to working directory)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["overwrite", "append"],
                        "description": "overwrite (default) replaces the file; append adds content to the end (creates the file if missing).",
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
            "description": "Exact-text replacement in a file. old_text must match exactly once (including whitespace) unless replace_all=true; if it matches several places, add surrounding context to make it unique. To change several places in one file, pass edits[] in ONE call (all edits are validated first, then applied together). Returns a short diff.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Exact text to find (single edit)",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Replacement text (single edit)",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace every occurrence of old_text (default false: must be unique).",
                    },
                    "edits": {
                        "type": "array",
                        "description": "Several edits applied to the same file in order, instead of old_text/new_text.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_text": {"type": "string"},
                                "new_text": {"type": "string"},
                                "replace_all": {"type": "boolean"},
                            },
                            "required": ["old_text", "new_text"],
                        },
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "Execute a shell command and return its output. Use for: running scripts, listing files, git, starting servers. On Windows the shell is PowerShell: separate commands with ';', not '&&' or '&'. A command still running after ~15s is moved to the background (it is NOT killed): you get a job id, then use the job tool to read output or kill it. Start servers/watchers with background=true. For short Python snippets prefer the python tool (no quoting problems).",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Seconds to wait in the foreground before it moves to background (default 15, max 300). Use when you expect a slow command.",
                    },
                    "background": {
                        "type": "boolean",
                        "description": "Start in the background immediately and return a job id (servers, long builds).",
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
    {
        "type": "function",
        "function": {
            "name": "python",
            "description": "Run a Python snippet directly (no shell quoting). Uses the current interpreter with vendor packages (openpyxl, pandas, docx, pptx...). The script stays in the task temp run/ directory until this task ends; edit that file instead of writing a new one. Print what you need to see; keep output small (summaries, not whole tables). Use save_as only for a script the user wants to keep.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python source code"},
                    "timeout": {
                        "type": "integer",
                        "description": "Seconds to wait in the foreground before it moves to background (default 15, max 300).",
                    },
                    "save_as": {
                        "type": "string",
                        "description": "Optional path to keep the script at (relative to working directory).",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preview_page",
            "description": "Open a local page in the system Edge browser and screenshot it at 1440x900 and 390x844. Use this after starting your own dev server. Do not write a Playwright script, do not install a browser, and do not patch Playwright. If Edge is missing or the page does not open, report the reason and stop.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "http:// or https:// URL, usually http://127.0.0.1:<port>/",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "job",
            "description": "Manage background jobs started by shell/python. action=output returns the NEW output since the last check (wait_sec lets you wait for it to finish); action=kill stops the job and its child processes; action=list shows all jobs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["output", "kill", "list"]},
                    "job_id": {"type": "string", "description": "e.g. j1 (not needed for list)"},
                    "wait_sec": {"type": "integer", "description": "For output: wait up to this many seconds (max 120) for the job to finish."},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": "Keep a task checklist for multi-step work (3+ steps). Persisted across context compaction and session resume. Default merge=true: send only the items that changed (id + status), or new items. Keep exactly one item in_progress. Mark an item completed right after it is really done (verified). Optional plan: the agreed plan text, saved as plan.md.",
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "content": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"]},
                            },
                            "required": ["id"],
                        },
                    },
                    "merge": {"type": "boolean", "description": "true (default): merge by id; false: replace the whole list."},
                    "plan": {"type": "string", "description": "Optional: the confirmed plan text to save as plan.md."},
                },
                "required": ["todos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Ask the user multiple-choice questions when a decision is genuinely theirs (scope, tech choice, destructive action). Put your recommended option FIRST and mark it '(Recommended)'. First show your plan or findings in normal text, then ask. Do not ask what you can decide from the request or by looking at the files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "prompt": {"type": "string"},
                                "options": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}, "label": {"type": "string"}},
                                        "required": ["label"],
                                    },
                                },
                                "allow_multiple": {"type": "boolean"},
                            },
                            "required": ["prompt", "options"],
                        },
                    },
                },
                "required": ["questions"],
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
    global LAST_VERDICT, SESSION_ID
    LAST_VERDICT = "allow"
    if session_id:
        SESSION_ID = str(session_id)
    ensure_temp_dir(WORK_DIR)
    try:
        taskdir.touch(task_temp())
    except OSError:
        pass
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
        elif tool_name == "python":
            result = _exec_python(args)
        elif tool_name == "preview_page":
            result = _exec_preview_page(args)
        elif tool_name == "job":
            result = _exec_job(args)
        elif tool_name == "todo_write":
            result = _exec_todo_write(args)
        elif tool_name == "ask_user":
            result = _exec_ask_user(args, input_fn)
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

    if tool_name != "job":
        note = running_jobs_note()
        if note:
            result = f"{result}\n{note}"
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
        # 只有显式 replace_all 的"多处替换"才需要确认；默认要求唯一匹配，不唯一时在执行期报错
        if path.exists() and path.is_file():
            content, _enc = _read_text(path)
            worst = 0
            for old_text, _new, rall in _edit_specs(args):
                if rall and old_text:
                    worst = max(worst, content.count(old_text))
            if worst > 1:
                blocked = _apply_verdict(
                    tool_guard.assess_multi_replace(worst, str(path)),
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
        msg = f"Error: 无法打开 docx: {type(e).__name__}: {e}"
        if isinstance(e, (ImportError, OSError)):
            msg += _vendor_abi_hint()
        return msg
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()][:40]
    text = f"docx: {path.name}\nparagraphs: {len(doc.paragraphs)}\n" + "\n".join(paras)
    return text[:OFFICE_SUMMARY_MAX]


def _summarize_pptx(path):
    _ensure_vendor()
    try:
        from pptx import Presentation
        prs = Presentation(str(path))
    except Exception as e:
        msg = f"Error: 无法打开 pptx: {type(e).__name__}: {e}"
        if isinstance(e, (ImportError, OSError)):
            msg += _vendor_abi_hint()
        return msg
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
_TRIM_LEAK = "…(已省略"


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

    lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    if str(args.get("mode") or "overwrite").lower() == "append":
        with open(path, "a", encoding="utf-8", newline="") as f:
            f.write(content)
        total = path.stat().st_size
        return f"OK: Appended {path} (+{lines} lines, +{len(content)} chars; file now {total} bytes)"
    path.write_text(content, encoding="utf-8")
    return f"OK: Wrote {path} ({lines} lines, {len(content)} bytes)"


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------
def _edit_specs(args):
    """归一化为 [(old, new, replace_all)]。edits[] 优先，否则取顶层 old_text/new_text。"""
    edits = args.get("edits")
    if isinstance(edits, list) and edits:
        out = []
        for e in edits:
            if isinstance(e, dict):
                out.append((str(e.get("old_text", "")), str(e.get("new_text", "")),
                            bool(e.get("replace_all"))))
        return out
    if "old_text" in args:
        return [(str(args.get("old_text", "")), str(args.get("new_text", "")),
                 bool(args.get("replace_all")))]
    return []


def _short_diff(old, new, path, max_lines=60):
    diff = list(difflib.unified_diff(
        old.splitlines(), new.splitlines(), fromfile=path.name, tofile=path.name, lineterm="", n=1))
    if len(diff) > max_lines:
        diff = diff[:max_lines] + [f"... (diff 共 {len(diff)} 行，已截断)"]
    return "\n".join(diff)


def _exec_edit_file(args):
    path = _resolve_path(args["path"])
    specs = _edit_specs(args)
    if not specs:
        return "Error: 需要 old_text/new_text，或 edits[]"
    for o, n, _r in specs:
        leaked = _reject_trim_leak(o, n)
        if leaked:
            return leaked

    if not path.exists():
        return f"Error: File not found: {path}"

    content, enc = _read_text(path)
    work = content
    total = 0
    # 先在内存里依次应用并校验；任何一处失败都不落盘（原子）
    for i, (old_text, new_text, rall) in enumerate(specs, 1):
        tag = f"edits[{i}] " if len(specs) > 1 else ""
        if not old_text:
            return f"Error: {tag}old_text 不能为空"
        count = work.count(old_text)
        if count == 0:
            return (
                f"Error: {tag}old_text not found in {path.name}（未修改任何内容）.\n"
                f"Make sure it matches exactly (including whitespace and indentation); "
                f"edits[] 按顺序依次应用，后一处要匹配前一处改完之后的文本。\n"
                f"First 200 chars of file: {repr(work[:200])}"
            )
        if count > 1 and not rall:
            return (
                f"Error: {tag}old_text 在 {path.name} 中出现 {count} 次，无法确定改哪一处（未修改任何内容）。"
                f"请在 old_text 里加上前后几行上下文使其唯一；确实要全部替换时传 replace_all=true。"
            )
        work = work.replace(old_text, new_text)
        total += count

    path.write_text(work, encoding=enc)
    head = f"OK: Replaced {total} occurrence(s) in {path}"
    if len(specs) > 1:
        head += f" ({len(specs)} edits)"
    return head + "\n" + _short_diff(content, work, path)


# ---------------------------------------------------------------------------
# shell
# ---------------------------------------------------------------------------
def _vendor_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")


def _vendor_python():
    """当前进程的解释器。vendor 原生扩展按这个版本打包。"""
    return sys.executable or None


def _vendor_abi_hint():
    ver = "%d.%d" % (sys.version_info[0], sys.version_info[1])
    return (
        " vendor 与解释器不匹配，不要搜索其他 python。"
        " 当前解释器: %s (%s)。"
    ) % (sys.executable, ver)


def _shell_env():
    """子进程环境：vendor 在 PYTHONPATH 最前，stdout 用 UTF-8。"""
    env = os.environ.copy()
    vendor = _vendor_dir()
    old = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = vendor + os.pathsep + old if old else vendor
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    try:
        env["TASK_TEMP"] = task_temp()
    except OSError:
        pass
    return env


def _matching_paren(src, open_idx):
    depth = 0
    for i in range(open_idx, len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return None


def _range_exprs_with_max_column(src):
    out = []
    for m in _MAX_COL_RANGE.finditer(src or ""):
        end = _matching_paren(src, m.end() - 1)
        if end is None:
            continue
        expr = src[m.start():end + 1]
        if "max_column" in expr:
            out.append(expr)
    return out


def _max_column_range_capped(expr):
    """range 内有 min(..., N) 或 min(N, ...)，且 N 是 ≤80 的数字。"""
    m = _MAX_COL_CAP.search(expr or "")
    if not m:
        return False
    n = int(m.group(1) or m.group(2))
    return n <= 80


def _reject_max_column_script(command):
    """跑 python 脚本前：无上界的 max_column 循环则不启动。min(..., N≤80) 放行。"""
    m = _PY_SCRIPT.search(command or "")
    if not m:
        return None
    script = m.group(1)
    try:
        p = _resolve_path(script)
        src = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    return _max_column_error(src)


def _max_column_error(src):
    """源码里有无上界的 max_column 循环 / 无界 iter_rows() 则返回错误文案，否则 None。"""
    bad = any(not _max_column_range_capped(expr) for expr in _range_exprs_with_max_column(src))
    if _ITER_ROWS_BARE.search(src) and "max_col" not in src:
        bad = True
    if not bad:
        return None
    return (
        "Error: 列循环须有上界（≤80 或最后有内容的列），xlsx 结构用 read_file。"
        " 已拒绝启动含 range(...max_column) 或无界 iter_rows() 的脚本。"
        " 允许 range(1, min(ws.max_column, 32)+1) 这种带数字上界（≤80）的写法。"
    )


def _powershell_ampersand_error(command):
    shell = _detect_shell()[0].lower()
    if "powershell" not in shell and shell != "pwsh":
        return None
    if "&&" not in (command or ""):
        return None
    return "Error: PowerShell 请用 `;` 分隔命令，不要用 `&&`。"


def _kill_shell_tree(proc):
    """超时后结束外壳和它拉起的子进程。Windows 上只杀外壳时，孙进程会占住管道。"""
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            try:
                proc.kill()
            except OSError:
                pass
    else:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.communicate(timeout=2)
    except Exception:
        pass


class _Job:
    """一个后台（或刚结束的）子进程。输出按字节追加到日志，按需解码。"""

    def __init__(self, jid, command, proc, log_path, script=None):
        self.id = jid
        self.command = command
        self.proc = proc
        self.log_path = log_path
        self.script = script
        self.started = time.time()
        self.ended = None
        self.returncode = None
        self.read_pos = 0


def _new_job_id():
    global _job_seq
    _job_seq += 1
    return "j{}".format(_job_seq)


def _wait_job(job, seconds):
    end = time.time() + max(0, seconds)
    while job.ended is None and time.time() < end:
        time.sleep(0.05)


def _read_job_new(job):
    """读出自上次以来新增的输出（字节解码，避免 text 模式按错误编码解码）。"""
    try:
        with open(job.log_path, "rb") as f:
            f.seek(job.read_pos)
            data = f.read()
            job.read_pos = f.tell()
    except OSError:
        data = b""
    return _decode_bytes(data)


def _cap_output(text, label):
    """超长输出保留首尾，全文落到任务目录 out/，返回带路径的截断文本。"""
    if not isinstance(text, str) or len(text) <= MAX_OUTPUT_CHARS:
        return text
    head = MAX_OUTPUT_CHARS // 2
    tail = MAX_OUTPUT_CHARS - head
    saved = ""
    try:
        d = taskdir.sub_dir(WORK_DIR, SESSION_ID or "default", "out")
        path = os.path.join(d, "{}-{}.log".format(label, int(time.time() * 1000)))
        with open(path, "w", encoding="utf-8", errors="replace") as f:
            f.write(text)
        saved = path
    except OSError:
        saved = "(落盘失败)"
    omitted = len(text) - head - tail
    return text[:head] + "\n…(中间省略 {} 字，全文: {})…\n".format(omitted, saved) + text[-tail:]


def _spawn_process(argv, command, script=None):
    """启动进程，输出由守护线程写入日志。返回 _Job；启动失败返回 None 并把异常存在 ._spawn_error。"""
    jid = _new_job_id()
    log_path = os.path.join(taskdir.sub_dir(WORK_DIR, SESSION_ID or "default", "jobs"), jid + ".log")
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=WORK_DIR,
            env=_shell_env(),
        )
    except Exception as e:
        job = _Job(jid, command, None, log_path, script=script)
        job.ended = time.time()
        job.returncode = -1
        job._spawn_error = "{}: {}".format(type(e).__name__, e)
        return job
    job = _Job(jid, command, proc, log_path, script=script)
    JOBS[jid] = job

    def _pump():
        try:
            with open(log_path, "wb") as f:
                while True:
                    chunk = proc.stdout.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
                    f.flush()
        except Exception:
            pass
        try:
            proc.wait()
        except Exception:
            pass
        job.returncode = proc.returncode
        job.ended = time.time()

    threading.Thread(target=_pump, name="job-" + jid, daemon=True).start()
    return job


def _format_finished(job, command):
    err = getattr(job, "_spawn_error", None)
    if err:
        return "Error: {}".format(err)
    output = _read_job_new(job).strip() or "(no output)"
    output = _cap_output(output, job.id)
    elapsed = (job.ended or time.time()) - job.started
    result = "$ {}\n".format(command)
    if job.returncode:
        result += "[exit code: {}]\n".format(job.returncode)
    result += output
    result += "\n[elapsed: {:.1f}s]".format(elapsed)
    if job.returncode:
        blob = output.lower()
        if "greenlet" in blob or "lxml" in blob or "dll load failed" in blob:
            result += _vendor_abi_hint()
    return result


_FIRST_OUTPUT_WAIT = 1.0  # 转入后台时，日志仍空则最多再等这么久拿首包


def _log_size(job):
    try:
        return os.path.getsize(job.log_path)
    except OSError:
        return 0


def _wait_first_output(job, seconds):
    """进程还在跑且日志仍空时，最多再等 seconds 秒。有新字节或进程结束即返回。"""
    end = time.time() + max(0, seconds)
    while job.ended is None and time.time() < end:
        if _log_size(job) > (job.read_pos or 0):
            return
        time.sleep(0.05)


def _after_foreground(job, command):
    """前台等待结束后：仍在跑且日志为空，再等一小段首包，然后格式化。"""
    if job.ended is None and _log_size(job) <= (job.read_pos or 0):
        _wait_first_output(job, _FIRST_OUTPUT_WAIT)
    if job.ended is not None:
        return _format_finished(job, command)
    return _format_running(job, command)


def _format_running(job, command):
    output = _read_job_new(job).strip()
    if output:
        text = (
            "$ {}\n已转入后台，任务 {} 仍在运行（没有被终止）。"
            "用 job 工具 action=output 查看新输出，action=kill 停止。日志: {}"
        ).format(command, job.id, job.log_path)
        text += "\n目前输出:\n" + _cap_output(output, job.id)
        return text
    return (
        "$ {}\n已转入后台，任务 {} 仍在运行（没有被终止）。"
        "日志可能还没写入。用 job 工具 action=output，并带上 wait_sec 再看；"
        "action=kill 停止。日志: {}"
    ).format(command, job.id, job.log_path)


def running_jobs_note():
    alive = [j.id for j in JOBS.values() if j.ended is None]
    if not alive:
        return ""
    return "[还有 {} 个后台任务在运行: {}。用 job 查看或停止。]".format(len(alive), ", ".join(alive))


def kill_all_jobs():
    """会话退出时停掉仍在跑的后台任务。返回杀掉的数量。"""
    n = 0
    for job in list(JOBS.values()):
        if job.ended is None and job.proc is not None:
            _kill_shell_tree(job.proc)
            n += 1
    return n


atexit.register(kill_all_jobs)


def _bounded_timeout(value, default):
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, 300))


def _exec_shell(args, confirm_fn=None):
    """执行 shell 命令。超过前台等待时间不杀进程，转入后台并返回 job id。"""
    command = str(args.get("command", "")).strip()
    if not command:
        return "Error: Empty command"
    amp = _powershell_ampersand_error(command)
    if amp:
        return amp
    blocked = _reject_max_column_script(command)
    if blocked:
        return blocked

    wait = 0.4 if args.get("background") else _bounded_timeout(args.get("timeout", AUTO_BG_SEC), AUTO_BG_SEC)
    self_kill = _self_kill_error(command)
    if self_kill:
        return self_kill
    job = _spawn_process(_detect_shell() + [command], command)
    _wait_job(job, wait)
    return _after_foreground(job, command)


def _script_keep_note(script):
    return "\n脚本保留至本任务结束: {}。要改就 edit_file 这个文件，不要另写一份。".format(script)


def _exec_python(args):
    """内联执行 Python。脚本留在任务目录，任务结束且没有未完成待办时再清理。"""
    code = args.get("code")
    if not isinstance(code, str) or not code.strip():
        return "Error: code required"
    blocked = _max_column_error(code)
    if blocked:
        return blocked
    save_as = args.get("save_as")
    if save_as:
        dest = _resolve_path(str(save_as))
        if not _within_workdir(dest):
            return "Error: save_as 必须在工作目录内。工作目录外的文件请用 write_file。"
    run_dir = taskdir.sub_dir(WORK_DIR, SESSION_ID or "default", "run")
    script = os.path.join(run_dir, "py-{}-{}.py".format(int(time.time() * 1000), os.getpid()))
    with open(script, "w", encoding="utf-8", newline="\n") as f:
        f.write(code)
    if save_as:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(script, str(dest))
    py = sys.executable
    label = "python {}".format(os.path.basename(script))
    job = _spawn_process([py, script], label, script=script)
    wait = _bounded_timeout(args.get("timeout", AUTO_BG_SEC), AUTO_BG_SEC)
    _wait_job(job, wait)
    if job.ended is None and _log_size(job) <= (job.read_pos or 0):
        _wait_first_output(job, _FIRST_OUTPUT_WAIT)
    keep = _script_keep_note(script)
    if job.ended is None:
        return _format_running(job, label) + keep
    text = _format_finished(job, label) + keep
    if save_as and job.returncode == 0:
        text += "\n脚本已保存: {}".format(dest)
    return text


def _preview_script():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_preview.py")


def _parse_preview_payload(text):
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "ok" in obj:
            return obj
    return None


def _vision_enabled():
    try:
        import llm as _llm
        return _llm.CAPABILITY.get("vision") is not False
    except Exception:
        return True


def _format_preview_payload(payload):
    if not payload.get("ok"):
        return payload.get("error") or "页面没打开。报告原因并停止。不要安装浏览器，不要另写验收脚本。"
    shots = payload.get("shots") or {}
    lines = [
        "title: {}".format(payload.get("title") or ""),
        "console_errors: {}".format(json.dumps(payload.get("errors") or [], ensure_ascii=False)),
    ]
    for name in ("desktop", "mobile"):
        lines.append("{}: {}".format(name, shots.get(name) or ""))
    if _vision_enabled():
        for name in ("desktop", "mobile"):
            path = shots.get(name)
            if not path or not os.path.isfile(path):
                continue
            if len(PENDING_IMAGES) >= vision.MAX_IMAGES_PER_REQUEST:
                break
            try:
                data_url, _note = vision.encode_for_llm(path)
            except (FileNotFoundError, ValueError, OSError):
                continue
            PENDING_IMAGES.append(data_url)
        lines.append("截图已附在下一轮。直接根据画面改页面。不要自己写 Playwright。")
    else:
        lines.append("当前模型不支持图像分析。正文摘要：")
        lines.append((payload.get("excerpt") or "")[:2000])
    return "\n".join(lines)


def _exec_preview_page(args):
    """用当前解释器跑仓库里的固定脚本，打开 Edge 并截图。"""
    url = str(args.get("url") or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return "Error: url 须以 http:// 或 https:// 开头"
    script = _preview_script()
    out = task_temp()
    label = "preview_page {}".format(url)
    job = _spawn_process([sys.executable, script, url, out], label)
    _wait_job(job, 60)
    if job.ended is None and _log_size(job) <= (job.read_pos or 0):
        _wait_first_output(job, _FIRST_OUTPUT_WAIT)
    if job.ended is None:
        return _format_running(job, label) + "\n页面还在打开。不要另写验收脚本。"
    if getattr(job, "_spawn_error", None):
        return _format_finished(job, label)
    raw = _read_job_new(job).strip()
    payload = _parse_preview_payload(raw)
    if payload is None:
        text = "$ {}\n".format(label)
        if job.returncode:
            text += "[exit code: {}]\n".format(job.returncode)
        text += raw or "(no output)"
        blob = text.lower()
        if "greenlet" in blob or "lxml" in blob or "dll load failed" in blob:
            if "不要搜索其他 python" not in text:
                text += _vendor_abi_hint()
        return text
    return _format_preview_payload(payload)


def _exec_job(args):
    action = str(args.get("action") or "output").lower()
    if action == "list":
        if not JOBS:
            return "(没有后台任务)"
        lines = []
        for job in JOBS.values():
            state = "running" if job.ended is None else "exit {}".format(job.returncode)
            lines.append("{}  {}  {}".format(job.id, state, job.command[:80]))
        return "\n".join(lines)
    jid = str(args.get("job_id") or "")
    job = JOBS.get(jid)
    if job is None:
        have = ", ".join(JOBS) or "无"
        return "Error: 没有任务 {}。当前: {}".format(jid, have)
    if action == "kill":
        if job.ended is None and job.proc is not None:
            _kill_shell_tree(job.proc)
            _wait_job(job, 3)
        return "已停止 {} (exit {})".format(jid, job.returncode)
    if action == "output":
        wait = _bounded_timeout(args.get("wait_sec", 0), 0) if args.get("wait_sec") else 0
        if wait and job.ended is None:
            _wait_job(job, min(wait, 120))
        text = _read_job_new(job).strip() or "(没有新输出)"
        text = _cap_output(text, job.id)
        state = "已结束 exit {}".format(job.returncode) if job.ended is not None else "仍在运行"
        return "[{} {}]\n{}\n日志: {}".format(jid, state, text, job.log_path)
    return "Error: action 应为 output / kill / list"


def _exec_todo_write(args):
    d = task_temp()
    items = todo_mod.load(d)
    merge = args.get("merge", True)
    if isinstance(merge, str):
        merge = merge.strip().lower() not in ("false", "0", "no")
    updated = todo_mod.apply(items, args.get("todos") or [], merge=bool(merge))
    todo_mod.save(d, updated)
    note = ""
    plan = args.get("plan")
    if isinstance(plan, str) and plan.strip():
        path = os.path.join(d, "plan.md")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(plan)
        note = "\n计划已写入 {}".format(path)
    return "待办已更新:\n" + todo_mod.render(updated) + note


def _exec_ask_user(args, input_fn):
    questions = args.get("questions") or []
    if not isinstance(questions, list) or not questions:
        return "Error: questions required"
    if input_fn is None:
        return ("Error: 当前是非交互模式，无法当场提问。"
                "请把选项写进回复正文，等用户的下一条消息。")
    answers = []
    for i, q in enumerate(questions, 1):
        if not isinstance(q, dict):
            continue
        prompt = str(q.get("prompt") or "")
        opts = [o for o in (q.get("options") or []) if isinstance(o, dict)]
        multi = bool(q.get("allow_multiple"))
        print("\n  问题 {}: {}".format(i, prompt))
        for j, o in enumerate(opts, 1):
            print("    {}. {}".format(j, o.get("label") or o.get("id") or ""))
        print("    0. 其他（自己输入）")
        hint = "序号，多选用逗号分隔" if multi else "序号"
        try:
            raw = str(input_fn("  请选择（{}）: ".format(hint)) or "").strip()
        except (EOFError, KeyboardInterrupt):
            raw = ""
        picked = []
        if raw == "0" or raw.lower() in ("other", "其他"):
            try:
                custom = str(input_fn("  请输入: ") or "").strip()
            except (EOFError, KeyboardInterrupt):
                custom = ""
            picked.append(custom or "(空)")
        else:
            for part in raw.replace("，", ",").split(","):
                part = part.strip()
                if part.isdigit() and 1 <= int(part) <= len(opts):
                    o = opts[int(part) - 1]
                    picked.append(o.get("label") or o.get("id") or part)
                elif part:
                    picked.append(part)
            if not multi:
                picked = picked[:1]
        if not picked:
            picked = ["(未选择)"]
        answers.append("{}: {}".format(prompt, "；".join(picked)))
    if not answers:
        return "Error: 没有有效的问题"
    return "用户的选择:\n" + "\n".join(answers)


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
