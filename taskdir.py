#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
taskdir.py — 任务临时目录的生命周期（P4，叶子模块，仅 stdlib）

布局：{work_dir}/temp/tasks/<session_id>/
  .active        每次工具执行 touch，用作"最后活动时间"
  todo.json      todo_write 的持久化（P2）
  plan.md        用户确认后的计划（P2）
  task_notes.md  长任务笔记（原来共用的 temp/task_notes.md，现按任务隔离）
  jobs/          后台任务日志（P3）
  out/           被截断的大输出全文（P3）
  run/           python 工具的脚本（任务结束且没有未完成待办时清理）

规则：
  * 每个会话一个目录；恢复会话沿用同一目录（session_id 不变）。
  * 任务结束可列出"遗留文件"，默认清理（保留 KEEP_NAMES）。
  * sweep(): 最后活动超过 ttl_days 的任务目录、旧版 temp/ 平铺文件一并清理；
    正在运行/恢复的会话（protect）不动。
"""
import os
import shutil
import time

KEEP_NAMES = ("todo.json", "plan.md", "task_notes.md", ".active")
SUBDIRS = ("jobs", "out", "run")
_SAFE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def _safe_id(session_id):
    sid = "".join(c for c in str(session_id or "") if c in _SAFE_CHARS)
    return sid or "default"


def temp_root(work_dir):
    return os.path.join(os.path.abspath(work_dir), "temp")


def tasks_root(work_dir):
    return os.path.join(temp_root(work_dir), "tasks")


def task_dir(work_dir, session_id, create=True):
    d = os.path.join(tasks_root(work_dir), _safe_id(session_id))
    if create:
        os.makedirs(d, exist_ok=True)
        touch(d)
    return d


def sub_dir(work_dir, session_id, name):
    d = os.path.join(task_dir(work_dir, session_id), name)
    os.makedirs(d, exist_ok=True)
    return d


def touch(d):
    try:
        with open(os.path.join(d, ".active"), "a"):
            pass
        os.utime(os.path.join(d, ".active"), None)
    except OSError:
        pass


def last_active(d):
    """目录最后活动时间（.active 的 mtime，缺失则取目录内最新文件 mtime）。"""
    best = 0.0
    try:
        best = os.path.getmtime(os.path.join(d, ".active"))
    except OSError:
        pass
    if best:
        return best
    for dp, _dn, fns in os.walk(d):
        for fn in fns:
            try:
                best = max(best, os.path.getmtime(os.path.join(dp, fn)))
            except OSError:
                pass
    if not best:
        try:
            best = os.path.getmtime(d)
        except OSError:
            best = 0.0
    return best


def _size_of(path):
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    total = 0
    for dp, _dn, fns in os.walk(path):
        for fn in fns:
            try:
                total += os.path.getsize(os.path.join(dp, fn))
            except OSError:
                pass
    return total


def leftovers(work_dir, session_id):
    """任务目录里除 KEEP_NAMES 之外的遗留项：[(相对路径, 字节数)]。"""
    d = task_dir(work_dir, session_id, create=False)
    out = []
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        if name in KEEP_NAMES:
            continue
        p = os.path.join(d, name)
        sz = _size_of(p)
        if os.path.isdir(p) and sz == 0:
            continue
        out.append((name + ("/" if os.path.isdir(p) else ""), sz))
    return out


def clean_task(work_dir, session_id, drop_all=False):
    """清理任务目录遗留项（默认保留 KEEP_NAMES；drop_all=True 整个目录删除）。
    返回 (删除项数, 释放字节)。"""
    d = task_dir(work_dir, session_id, create=False)
    if not os.path.isdir(d):
        return 0, 0
    n, freed = 0, 0
    if drop_all:
        freed = _size_of(d)
        shutil.rmtree(d, ignore_errors=True)
        return 1, freed
    for name in os.listdir(d):
        if name in KEEP_NAMES:
            continue
        p = os.path.join(d, name)
        sz = _size_of(p)
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
            n += 1
            freed += sz
        except OSError:
            pass
    return n, freed


def sweep(work_dir, ttl_days=7, protect=(), now=None, dry_run=False):
    """清理过期的临时内容。
    - temp/tasks/<id>/：最后活动超过 ttl 天，且不在 protect 里
    - temp/ 下旧版平铺文件/目录（非 tasks、非点开头）：mtime 超过 ttl 天
    返回 {"task_dirs": n, "legacy": n, "bytes": b, "items": [路径...]}。
    """
    now = now if now is not None else time.time()
    cutoff = now - float(ttl_days) * 86400
    protect = {_safe_id(p) for p in protect if p}
    res = {"task_dirs": 0, "legacy": 0, "bytes": 0, "items": []}

    troot = tasks_root(work_dir)
    if os.path.isdir(troot):
        for name in os.listdir(troot):
            p = os.path.join(troot, name)
            if not os.path.isdir(p) or name in protect:
                continue
            if last_active(p) < cutoff:
                res["items"].append(p)
                res["task_dirs"] += 1
                res["bytes"] += _size_of(p)
                if not dry_run:
                    shutil.rmtree(p, ignore_errors=True)

    root = temp_root(work_dir)
    if os.path.isdir(root):
        for name in os.listdir(root):
            if name == "tasks" or name.startswith("."):
                continue
            p = os.path.join(root, name)
            try:
                mt = os.path.getmtime(p)
            except OSError:
                continue
            if mt < cutoff:
                res["items"].append(p)
                res["legacy"] += 1
                res["bytes"] += _size_of(p)
                if not dry_run:
                    try:
                        if os.path.isdir(p):
                            shutil.rmtree(p, ignore_errors=True)
                        else:
                            os.remove(p)
                    except OSError:
                        pass
    return res


def fmt_bytes(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"
