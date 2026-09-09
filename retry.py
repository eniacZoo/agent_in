#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
retry.py — 可重试判定 + 指数退避（H1，叶子模块，纯函数可单测）

零本地依赖（仅 config 读默认值；config 同为叶子，无循环）。
对齐 szclaw providers/retry_chat_model.py 思路，极简版：
- 区分可重试（429/5xx/网络类）与不可重试（401/403/其他 4xx）
- 指数退避 + jitter：delay = min(cap, base * 2**attempt) (+ 0~0.3*delay)
"""
from __future__ import annotations

import random
import socket
import urllib.error

import config


# ---------------------------------------------------------------------------
# 可重试判定
# ---------------------------------------------------------------------------
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def is_retryable_status(status: int) -> bool:
    """HTTP 状态码是否可重试（429 / 5xx）。"""
    return status in RETRYABLE_STATUS


def is_retryable_exception(exc) -> bool:
    """
    异常是否可重试。

    - HTTPError → 转 is_retryable_status(e.code)
      （4xx 除 429 / 鉴权 401、403 → 不可重试，不浪费时间）
    - 网络类（URLError / ConnectionError / TimeoutError / socket.timeout）→ 可重试
    - 其他 → 不可重试
    """
    if isinstance(exc, urllib.error.HTTPError):
        return is_retryable_status(exc.code)
    if isinstance(exc, (TimeoutError, ConnectionError, socket.timeout)):
        # socket.timeout 是 TimeoutError 的别名（3.10+），双列以兼容 3.8
        return True
    if isinstance(exc, urllib.error.URLError):
        # 网络层错误（reason 通常为 socket.timeout / ConnectionRefusedError 等）
        reason = getattr(exc, "reason", None)
        if isinstance(reason, Exception):
            return is_retryable_exception(reason)
        return True
    return False


# ---------------------------------------------------------------------------
# 指数退避
# ---------------------------------------------------------------------------
def backoff_delay(attempt: int, base: float | None = None, cap: float = 30.0,
                  jitter: bool = True) -> float:
    """
    指数退避秒数：min(cap, base * 2**attempt)，可选 0~0.3*delay 的 jitter。

    参数：
        attempt: 第几次重试（0-based）
        base: 基准秒数，None 则读 config["retry_base_delay"]（默认 1.0）
        cap: 上限秒数（默认 30，防退避过久卡住）
        jitter: 是否加随机抖动（防多客户端同时重试）
    """
    if base is None:
        try:
            base = float(config.get("retry_base_delay", 1.0))
        except (TypeError, ValueError):
            base = 1.0
    delay = min(cap, base * (2 ** max(0, attempt)))
    if jitter:
        delay += random.uniform(0.0, 0.3 * delay)
    return delay


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
def max_retries() -> int:
    """读 config["max_retries"]（默认 3），下限 0。"""
    try:
        return max(0, int(config.get("max_retries", 3)))
    except (TypeError, ValueError):
        return 3
