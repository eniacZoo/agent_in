#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
logger.py — 轻量 JSON Lines 日志模块

功能：
- 按天分文件写入 logs/agent_YYYY-MM-DD.log
- 支持 DEBUG/INFO/WARN/ERROR 级别
- WARN/ERROR 同时输出到终端（彩色）
- 单文件 > 10MB 自动归档
- 零依赖，仅 stdlib
"""
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# 内联最小 ANSI 颜色（F1 陷阱A处置：去掉 import ui）
# ---------------------------------------------------------------------------
def _init_color():
    """根据 COLOR 环境变量决定是否启用颜色。"""
    color_mode = os.environ.get("COLOR", "auto")
    if color_mode == "never":
        use = False
    elif color_mode == "always":
        use = True
    else:
        use = sys.stdout.isatty()
    return use


_USE_COLOR = _init_color()
_C_YELLOW = "\033[33m" if _USE_COLOR else ""
_C_RED = "\033[31m" if _USE_COLOR else ""
_C_RESET = "\033[0m" if _USE_COLOR else ""


def _c(text, color):
    """最小彩色打印。"""
    if not _USE_COLOR:
        return text
    return f"{color}{text}{_C_RESET}"


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
LOG_DIR = Path(os.path.dirname(os.path.abspath(__file__))) / "logs"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
_current_level = LEVEL_ORDER.get(LOG_LEVEL, 1)

_lock = threading.Lock()
_file_handle = None
_current_date = None


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------
def _get_log_path():
    """返回当日日志文件路径。"""
    today = datetime.now().strftime("%Y-%m-%d")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR / "agent_{}.log".format(today), today


def _ensure_handle():
    """确保文件句柄有效（跨天自动切换）。"""
    global _file_handle, _current_date
    path, today = _get_log_path()
    if _current_date != today or _file_handle is None:
        if _file_handle:
            try:
                _file_handle.close()
            except Exception:
                pass
        # 检查是否需要归档
        try:
            if path.exists() and path.stat().st_size > MAX_FILE_SIZE:
                archive = path.with_suffix(".log.gz")
                import gzip
                with open(path, "rb") as f_in, gzip.open(archive, "wb") as f_out:
                    f_out.write(f_in.read())
                path.unlink()
        except Exception:
            pass
        _file_handle = open(path, "a", encoding="utf-8")
        _current_date = today
    return _file_handle


def _now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def _print_terminal(level, event, data):
    """WARN/ERROR 输出到终端。"""
    ts = datetime.now().strftime("%H:%M:%S")
    if level == "WARN":
        msg = "  " + _c("[!] [{}] {}".format(ts, event), _C_YELLOW)
        if data:
            msg += " " + json.dumps(data, ensure_ascii=False)[:100]
        try:
            print(msg)
        except UnicodeEncodeError:
            print(msg.encode("ascii", errors="replace").decode("ascii"))
    elif level == "ERROR":
        msg = "  " + _c("[!!] [{}] {}".format(ts, event), _C_RED)
        if data:
            msg += " " + json.dumps(data, ensure_ascii=False)[:150]
        try:
            print(msg)
        except UnicodeEncodeError:
            print(msg.encode("ascii", errors="replace").decode("ascii"))


# ---------------------------------------------------------------------------
# 核心接口
# ---------------------------------------------------------------------------
def log(level, event, data=None):
    """
    记录一条日志。

    level: "DEBUG" / "INFO" / "WARN" / "ERROR"
    event: 事件名称（如 "llm_request"）
    data: dict，附加信息
    """
    if LEVEL_ORDER.get(level, 1) < _current_level:
        return

    entry = {
        "ts": _now_iso(),
        "level": level,
        "event": event,
    }
    if data:
        entry["data"] = data

    line = json.dumps(entry, ensure_ascii=False)

    # 写文件
    try:
        with _lock:
            handle = _ensure_handle()
            handle.write(line + "\n")
    except Exception:
        pass  # 日志写入失败不影响主流程

    # 终端输出
    if level in ("WARN", "ERROR"):
        _print_terminal(level, event, data)


def debug(event, data=None):
    log("DEBUG", event, data)


def info(event, data=None):
    log("INFO", event, data)


def warn(event, data=None):
    log("WARN", event, data)


def error(event, data=None):
    log("ERROR", event, data)


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def recent(n=20):
    """返回最近 n 条日志（当日文件）。"""
    # 先 flush 确保所有写入落盘
    with _lock:
        if _file_handle:
            try:
                _file_handle.flush()
            except Exception:
                pass
    path, _ = _get_log_path()
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        tail = lines[-n:]
        results = []
        for line in tail:
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return results
    except Exception:
        return []


def close():
    """关闭文件句柄（程序退出时调用）。"""
    global _file_handle
    with _lock:
        if _file_handle:
            try:
                _file_handle.close()
            except Exception:
                pass
            _file_handle = None
