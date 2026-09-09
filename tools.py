#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools.py — Agent 工具定义与执行

5 个工具：read_file, write_file, edit_file, shell, view_image
零依赖，跨平台（Windows / Linux / macOS）
"""
import os
import re
import shutil
import subprocess
import sys
import time
import locale
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
            "description": "Read the content of a file. Returns content with line numbers. Use start_line/end_line for partial reads of large files.",
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
            "description": "Execute a shell command and return its output. Use for: running scripts, listing files (ls/dir), git operations, grep/find, package management, etc.",
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
]


def _load_tools():
    """构建 TOOLS 列表 = 固定 5 个基础工具（技能不注册为 function）。"""
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
        blocked = _apply_verdict(
            tool_guard.assess_overwrite(str(path)),
            tool_name, "file_write", confirm_fn, input_fn, session_id)
        if blocked is not None:
            return blocked

    if tool_name == "edit_file":
        path = _resolve_path(args.get("path", ""))
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
def _exec_read_file(args):
    path = _resolve_path(args["path"])
    if not path.exists():
        return f"Error: File not found: {path}"
    if not path.is_file():
        return f"Error: Not a file: {path}"

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

    # 截断保护
    if end - start + 1 > MAX_READ_LINES:
        end = start + MAX_READ_LINES - 1
        truncated = True
    else:
        truncated = False

    # 带行号输出
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
def _exec_write_file(args):
    path = _resolve_path(args["path"])
    content = args.get("content", "")

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
    return env


def _exec_shell(args, confirm_fn=None):
    """执行 shell 命令。危险/逃逸检测 + 审批已上提到 execute() 统一安全链。"""
    command = args.get("command", "").strip()
    timeout = min(int(args.get("timeout", SHELL_TIMEOUT)), 300)

    if not command:
        return "Error: Empty command"

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
        return f"Error: Command timed out after {timeout}s: {command}"
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
