from __future__ import annotations

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
usage.py — Token/成本聚合（F5，叶子模块）

存储：usage/usage_YYYY-MM.json（按天，JSON Lines 格式追加）
每行: {"ts": "...", "session_id": "...", "model": "...", "prompt": N, "completion": N}

零本地模块依赖。
"""
import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
_USAGE_DIR = Path(os.path.dirname(os.path.abspath(__file__))) / "usage"
_lock = threading.Lock()


def _ensure_dir():
    _USAGE_DIR.mkdir(parents=True, exist_ok=True)


def _day_path(date: datetime | None = None) -> Path:
    d = date or datetime.now()
    return _USAGE_DIR / f"usage_{d.strftime('%Y-%m')}.json"


# ---------------------------------------------------------------------------
# Tracker 类
# ---------------------------------------------------------------------------
class Tracker:
    """
    会话级 token 追踪器。

    用法：
        tracker = Tracker("abc123")
        tracker.add("AngelOrDevil", 5000, 300)  # 每轮 LLM 调用后
        print(tracker.session_total())
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._prompt_total = 0
        self._completion_total = 0
        self._call_count = 0

    def add(self, model: str, prompt: int, completion: int):
        """累加内存 + 落盘。"""
        self._prompt_total += prompt
        self._completion_total += completion
        self._call_count += 1

        # 落盘（JSON Lines 追加）
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "session_id": self.session_id,
            "model": model,
            "prompt": prompt,
            "completion": completion,
        }
        try:
            with _lock:
                _ensure_dir()
                p = _day_path()
                with open(p, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass  # 写入失败不影响主流程

    def session_total(self) -> dict:
        """返回本会话累计 {prompt, completion, calls}。"""
        return {
            "prompt": self._prompt_total,
            "completion": self._completion_total,
            "calls": self._call_count,
        }

    def __repr__(self):
        t = self.session_total()
        return f"Tracker({self.session_id}): in={t['prompt']} out={t['completion']} ({t['calls']} calls)"


# ---------------------------------------------------------------------------
# 跨会话聚合
# ---------------------------------------------------------------------------
def _read_day_file(p: Path) -> list:
    """读取一天的 usage 文件，返回 entry 列表。"""
    if not p.exists():
        return []
    entries = []
    try:
        for line in p.read_text(encoding="utf-8").strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return entries


def day_total(date: datetime | None = None) -> dict:
    """
    跨会话按天聚合。
    返回: {"prompt": N, "completion": N, "sessions": N, "entries": N}
    """
    d = date or datetime.now()
    entries = _read_day_file(_day_path(d))
    prompt = sum(e.get("prompt", 0) for e in entries)
    completion = sum(e.get("completion", 0) for e in entries)
    sessions = len({e.get("session_id") for e in entries})
    return {
        "prompt": prompt,
        "completion": completion,
        "sessions": sessions,
        "entries": len(entries),
    }


def range_total(days: int = 7) -> dict:
    """
    近 N 天聚合。
    返回: {"prompt": N, "completion": N, "sessions": N, "days": N}
    """
    today = datetime.now()
    total_prompt = 0
    total_completion = 0
    session_set = set()
    days_with_data = 0

    for i in range(days):
        d = today - timedelta(days=i)
        entries = _read_day_file(_day_path(d))
        if entries:
            days_with_data += 1
        for e in entries:
            total_prompt += e.get("prompt", 0)
            total_completion += e.get("completion", 0)
            sid = e.get("session_id")
            if sid:
                session_set.add(sid)

    return {
        "prompt": total_prompt,
        "completion": total_completion,
        "sessions": len(session_set),
        "days": days_with_data,
    }


def model_breakdown(days: int = 7) -> dict:
    """
    按 model 聚合近 N 天。
    返回: {model_name: {"prompt": N, "completion": N}}
    """
    today = datetime.now()
    breakdown = {}

    for i in range(days):
        d = today - timedelta(days=i)
        entries = _read_day_file(_day_path(d))
        for e in entries:
            model = e.get("model", "unknown")
            if model not in breakdown:
                breakdown[model] = {"prompt": 0, "completion": 0}
            breakdown[model]["prompt"] += e.get("prompt", 0)
            breakdown[model]["completion"] += e.get("completion", 0)

    return breakdown
