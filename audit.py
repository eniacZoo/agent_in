#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit.py — S7 安全审计日志（append-only，独立文件/锁，不 import logger）

写 logs/audit_YYYY-MM.jsonl，按月追加，不 gzip、不轮转删除。
零依赖：json + os + threading + datetime + pathlib
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
_AUDIT_DIR = Path(os.path.dirname(os.path.abspath(__file__))) / "logs"
_lock = threading.Lock()


def _audit_path():
    """返回当月审计文件路径。"""
    month = datetime.now().strftime("%Y-%m")
    _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    return _AUDIT_DIR / "audit_{}.jsonl".format(month)


def _now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S.") + \
           "{:03d}Z".format(datetime.now().microsecond // 1000)


def log_security(
    event,
    session_id,
    tool,
    detail,
    severity,
    action,
    decision,
    rule_id,
    findings,
    pid=None,
    answer=None,
):
    """
    写入一条安全审计记录（append-only）。

    event:      "shell" | "file_write" | "file_edit" | "skill_load" | "skill_exec"
    session_id: 审计"谁"；非交互/未知 → "-"
    tool:       "shell" / "write_file" / "edit_file" / "skill_xxx"
    detail:     命令原文/目标路径/skill entry 摘要（截断 ≤300 字符）
    severity:   INFO/LOW/MEDIUM/HIGH/CRITICAL
    action:     allowed/confirmed/strong_confirmed/blocked/rejected
    decision:   yes/no/blocked/non_interactive
    rule_id:    命中规则 id 或 "evasion:<type>"
    findings:   list[str]（触发点描述）
    pid:        os.getpid()（可选，默认自动）
    """
    if pid is None:
        pid = os.getpid()
    if detail and len(detail) > 300:
        detail = detail[:300] + "\u2026"
    record = {
        "ts": _now_iso(),
        "event": event,
        "session_id": session_id,
        "tool": tool,
        "detail": detail,
        "severity": severity,
        "action": action,
        "decision": decision,
        "rule_id": rule_id,
        "findings": findings,
        "pid": pid,
    }
    if answer is not None:
        record["answer"] = answer[:80]
    line = json.dumps(record, ensure_ascii=False)
    try:
        with _lock:
            path = _audit_path()
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as e:
        print("[audit] write failed: {}".format(e), file=sys.stderr)


def recent(limit=20):
    """读当月（+上月）审计记录，倒序，返回 list[dict]。"""
    results = []
    now = datetime.now()
    months = [now.strftime("%Y-%m")]
    # 上月
    if now.month == 1:
        months.append("{}-12".format(now.year - 1))
    else:
        months.append("{}-{:02d}".format(now.year, now.month - 1))

    for m in reversed(months):
        path = _AUDIT_DIR / "audit_{}.jsonl".format(m)
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            for line in reversed(lines):
                line = line.strip()
                if line:
                    try:
                        results.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
                if len(results) >= limit:
                    break
        except OSError:
            continue
        if len(results) >= limit:
            break
    return results[:limit]
