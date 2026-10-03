#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
todo.py — 任务清单（P2，叶子模块，仅 stdlib）

todo_write 工具的状态层。持久化到 {task_dir}/todo.json，会话恢复后仍在。
merge 语义（参考 Grok Build）：默认只发变化的条目（id + status），按 id 合并。
"""
import json
import os

STATUSES = ("pending", "in_progress", "completed", "cancelled")
_MARK = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]", "cancelled": "[-]"}


def _path(task_dir):
    return os.path.join(task_dir, "todo.json")


def load(task_dir):
    try:
        with open(_path(task_dir), encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("todos") if isinstance(data, dict) else data
        return [t for t in (items or []) if isinstance(t, dict) and t.get("id")]
    except (OSError, json.JSONDecodeError):
        return []


def save(task_dir, items):
    os.makedirs(task_dir, exist_ok=True)
    tmp = _path(task_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"todos": items}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path(task_dir))


def apply(items, updates, merge=True):
    """把 updates 应用到 items，返回新列表。非法条目被忽略。"""
    clean = []
    for u in updates or []:
        if not isinstance(u, dict) or not u.get("id"):
            continue
        d = {"id": str(u["id"])}
        if u.get("content"):
            d["content"] = str(u["content"])
        st = u.get("status")
        if st in STATUSES:
            d["status"] = st
        clean.append(d)
    if not merge:
        out = []
        for d in clean:
            d.setdefault("content", d["id"])
            d.setdefault("status", "pending")
            out.append(d)
        return out
    by_id = {t["id"]: dict(t) for t in items}
    order = [t["id"] for t in items]
    for d in clean:
        if d["id"] in by_id:
            by_id[d["id"]].update(d)
        else:
            d.setdefault("content", d["id"])
            d.setdefault("status", "pending")
            by_id[d["id"]] = d
            order.append(d["id"])
    return [by_id[i] for i in order]


def render(items):
    if not items:
        return "(待办为空)"
    return "\n".join(f"{_MARK.get(t.get('status'), '[ ]')} {t['id']}: {t.get('content', '')}" for t in items)


def progress_key(items):
    """用于判断"有没有进展"：完成/取消数 + 当前 in_progress 的 id。"""
    done = sum(1 for t in items if t.get("status") in ("completed", "cancelled"))
    cur = ",".join(t["id"] for t in items if t.get("status") == "in_progress")
    return (done, cur)


def unfinished(items):
    return [t for t in items if t.get("status") in ("pending", "in_progress")]


def reminder(items, plan_path=None):
    """追加在请求尾部的简短提醒（不进系统提示、不破坏缓存前缀）。无待办返回空串。"""
    if not items:
        return ""
    lines = ["[系统提醒] 当前待办（用 todo_write 更新状态，只发变化的条目）:", render(items)]
    if plan_path:
        lines.append(f"计划文件: {plan_path}")
    return "\n".join(lines)
