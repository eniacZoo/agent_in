#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
debug.py — 长链路会话旁路记录（叶子：stdlib + logger 可选）

/debug on 后写 logs/debug/{session_id}/events.jsonl
/debug report 生成 workflow_session_report.md
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

ENABLED = False
SESSION_ID = ""
_EVENTS = []
_PROJECT = Path(os.path.dirname(os.path.abspath(__file__)))
_ROOT = _PROJECT / "logs" / "debug"


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def dir_for(session_id=None):
    sid = session_id or SESSION_ID or "unknown"
    d = _ROOT / sid
    d.mkdir(parents=True, exist_ok=True)
    return d


def start(session_id):
    global ENABLED, SESSION_ID, _EVENTS
    ENABLED = True
    SESSION_ID = session_id or "unknown"
    _EVENTS = []
    dir_for(SESSION_ID)
    emit("DEBUG_ON", {"session_id": SESSION_ID})


def stop():
    global ENABLED
    if ENABLED:
        emit("DEBUG_OFF", {})
    ENABLED = False


def emit(kind, data=None):
    if not ENABLED:
        return
    entry = {"ts": _now(), "kind": kind, "data": data or {}}
    _EVENTS.append(entry)
    path = dir_for() / "events.jsonl"
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def is_skill_path(path):
    p = (path or "").replace("\\", "/")
    return "skills/" in p and p.lower().endswith(".md")


def write_report(session_id=None):
    sid = session_id or SESSION_ID or "unknown"
    d = dir_for(sid)
    events_path = d / "events.jsonl"
    events = []
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not events:
        events = list(_EVENTS)
    tools = {}
    skills = 0
    for e in events:
        if e.get("kind") == "TOOL_CALL":
            name = (e.get("data") or {}).get("name") or "?"
            tools[name] = tools.get(name, 0) + 1
        if e.get("kind") == "SKILL_LOAD":
            skills += 1
    lines = [
        "# 会话工作流报告 — {}".format(sid),
        "",
        "生成时间: {}  事件数: {}".format(_now(), len(events)),
        "",
        "## 1. Agent-模型交互 & Tool 调度时间线",
        "",
    ]
    for e in events:
        kind = e.get("kind", "")
        ts = e.get("ts", "")
        data = e.get("data") or {}
        if kind == "USER":
            lines.append("### 👤 USER  `{}`".format(ts))
            lines.append((data.get("content") or "")[:500])
            lines.append("")
        elif kind == "THINK":
            lines.append("- 💭 THINK `{}` {}".format(ts, (data.get("content") or "")[:300]))
        elif kind == "AI":
            lines.append("- 🤖 AI `{}` {}".format(ts, (data.get("content") or "")[:300]))
        elif kind == "TOOL_CALL":
            lines.append("- 🔧 TOOL_CALL `{}` **{}**".format(ts, data.get("name", "")))
            args = str(data.get("args") or "")[:200]
            if args:
                lines.append("  - args: `{}`".format(args))
        elif kind == "SKILL_LOAD":
            lines.append("- ⭐ **SKILL_LOAD** `{}` skill=**{}**".format(ts, data.get("name", "")))
    lines.extend([
        "",
        "## 2. Skill 使用统计",
        "",
        "SKILL_LOAD 次数: {}".format(skills),
        "",
        "## 3. Tool 调用统计",
        "",
    ])
    if not tools:
        lines.append("- (无)")
    else:
        for name, n in sorted(tools.items(), key=lambda x: -x[1]):
            lines.append("- {}: {}".format(name, n))
    report = d / "workflow_session_report.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
