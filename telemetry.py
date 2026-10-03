#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
telemetry.py — 会话级质量遥测（叶子模块，零本地依赖）

用途：把"harness 有没有在空转"量化出来，写进 session meta，并在 /status 展示。
参考 Grok Build 的 signals.json（doom loop / 原样重试 / 压缩次数 / 窗口占用）。

计数项：
  rounds          工具轮次累计
  reread          同一 read_file 参数重复读取（命中缓存）次数
  repeat_calls    同一工具同一参数重复调用次数
  truncations     工具参数被截断/非法 JSON 被拒绝次数
  finish_length   finish_reason=length 次数（输出撞上 max_tokens）
  errors          工具返回 Error / 被拒绝次数
  max_err_streak  连续工具错误的最大值
  compactions     上下文压缩次数
  prompt_total    累计 prompt tokens（来自 usage）
  cache_hit       累计命中缓存的 prompt tokens
  peak_prompt     单次 prompt 峰值
  requests        LLM 请求数
"""

_COUNTERS = (
    "rounds", "reread", "repeat_calls", "truncations", "finish_length",
    "errors", "max_err_streak", "compactions", "prompt_total", "cache_hit",
    "peak_prompt", "requests", "stops",
)


def cache_hit_tokens(usage) -> int:
    """从 usage 里取命中缓存的 prompt tokens。
    DeepSeek: prompt_cache_hit_tokens；OpenAI/vLLM: prompt_tokens_details.cached_tokens。"""
    if not isinstance(usage, dict):
        return 0
    v = usage.get("prompt_cache_hit_tokens")
    if isinstance(v, int):
        return v
    det = usage.get("prompt_tokens_details")
    if isinstance(det, dict) and isinstance(det.get("cached_tokens"), int):
        return det["cached_tokens"]
    return 0


class Telemetry:
    def __init__(self, data=None):
        self.c = {k: 0 for k in _COUNTERS}
        self.err_streak = 0
        if isinstance(data, dict):
            for k in _COUNTERS:
                v = data.get(k)
                if isinstance(v, int):
                    self.c[k] = v

    def add(self, key, n=1):
        self.c[key] = self.c.get(key, 0) + n

    def peak(self, key, value):
        if value > self.c.get(key, 0):
            self.c[key] = value

    def tool_result(self, ok: bool):
        """记一次工具结果，维护连续错误。返回当前连续错误数。"""
        if ok:
            self.err_streak = 0
        else:
            self.err_streak += 1
            self.add("errors")
            self.peak("max_err_streak", self.err_streak)
        return self.err_streak

    def usage(self, usage):
        p = int((usage or {}).get("prompt_tokens", 0) or 0)
        self.add("requests")
        self.add("prompt_total", p)
        self.add("cache_hit", cache_hit_tokens(usage))
        self.peak("peak_prompt", p)

    @property
    def cache_rate(self) -> float:
        t = self.c["prompt_total"]
        return (self.c["cache_hit"] / t) if t else 0.0

    def to_dict(self):
        return dict(self.c)

    def summary_lines(self):
        c = self.c
        return [
            f"请求 {c['requests']}  工具轮次 {c['rounds']}  压缩 {c['compactions']}  "
            f"缓存命中 {self.cache_rate * 100:.0f}%",
            f"重复读 {c['reread']}  重复调用 {c['repeat_calls']}  截断拒绝 {c['truncations']}  "
            f"length 截断 {c['finish_length']}",
            f"工具错误 {c['errors']}（最长连续 {c['max_err_streak']}）  "
            f"prompt 峰值 {c['peak_prompt']}  自动停机 {c['stops']}",
        ]
