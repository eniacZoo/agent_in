#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
capability.py — 模型能力探测 + 缓存（H4，叶子模块）

探测 tool_call / vision / max_context 三项能力，结果按 model 缓存
（capability_cache.json，TTL 默认 7 天）。

陷阱 A：本模块**不 import llm**（避免 llm ↔ capability 循环）。
自带 urllib 最小请求函数，探测所需 base_url / api_key / model 由调用方
（llm.py）传入。

零本地依赖（纯 stdlib），单项探测失败记 False/None，不抛异常。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

# 项目根（__file__ 锁定）
_PROJECT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
_CACHE_FILE = _PROJECT_DIR / "capability_cache.json"

# 1x1 透明 PNG（base64），vision 探测用
_PNG_1X1_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# 探测超参：所有探测请求 max_tokens=1，尽量不吃 token
_PROBE_TIMEOUT = 15

# "不支持" 类错误提示（小写匹配）
_UNSUPPORTED_HINTS = (
    "not supported", "unsupported", "does not support",
    "no such object", "invalid", "is not allowed",
)


# ---------------------------------------------------------------------------
# 最小请求（自包含，不依赖 llm）
# ---------------------------------------------------------------------------
def _post(base_url: str, api_key: str, payload: dict, timeout: int = _PROBE_TIMEOUT):
    """
    最小 chat/completions 请求。

    返回 (ok: bool, err: str, obj: dict|None)
    ok=True 时 err 为空、obj 为解析后的响应；ok=False 时 err 为 "状态码 响应体/异常"。
    """
    url = f"{base_url.rstrip('/')}/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            obj = json.loads(resp.read().decode("utf-8", "replace"))
        return True, "", obj
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return False, f"{e.code} {body}", None
    except Exception as e:  # 网络类：探测不抛，交给单项判定
        return False, str(e), None


def _looks_unsupported(err: str, topic: str) -> bool:
    """错误信息是否明确表达「不支持该能力」。"""
    low = err.lower()
    if topic not in low:
        return False
    return any(h in low for h in _UNSUPPORTED_HINTS)


# ---------------------------------------------------------------------------
# 单项探测
# ---------------------------------------------------------------------------
def _probe_tool_call(base_url: str, api_key: str, model: str) -> bool:
    """
    tool_call：带最小单参数 tool 发 ping。
    正常返回 → True；明确报 tools-unsupported 类错误 → False；
    其他错误（网络等）→ 保守记 True（宁可尝试，见 plan 风险表）。
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "tools": [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the current weather of a city",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string", "description": "City name"}},
                    "required": ["city"],
                },
            },
        }],
        "tool_choice": "auto",
        "max_tokens": 1,
        "stream": False,
    }
    ok, err, _obj = _post(base_url, api_key, payload)
    if ok:
        return True
    if _looks_unsupported(err, "tool"):
        return False
    return True  # 拿不准 → 保守 True（宁可尝试）


def _probe_vision(base_url: str, api_key: str, model: str) -> bool:
    """
    vision：发 1x1 PNG 的 image_url。
    正常返回 → True；明确报 image-unsupported 类错误 → False；
    其他错误 → 保守记 True。
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "ping"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_PNG_1X1_B64}"}},
        ]}],
        "max_tokens": 1,
        "stream": False,
    }
    ok, err, _obj = _post(base_url, api_key, payload)
    if ok:
        return True
    if _looks_unsupported(err, "image") or _looks_unsupported(err, "vision") \
            or _looks_unsupported(err, "multimodal"):
        return False
    return True  # 拿不准 → 保守 True


_MAX_CTX_PATTERNS = (
    r"maximum context length is (\d+)",
    r"maximum context length of (\d+)",
    r"context length of (\d+)",
    r"context_length(?:_exceeded)?[^\d]*(\d+)",
    r"exceeds? the maximum[^\d]*(\d+)",
)


def _probe_max_context(base_url: str, api_key: str, model: str):
    """
    max_context：发远超上限的长文本，从 400 的 context_length_exceeded 错误
    信息里解析上限。取不到 → None。
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "x" * 200_000}],
        "max_tokens": 1,
        "stream": False,
    }
    ok, err, _obj = _post(base_url, api_key, payload)
    if ok:
        return None  # 未触发溢出，无法得知上限
    for pat in _MAX_CTX_PATTERNS:
        m = re.search(pat, err, re.IGNORECASE)
        if m:
            val = int(m.group(1))
            if 1000 <= val <= 100_000_000:
                return val
    return None


# ---------------------------------------------------------------------------
# 探测入口
# ---------------------------------------------------------------------------
def probe(base_url: str, api_key: str, model: str, timeout: int = _PROBE_TIMEOUT) -> dict:
    """
    探测三项能力，返回：
    {"tool_call": bool, "vision": bool, "max_context": int|None, "probed_at": ts}
    单项失败记 False/None，不抛异常。
    """
    return {
        "tool_call": _probe_tool_call(base_url, api_key, model),
        "vision": _probe_vision(base_url, api_key, model),
        "max_context": _probe_max_context(base_url, api_key, model),
        "probed_at": time.time(),
    }


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
def load_cache(model: str):
    """读 capability_cache.json 中该 model 的缓存条目，无/损坏 → None。"""
    if not _CACHE_FILE.exists():
        return None
    try:
        raw = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    entry = raw.get(model)
    return entry if isinstance(entry, dict) else None


def save_cache(model: str, cap: dict) -> None:
    """原子写（先 .tmp 再 replace），保留其他 model 的缓存。"""
    data = {}
    if _CACHE_FILE.exists():
        try:
            data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (json.JSONDecodeError, OSError):
            data = {}
    data[model] = cap
    tmp = _CACHE_FILE.with_name(_CACHE_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_CACHE_FILE)


def cache_valid(model: str, ttl_days: float) -> bool:
    """缓存是否存在且未过期。"""
    cap = load_cache(model)
    if not cap:
        return False
    try:
        age = time.time() - float(cap.get("probed_at", 0))
    except (TypeError, ValueError):
        return False
    return age < ttl_days * 86400


def get(base_url: str, api_key: str, model: str, force: bool = False,
        ttl_days: float = 7.0) -> dict:
    """
    取能力结果：有缓存且未过期（且非 force）→ 返回缓存；否则 probe + save_cache。
    """
    if not force and cache_valid(model, ttl_days):
        return load_cache(model)
    cap = probe(base_url, api_key, model)
    save_cache(model, cap)
    return cap
