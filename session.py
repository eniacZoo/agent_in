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
import uuid
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


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def stamp_missing(messages, fallback_parent=None):
    """给缺 id/parent 的消息补链。返回最后一条的 id。"""
    prev = fallback_parent
    last = fallback_parent
    for m in messages:
        if not m.get("id"):
            m["id"] = new_id()
        if "parent" not in m:
            m["parent"] = prev
        prev = m["id"]
        last = m["id"]
    return last


def ensure_tree(messages, leaf_id=None):
    """旧线性会话补成单链。返回 (messages, leaf_id)。"""
    if not messages:
        return [], None
    stamp_missing(messages)
    ids = {m.get("id") for m in messages if m.get("id")}
    if not leaf_id or leaf_id not in ids:
        leaf_id = messages[-1].get("id")
    return messages, leaf_id


def path_to_leaf(messages, leaf_id):
    """root → 当前叶（含叶）。叶无效则退回整表。"""
    if not messages:
        return []
    by_id = {m.get("id"): m for m in messages if m.get("id")}
    if not leaf_id or leaf_id not in by_id:
        return list(messages)
    path = []
    seen = set()
    cur = leaf_id
    while cur and cur not in seen:
        seen.add(cur)
        m = by_id.get(cur)
        if not m:
            break
        path.append(m)
        cur = m.get("parent")
    path.reverse()
    return path


def absorb_path(tree, path_msgs):
    """把路径上尚未入树的节点挂上去。返回新 leaf_id。"""
    known = {m.get("id") for m in tree if m.get("id")}
    parent = None
    for m in path_msgs:
        mid = m.get("id")
        if mid in known:
            parent = mid
            continue
        if not mid:
            m["id"] = new_id()
            mid = m["id"]
        if "parent" not in m:
            m["parent"] = parent
        tree.append(m)
        known.add(mid)
        parent = mid
    return parent


def find_node(messages, key):
    """按完整 id 或唯一前缀找节点。"""
    key = (key or "").strip()
    if not key:
        return None
    exact = [m for m in messages if m.get("id") == key]
    if exact:
        return exact[0]
    hits = [m for m in messages if (m.get("id") or "").startswith(key)]
    return hits[0] if len(hits) == 1 else None


def format_tree(messages, leaf_id):
    """ASCII 行：id  role  摘要，当前叶标 ←。"""
    by_id = {m.get("id"): m for m in messages if m.get("id")}
    kids = {}
    roots = []
    for m in messages:
        p = m.get("parent")
        if p and p in by_id:
            kids.setdefault(p, []).append(m)
        else:
            roots.append(m)

    def _snippet(m):
        c = m.get("content") or ""
        if m.get("role") == "tool":
            c = "[tool] " + (c if isinstance(c, str) else "")
        elif m.get("role") == "assistant" and m.get("tool_calls"):
            names = [tc.get("function", {}).get("name", "?") for tc in m["tool_calls"]]
            c = "[tools: " + ", ".join(names) + "]"
        if not isinstance(c, str):
            c = str(c)
        c = c.replace("\n", " ")
        return (c[:40] + "…") if len(c) > 40 else c

    lines = []

    def walk(nodes, depth):
        for m in nodes:
            mark = " ←" if m.get("id") == leaf_id else ""
            pad = "  " * depth
            lines.append(
                f"  {pad}{m.get('id', '????????')}  {m.get('role', '?'):<9} {_snippet(m)}{mark}"
            )
            walk(kids.get(m.get("id"), []), depth + 1)

    walk(roots, 0)
    return lines


def _merge_messages(old, incoming):
    by_id = {}
    order = []
    for m in list(old or []) + list(incoming or []):
        mid = m.get("id")
        if not mid:
            continue
        if mid not in by_id:
            order.append(mid)
        by_id[mid] = m
    return [by_id[i] for i in order]


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

    # 如果是首次保存，设 created；已有文件则按 id 合并，避免中途存路径冲掉兄弟枝
    p = _path(session_id)
    created = now
    old_messages = []
    old_leaf = None
    if p.exists():
        try:
            old = json.loads(p.read_text(encoding="utf-8"))
            created = old.get("created", now)
            old_messages = old.get("messages") or []
            old_leaf = old.get("leaf_id")
        except Exception:
            pass

    stamp_missing(messages)
    leaf_id = (meta or {}).get("leaf_id") if meta else None
    if not leaf_id:
        leaf_id = messages[-1]["id"] if messages else old_leaf
    merged = _merge_messages(old_messages, messages)

    data = {
        "session_id": session_id,
        "created": created,
        "updated": now,
        "leaf_id": leaf_id,
        "messages": merged,
    }
    if meta:
        data.update({k: v for k, v in meta.items() if k != "leaf_id"})
        data["leaf_id"] = leaf_id

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

    # 校验 messages 里 tool_call/tool 配对完整性，再补树
    messages = raw.get("messages", [])
    messages = _validate_messages(messages)
    messages, leaf_id = ensure_tree(messages, raw.get("leaf_id"))

    # 拆分 meta（去掉 messages 和 session_id）
    meta = {k: v for k, v in raw.items() if k not in ("messages", "session_id")}
    meta["leaf_id"] = leaf_id

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
