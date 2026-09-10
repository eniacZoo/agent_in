from __future__ import annotations

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
session.py — 会话持久化（F2，叶子模块）

存储：sessions/<session_id>.json
原子写：先写 .tmp 再 os.replace，防写一半损坏。

零本地模块依赖（仅 stdlib）。
"""
import json
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
_SESSIONS_DIR = Path(os.path.dirname(os.path.abspath(__file__))) / "sessions"
_lock = threading.Lock()


def _ensure_dir():
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _path(session_id: str) -> Path:
    return _SESSIONS_DIR / f"{session_id}.json"


# ---------------------------------------------------------------------------
# 核心接口
# ---------------------------------------------------------------------------
def save(session_id: str, messages: list, meta: dict | None = None) -> Path:
    """
    保存会话（原子写）。

    参数：
        session_id: 会话 ID
        messages: 对话轨迹列表（不含 system）
        meta: 可选元数据 {created, updated, provider, model, work_dir,
              total_prompt, total_completion, ...}

    返回：
        文件路径
    """
    _ensure_dir()
    now = datetime.now().isoformat(timespec="seconds")

    # 如果是首次保存，设 created
    p = _path(session_id)
    created = now
    if p.exists():
        try:
            old = json.loads(p.read_text(encoding="utf-8"))
            created = old.get("created", now)
        except Exception:
            pass

    data = {
        "session_id": session_id,
        "created": created,
        "updated": now,
        "messages": messages,
    }
    if meta:
        data.update(meta)

    # 原子写：.tmp → os.replace
    tmp = p.with_suffix(".tmp")
    content = json.dumps(data, ensure_ascii=False, indent=None)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, p)
    return p


def load(session_id: str) -> dict | None:
    """
    加载会话。

    返回：
        {"messages": [...], "meta": {...}} 或 None（不存在/损坏）

    损坏处理：
        自动备份为 .corrupt，返回 None。
    """
    p = _path(session_id)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # 损坏 → 备份
        try:
            corrupt = p.with_suffix(".corrupt")
            os.replace(p, corrupt)
        except OSError:
            pass
        return None

    # 校验 messages 里 tool_call/tool 配对完整性
    messages = raw.get("messages", [])
    messages = _validate_messages(messages)

    # 拆分 meta（去掉 messages 和 session_id）
    meta = {k: v for k, v in raw.items() if k not in ("messages", "session_id")}

    return {"messages": messages, "meta": meta}


def _tool_batch_end(messages: list, asst_idx: int):
    """assistant(tool_calls) 起，若后续 tool 结果配齐则返回最后一条 tool 的下标，否则 None。"""
    calls = messages[asst_idx].get("tool_calls") or []
    ids = [c.get("id") for c in calls if isinstance(c, dict)]
    if not ids:
        return asst_idx
    found = set()
    j = asst_idx + 1
    while j < len(messages) and messages[j].get("role") == "tool":
        found.add(messages[j].get("tool_call_id"))
        j += 1
    if all(i in found for i in ids):
        return j - 1
    return None


def _validate_messages(messages: list) -> list:
    """
    校验 messages 中 tool_call / tool 配对完整性。
    完整的 assistant(tool_calls)+tool 批次视为合法末尾；
    仅残缺时截到上一个完整边界。
    """
    if not messages:
        return messages

    i = 0
    last_good = 0
    n = len(messages)
    while i < n:
        m = messages[i]
        role = m.get("role", "")
        if role == "user":
            last_good = i + 1
            i += 1
            continue
        if role == "assistant" and not m.get("tool_calls"):
            last_good = i + 1
            i += 1
            continue
        if role == "assistant" and m.get("tool_calls"):
            end = _tool_batch_end(messages, i)
            if end is None:
                return messages[:last_good]
            last_good = end + 1
            i = end + 1
            continue
        return messages[:last_good]
    return messages[:last_good]


def list_sessions(limit: int = 20) -> list:
    """
    列出所有会话，按 updated 倒序。
    返回: [{"session_id", "updated", "model", "first_user", "num_messages", ...}]
    """
    _ensure_dir()
    sessions = []
    for f in _SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        # 提取首条 user 消息摘要
        first_user = ""
        for m in data.get("messages", []):
            if m.get("role") == "user":
                content = m.get("content", "")
                if isinstance(content, str):
                    first_user = content[:40]
                break

        sessions.append({
            "session_id": data.get("session_id", f.stem),
            "updated": data.get("updated", ""),
            "created": data.get("created", ""),
            "model": data.get("model", ""),
            "first_user": first_user,
            "num_messages": len(data.get("messages", [])),
        })

    sessions.sort(key=lambda x: x.get("updated", ""), reverse=True)
    return sessions[:limit]


def latest() -> str | None:
    """返回最近一个 session_id。"""
    sessions = list_sessions(limit=1)
    if sessions:
        return sessions[0]["session_id"]
    return None


def delete(session_id: str) -> bool:
    """删除会话文件。返回是否成功。"""
    p = _path(session_id)
    if p.exists():
        p.unlink()
        return True
    return False
