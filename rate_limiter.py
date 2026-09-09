#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rate_limiter.py — per-model 限流（H2，叶子模块）

对齐 szclaw providers/rate_limiter.py：每个 "provider:model" 一个 limiter 实例，
某 model 触发 429 只影响该 model，不串扰其他 model（陷阱 C）。

极简版用「最小间隔」而非完整令牌桶（够防 429 风暴）：
同一 key 的两次请求强制间隔 ≥ min_interval；on_429 可临时抬高间隔。

零本地依赖（仅 config 读开关；config 同为叶子，无循环）。
"""
from __future__ import annotations

import threading
import time

import config


class ModelRateLimiter:
    """最小间隔限流器：保证两次 acquire 之间的最小间隔。"""

    def __init__(self, min_interval: float):
        self.min_interval = float(min_interval)
        self._last = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """阻塞到满足最小间隔，返回实际等待秒数（≥0）。

        持锁 sleep → 同 key 的并发请求被串行化；不同 key 是不同实例，互不阻塞。
        """
        with self._lock:
            now = time.monotonic()
            wait = self._last + self.min_interval - now
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            return max(0.0, wait)

    def bump(self, min_interval: float) -> None:
        """临时抬高最小间隔（收到 429 时调用；只升不降）。"""
        with self._lock:
            if min_interval > self.min_interval:
                self.min_interval = min_interval


_limiters: dict = {}
_limiters_lock = threading.Lock()


def _base_interval() -> float:
    """读 config["rate_limit"]（默认 0=关闭）。"""
    try:
        return max(0.0, float(config.get("rate_limit", 0)))
    except (TypeError, ValueError):
        return 0.0


def enabled() -> bool:
    """限流是否开启（rate_limit > 0）。"""
    return _base_interval() > 0


def get_limiter(key: str) -> ModelRateLimiter:
    """按 key（"provider:model"）惰性创建 limiter 实例。"""
    with _limiters_lock:
        lim = _limiters.get(key)
        if lim is None:
            lim = ModelRateLimiter(_base_interval())
            _limiters[key] = lim
        return lim


def on_429(key: str, retry_after=None) -> None:
    """
    收到 429 时临时抬高该 model 的间隔。

    优先用 Retry-After 头（秒）；缺失则抬到至少 2 秒。
    只影响该 key，其他 model 不受影响。
    """
    lim = get_limiter(key)
    base = _base_interval()
    try:
        ra = float(retry_after) if retry_after is not None else 0.0
    except (TypeError, ValueError):
        ra = 0.0
    if ra < 0:
        ra = 0.0
    new_interval = max(lim.min_interval, base, ra if ra > 0 else 2.0)
    lim.bump(new_interval)
