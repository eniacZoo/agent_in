#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 agent_in 的会话文件里抽出长任务对比指标。不调用模型。

用法：python tests/regression/score_session.py sessions/bc657821.json
"""
import json
import sys


def _read_path(raw):
    """同一文件换了行号也算重读。参数对不上时退回整段参数。"""
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return str(raw)
    if isinstance(obj, dict) and obj.get("path"):
        return str(obj["path"]).replace("\\", "/")
    return str(raw)


def score(path):
    with open(path, encoding="utf-8") as f:
        data = json.loads(f.read())
    messages = data.get("messages") or []
    reads = {}
    tools_n = 0
    errors = 0
    trunc = 0
    users = 0
    for m in messages:
        if m.get("role") == "user" and m.get("name") not in ("context_summary", "context_compaction"):
            if isinstance(m.get("content"), str):
                users += 1
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            tools_n += 1
            fn = tc.get("function") or {}
            raw = fn.get("arguments") or ""
            if not isinstance(raw, str):
                raw = json.dumps(raw, ensure_ascii=False)
            if fn.get("name") == "read_file":
                key = _read_path(raw)
                reads[key] = reads.get(key, 0) + 1
            if '"_raw"' in raw or "已省略" in raw:
                trunc += 1
        content = m.get("content") or ""
        if isinstance(content, str) and "达到最大工具调用轮数" in content:
            errors += 1
    reread = sum(n - 1 for n in reads.values() if n > 1)
    top = sorted(((n, p) for p, n in reads.items() if n > 1), reverse=True)[:8]
    tel = data.get("telemetry") or {}
    return {
        "session_id": data.get("session_id"),
        "model": data.get("model"),
        "user_messages": users,
        "tool_calls": tools_n,
        "reread_reads": reread,
        "top_reread": [{"path": p, "reads": n} for n, p in top],
        "suspected_truncations": trunc,
        "hit_round_cap_notes": errors,
        "telemetry": tel,
    }


def main(argv):
    if len(argv) < 2:
        print("usage: score_session.py <session.json>")
        return 2
    print(json.dumps(score(argv[1]), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
