#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ui.py — 终端 UI（框线、流式输出、工具调用展示、颜色、代码预览、进度 Spinner）
"""
import os
import time
import sys
import threading
import unicodedata


W = 58  # 框线宽度
APP_VERSION = "2.0"  # 产品版本；plan13 为第 13 次增量，此为功能完整快照

# ---------------------------------------------------------------------------
# 环境变量（v3.0）
# ---------------------------------------------------------------------------
STREAM_CODE = os.environ.get("STREAM_CODE", "1") in ("1", "true", "yes")
SPINNER_ENABLED = os.environ.get("SPINNER", "1") in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# ANSI 颜色（Windows 10+ 原生支持 VT100）
# ---------------------------------------------------------------------------
_COLOR_MODE = os.environ.get("COLOR", "auto")
if _COLOR_MODE == "never":
    _USE_COLOR = False
elif _COLOR_MODE == "always":
    _USE_COLOR = True
else:
    # auto: TTY 则开，否则关
    _USE_COLOR = sys.stdout.isatty()


def _enable_ansi():
    """Windows 下激活 ANSI/VT100 + 切换控制台到 UTF-8 代码页。

    PowerShell 默认控制台代码页是 GBK(cp936)/cp950：
      - 非 BMP emoji 显示为方框，U+FE0F 甚至不在 GBK 中；
      - 办公机字体通常也没有 emoji 字形，切 UTF-8 也救不了。
      - UI 因此只用 ASCII 标记。此处仍开 VT100，流重配 UTF-8 防管道崩溃。
    """
    if os.name == "nt":
        os.system("")  # 触发 Win10+ VT100 模式
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            ENABLE_VIRTUAL_TERMINAL = 0x0004
            # 给 stdout / stderr / stdin 都开 VT100
            for handle_id in (-11, -12, -10):
                handle = k32.GetStdHandle(handle_id)
                mode = ctypes.c_uint32()
                if k32.GetConsoleMode(handle, ctypes.byref(mode)):
                    k32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL)
            # 仍尝试 UTF-8 代码页（中文 Windows Terminal）；老 conhost 靠 ASCII 图标兜底
            k32.SetConsoleCP(65001)
            k32.SetConsoleOutputCP(65001)
        except Exception:
            pass
    # 标准流重配为 UTF-8（重定向/管道环境下默认是 cp936，strict 编码遇 emoji 会崩）
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        # 仅当 stdin 是管道时重配（控制台 TTY 用宽字符 API，无需动）
        if not sys.stdin.isatty():
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


_enable_ansi()

C_GRAY = "\033[90m" if _USE_COLOR else ""
C_CYAN = "\033[36m" if _USE_COLOR else ""
C_GREEN = "\033[32m" if _USE_COLOR else ""
C_YELLOW = "\033[33m" if _USE_COLOR else ""
C_RED = "\033[31m" if _USE_COLOR else ""
C_BLUE = "\033[34m" if _USE_COLOR else ""
C_MAGENTA = "\033[35m" if _USE_COLOR else ""
C_BOLD = "\033[1m" if _USE_COLOR else ""
C_ITALIC = "\033[3m" if _USE_COLOR else ""
C_RESET = "\033[0m" if _USE_COLOR else ""

# 办公机 GBK / 老字体没有 emoji 和盲文点阵，方块就是这个原因。
# 只用 ASCII，所有代码页都能显示。
ICO_OK = "[+]"
ICO_FAIL = "[x]"
ICO_WARN = "[!]"
ICO_ERR = "[!!]"
ICO_TOOL = "[>]"
ICO_USER = ">>"
ICO_AI = "::"
ICO_THINK = ".."
ICO_FILE = "#"
ICO_IMG = "[#]"
ICO_SKILL = "[*]"

_VERDICT_LABEL = {
    "allow": "放行",
    "confirmed": "已确认",
    "strong_confirmed": "已确认",
    "rejected": "已拒绝",
    "blocked": "阻断",
}


def _color(text, color):
    if not _USE_COLOR:
        return text
    return f"{color}{text}{C_RESET}"


def _now():
    return time.strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# 代码扩展名颜色映射
# ---------------------------------------------------------------------------
_EXT_COLORS = {
    ".py":   C_BLUE,
    ".js":   C_YELLOW,
    ".ts":   C_MAGENTA,
    ".tsx":  C_MAGENTA,
    ".jsx":  C_YELLOW,
    ".sh":   C_MAGENTA,
    ".bash": C_MAGENTA,
    ".zsh":  C_MAGENTA,
    ".json": C_YELLOW,
    ".md":   "",
    ".html": C_CYAN,
    ".css":  C_CYAN,
    ".yml":  C_GREEN,
    ".yaml": C_GREEN,
    ".toml": C_GREEN,
    ".ini":  C_GREEN,
    ".cfg":  C_GREEN,
    ".conf": C_GREEN,
    ".sql":  C_RED,
    ".go":   C_CYAN,
    ".rs":   C_YELLOW,
    ".c":    C_BLUE,
    ".h":    C_BLUE,
    ".cpp":  C_BLUE,
    ".java": C_BLUE,
    ".rb":   C_RED,
    ".php":  C_MAGENTA,
    ".swift": C_YELLOW,
    ".kt":   C_MAGENTA,
    ".dockerfile": C_CYAN,
    ".xml":  C_CYAN,
}


def _get_ext_color(path):
    """根据文件扩展名返回颜色代码。"""
    if not _USE_COLOR:
        return ""
    ext = os.path.splitext(path)[1].lower()
    # 特殊文件名
    basename = os.path.basename(path).lower()
    if basename in ("dockerfile", "makefile", "ruff.toml"):
        return C_CYAN
    return _EXT_COLORS.get(ext, "")


# ---------------------------------------------------------------------------
# 格式化
# ---------------------------------------------------------------------------
def fmt_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


# ---------------------------------------------------------------------------
# 框线
# ---------------------------------------------------------------------------
def turn_header(turn, session_id=""):
    left = f" #{turn} "
    right = f"  {_now()}  "
    mid = session_id[:8] if session_id else "agent"
    total = len(left) + len(right) + len(mid) + 4
    filler = max(W - total, 4)
    print()
    print(f" \u2500\u2500{left} {mid} \u2500" * 1 + "\u2500" * (filler - 3) + right + " \u2500")


def turn_sep(label=""):
    if label:
        colored = _color(label, C_CYAN)
        pad = max(W - 4 - len(label), 0)
        print(f"   \u2500" * 6 + f" {colored} " + "\u2500" * pad)
    else:
        print("   " + "\u2500" * (W - 4))


def turn_footer(usage=None, elapsed=None):
    turn_sep()
    if usage:
        line = f"  in {fmt_tokens(usage.get('prompt_tokens', 0)):<8} out {fmt_tokens(usage.get('completion_tokens', 0)):<8}"
        if elapsed is not None:
            line += f"  \u23f1 {elapsed:.1f}s"
        # reasoning tokens
        rt = 0
        comp_details = usage.get("completion_tokens_details", {})
        if comp_details:
            rt = comp_details.get("reasoning_tokens", 0)
        if rt:
            line += f"  (think: {fmt_tokens(rt)})"
        print(line)
    elif elapsed is not None:
        print(f"  \u23f1 {elapsed:.1f}s")
    print("   " + "\u2500" * (W - 4))


# ---------------------------------------------------------------------------
# 代码预览（v3.0 新增）
# ---------------------------------------------------------------------------
def preview_code(path, content, max_lines=40):
    """
    工具执行前，以代码块样式展示文件内容。
    带行号 + 扩展名语法颜色。
    """
    ext_color = _get_ext_color(path)
    lines = content.split("\n")
    # 去掉末尾空行
    while lines and lines[-1] == "":
        lines.pop()
    total = len(lines)
    shown = lines[:max_lines]

    # 标题栏
    title = f" {path} "
    inner_w = W - len(title) - 4
    print(f"  \u250c\u2500{title}\u2500" + "\u2500" * max(inner_w, 0))

    # 代码行
    for i, line in enumerate(shown, 1):
        line_display = line.rstrip()
        if ext_color:
            line_display = f"{ext_color}{line_display}{C_RESET}"
        print(f"  \u2502{i:>4}  {line_display}")

    if total > max_lines:
        remaining = total - max_lines
        print(f"  \u2502      {_color(f'... ({remaining} more lines)', C_GRAY)}")
    print(f"  \u2514" + "\u2500" * (W - 2))
    print()


def preview_edit(path, old_text, new_text, context_lines=2):
    """
    edit_file 工具执行前，以 diff 样式展示修改。
    old_text 红色删除线，new_text 绿色。
    """
    print(f"  {_color('edit', C_BOLD)} {_color(path, C_CYAN)}")
    print(f"  \u250c" + "\u2500" * (W - 2))

    # 旧内容（红色，前缀 -）
    old_lines = old_text.split("\n")
    for line in old_lines:
        display = line.rstrip()
        print(f"  \u2502{_color('-', C_RED)} {_color(display, C_RED)}")

    # 分隔
    print(f"  \u2502{_color('~' * 20, C_GRAY)}")

    # 新内容（绿色，前缀 +）
    new_lines = new_text.split("\n")
    for line in new_lines:
        display = line.rstrip()
        print(f"  \u2502{_color('+', C_GREEN)} {_color(display, C_GREEN)}")

    print(f"  \u2514" + "\u2500" * (W - 2))
    print()


# ---------------------------------------------------------------------------
# 进度 Spinner（v3.0 新增）
# ---------------------------------------------------------------------------
# spinner 擦行是否可用 VT100 的 "清到行尾" 序列（需要 TTY）
_VT_ERASE = sys.stdout.isatty()


def _disp_width(s):
    """估算字符串在终端的显示宽度（东亚宽字符按 2 列）。"""
    w = 0
    for ch in s:
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


class Spinner:
    """
    工具执行时的进度指示器。超过 delay 秒后才开始显示旋转动画。

    用法：
        with Spinner("shell: ls -la"):
            result = tools.execute(...)

    注意：工具执行过程中若需要用户输入（审批/确认），必须在 input() 前
    调用 pause()，输入完成后 resume()，否则旋转动画会覆写提示行和
    用户正在输入的回显（Windows PowerShell 下表现为"字符重叠、输入
    不生效"）。ui.confirm / ui.text_input 会自动做这件事。
    """
    FRAMES = ["|", "/", "-", "\\"]

    _active = None  # 当前活跃的 spinner 实例（供 confirm/text_input 暂停用）

    def __init__(self, text="executing", delay=2.0):
        self.text = text
        self.delay = delay
        self._stop_event = threading.Event()
        self._paused = threading.Event()
        self._thread = None
        self._t0 = 0
        self._last_width = 0  # 已绘制内容的显示宽度（用于擦行）

    # ---- 绘制 / 擦行 ----
    def _render(self, body):
        if _VT_ERASE:
            sys.stdout.write("\r" + body + "\033[K")
        else:
            pad = max(self._last_width - _disp_width(body), 0)
            sys.stdout.write("\r" + body + " " * pad)
        self._last_width = _disp_width(body)
        sys.stdout.flush()

    def _erase_line(self):
        if self._last_width <= 0:
            return
        if _VT_ERASE:
            sys.stdout.write("\r\033[K")
        else:
            sys.stdout.write("\r" + " " * self._last_width + "\r")
        self._last_width = 0
        sys.stdout.flush()

    # ---- 暂停 / 恢复（用户输入期间必须暂停）----
    def pause(self):
        if not self._paused.is_set():
            self._paused.set()
            self._erase_line()  # 若已开始绘制，先擦掉 spinner 行

    def resume(self):
        self._paused.clear()

    def _spin(self):
        i = 0
        while not self._stop_event.is_set():
            if not self._paused.is_set():
                elapsed = time.time() - self._t0
                if elapsed >= self.delay:
                    frame = self.FRAMES[i % len(self.FRAMES)]
                    # 截断文本
                    display_text = self.text[:40] if len(self.text) > 40 else self.text
                    self._render(f"  {frame} {display_text} ({elapsed:.0f}s)")
            i += 1
            time.sleep(0.1)
        # 清除 spinner 行
        self._erase_line()

    def __enter__(self):
        self._t0 = time.time()
        self._stop_event.clear()
        self._paused.clear()
        Spinner._active = self
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=0.5)
        if Spinner._active is self:
            Spinner._active = None
        return False


# ---------------------------------------------------------------------------
# 流式输出
# ---------------------------------------------------------------------------
class StreamDisplay:
    """
    处理流式 chunk 的展示。

    用法：
        disp = StreamDisplay(show_reasoning=True)
        for chunk in llm.chat(...):
            disp.handle(chunk)
        disp.finish()
    """

    def __init__(self, show_reasoning=False):
        self.show_reasoning = show_reasoning
        self._in_reasoning = False
        self._in_text = False
        self._text_buf = ""

    def handle(self, chunk):
        ctype = chunk["type"]

        if ctype == "reasoning":
            if self.show_reasoning:
                if not self._in_reasoning:
                    self._in_reasoning = True
                    turn_sep("thinking")
                    print(f"  {ICO_THINK} ", end="", flush=True)
                # v3.0: 斜体 + 灰色
                content = chunk["content"]
                if _USE_COLOR:
                    print(f"{C_ITALIC}{C_GRAY}{content}{C_RESET}", end="", flush=True)
                else:
                    print(content, end="", flush=True)

        elif ctype == "text":
            if not self._in_text:
                if self._in_reasoning:
                    print()  # reasoning 结束换行
                    self._in_reasoning = False
                self._in_text = True
                turn_sep("ai")
                print(f"  {ICO_AI} ", end="", flush=True)
            print(chunk["content"], end="", flush=True)
            self._text_buf += chunk["content"]

        elif ctype == "tool_call":
            if self._in_reasoning:
                print()
                self._in_reasoning = False
            if self._in_text:
                print()
                self._in_text = False
            self._print_tool_call(chunk)

        elif ctype == "error":
            if self._in_reasoning or self._in_text:
                print()
                self._in_reasoning = False
                self._in_text = False
            err_label = _color(ICO_ERR + " ERROR", C_RED)
            print(f"  {err_label}: {chunk['content']}")

    def finish(self):
        if self._in_reasoning or self._in_text:
            print()
            self._in_reasoning = False
            self._in_text = False
        return self._text_buf.strip()

    def _print_tool_call(self, chunk):
        name = chunk.get("name", "unknown")
        args = chunk.get("arguments", {})
        # 简短展示
        args_str = self._summarize_args(name, args)
        tool_icon = _color(ICO_TOOL, C_CYAN)
        name_c = _color(name, C_BOLD)
        print(f"  {tool_icon} {name_c}({args_str})")

    @staticmethod
    def _summarize_args(name, args):
        """生成简短的参数摘要。"""
        if name == "read_file":
            p = args.get("path", "?")
            sl = args.get("start_line")
            el = args.get("end_line")
            s = f" {p}"
            if sl or el:
                s += f" [{sl or 1}:{el or 'end'}]"
            return s
        elif name == "write_file":
            p = args.get("path", "?")
            c = args.get("content", "")
            lines = c.count("\n") + 1 if c else 0
            return f" {p} ({lines} lines)"
        elif name == "edit_file":
            p = args.get("path", "?")
            return f" {p}"
        elif name == "shell":
            cmd = args.get("command", "?")
            if len(cmd) > 50:
                cmd = cmd[:47] + "..."
            return f" {cmd}"
        elif name == "view_image":
            p = args.get("path", "?")
            return f" {p}"
        else:
            import json as _json
            s = _json.dumps(args, ensure_ascii=False)
            return s[:60] + ("..." if len(s) > 60 else "")


def print_tool_result(result, max_lines=30):
    """展示工具执行结果（缩进、截断）。"""
    lines = result.split("\n") if result else ["(empty)"]
    shown = lines[:max_lines]
    for line in shown:
        print(f"    {line}")
    if len(lines) > max_lines:
        print(f"    ... ({len(lines) - max_lines} more lines)")


def print_tool_status(name, args, verdict, result=""):
    """
    工具执行后固定一行：名、路径/命令截断、审批结果。
    危险命令原文在审批提示里已经出现；这里再带截断摘要。
    结果以 Error: 开头时标记失败，并附错误首行（审批「放行」不等于执行成功）。
    """
    summary = StreamDisplay._summarize_args(name, args or {}).strip()
    if len(summary) > 42:
        summary = summary[:39] + "..."
    label = _VERDICT_LABEL.get(verdict, str(verdict or "allow"))
    result_s = (result or "").lstrip()
    failed = result_s.startswith("Error:")
    if failed or verdict in ("rejected", "blocked"):
        mark = _color(ICO_FAIL, C_RED)
    elif verdict in ("confirmed", "strong_confirmed"):
        mark = _color(ICO_WARN, C_YELLOW)
    else:
        mark = _color(ICO_OK, C_GREEN)
    line = f"    {mark} {name} {summary} | {label}"
    if failed:
        err_line = result_s.split("\n")[0]
        if len(err_line) > 48:
            err_line = err_line[:45] + "..."
        line += " " + err_line
    print(line)


# ---------------------------------------------------------------------------
# 交互提示
# ---------------------------------------------------------------------------
def _pause_spinner():
    """暂停当前活跃的 spinner（若存在），避免旋转动画覆写输入行。"""
    sp = Spinner._active
    if sp is not None:
        sp.pause()


def _resume_spinner():
    sp = Spinner._active
    if sp is not None:
        sp.resume()


def confirm(prompt):
    """用户确认，返回 True/False。

    输入前暂停 spinner：否则动画线程每 0.1s 用 \r 重绘当前行，
    会覆写 [y/N] 提示和用户回显（Windows PowerShell 下表现为字符
    重叠、输入"不生效"）。
    """
    warn_icon = _color(ICO_WARN, C_YELLOW)
    _pause_spinner()
    try:
        answer = input("  {} {} [y/N]: ".format(warn_icon, prompt)).strip().lower()
        return answer in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    finally:
        _resume_spinner()


def text_input(prompt=""):
    """input() 包装：输入期间暂停 spinner。

    作为 approval.resolve 的 input_fn 使用（强确认键入完整命令/路径
    的 token）。用裸 input() 做 input_fn 时，spinner 会把用户正在
    键入的 token 糊掉，导致"token 不匹配"。
    """
    _pause_spinner()
    try:
        return input(prompt)
    finally:
        _resume_spinner()


def print_warning(msg):
    warn_icon = _color(ICO_WARN, C_YELLOW)
    print("  {} {}".format(warn_icon, msg))


def print_error(msg):
    err_icon = _color(ICO_ERR, C_RED)
    print("  {} {}".format(err_icon, msg))


def print_image_loaded(path, size_kb):
    """v3.0: 图片加载确认提示。"""
    icon = _color(ICO_IMG, C_CYAN)
    name = os.path.basename(path)
    print(f"  {icon} {name} ({size_kb}KB) {_color(ICO_OK, C_GREEN)}")


# ---------------------------------------------------------------------------
# 横幅 & 汇总
# ---------------------------------------------------------------------------
def print_banner(model, base_url, work_dir, show_reasoning, safe_mode=None,
                 context_limit=None, capability=None):
    print()
    print(f"  {'=' * W}")
    print(f"   {_color('agent_in', C_BOLD)} — Minimal CLI Agent  v{APP_VERSION}")
    print(f"   {'-' * W}")
    print(f"   Model      : {model}")
    print(f"   Base URL   : {base_url}")
    print(f"   Work Dir   : {work_dir}")
    if context_limit:
        print(f"   Context    : {fmt_tokens(context_limit)}")
    print(f"   Reasoning  : {'ON' if show_reasoning else 'OFF'}")
    print(f"   Code Prev  : {'ON' if STREAM_CODE else 'OFF'}")
    if safe_mode is not None:
        print(f"   SAFE_MODE  : {'ON' if safe_mode else 'OFF'}")
    if capability:
        tc = capability.get("tool_call")
        vi = capability.get("vision")
        tc_s = "yes" if tc else ("no" if tc is False else "?")
        vi_s = "yes" if vi else ("no" if vi is False else "?")
        print(f"   Caps       : tool_call {tc_s}  vision {vi_s}")
    print(f"  {'=' * W}")
    print()
    print(f"   命令: /quit 退出 | /new 新会话 | /help 帮助")
    print()


def print_summary(turns, total_prompt, total_completion):
    print()
    print(f"  {'=' * W}")
    print(f"   Session Summary")
    print(f"   {'-' * W}")
    print(f"   Turns        : {turns}")
    print(f"   Total tokens : in {fmt_tokens(total_prompt)} | out {fmt_tokens(total_completion)}")
    print(f"  {'=' * W}")


def print_help():
    print()
    print(f"  {'-' * W}")
    print("   命令:")
    print("     /quit, /exit, /q     — 退出")
    print("     /new                 — 新会话")
    print("     /sessions            — 最近会话")
    print("     /resume [id]         — 恢复会话")
    print("     /save                — 保存当前会话")
    print("     /status              — token / SAFE_MODE / 上下文")
    print("     /history             — 消息历史")
    print("     /config              — 当前配置")
    print("     /provider [name]     — 切换 provider")
    print("     /model [deepseek|qwen] — 切换 DeepSeek / 办公 Qwen")
    print("     /probe [force]       — 探测模型能力")
    print("     /memory              — 查看记忆")
    print("     /memory add <text>   — 追加记忆")
    print("     /memory del <kw>     — 删除匹配记忆")
    print("     /memory clear        — 清空记忆")
    print("     /ls skills           — 列出流程 md 与脚本 skill")
    print("     /read-skill [name]   — 打印 markdown 流程（read_file 的糖）")
    print("     /use <name> [path]   — 手动执行脚本 skill")
    print("     /del <name>          — 删除脚本 skill")
    print("     /logs [n]            — 最近日志")
    print("     /help                — 显示本帮助")
    print(f"  {'-' * W}")
