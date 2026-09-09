#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
skill_scanner.py — S5 Skill 静态扫描 + 文件 hash + 信任记录（叶子模块）

不 import skill_manager。只收 entry 文件绝对路径。
零依赖：hashlib + json + os + re + time + pathlib
"""
import hashlib
import json
import os
import re
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# 风险模式 (regex, 说明, 严重级)
# ---------------------------------------------------------------------------
RISKY_PATTERNS = [
    (r"subprocess\.(run|call|Popen|check_output|getoutput)", "调用子进程", "HIGH"),
    (r"os\.system\s*\(", "os.system 执行", "HIGH"),
    (r"\beval\s*\(|\bexec\s*\(", "eval/exec 动态执行", "HIGH"),
    (r"import\s+(socket|requests|urllib)\b|urlopen|\.post\(|\.get\(", "网络外发", "MEDIUM"),
    (r"[~/][.](ssh|aws|gnupg|kube)|/etc/(passwd|shadow)|id_rsa|\.env\b", "读取凭据", "HIGH"),
    (r"\bDROP\s+(TABLE|DATABASE)\b|TRUNCATE\s|rm\s+-rf", "破坏性操作", "HIGH"),
    (r"base64\s+(-d|--decode)|\bx6d|zlib\.decompress", "编码载荷", "MEDIUM"),
]

SEV_RANK = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}

# 信任记录路径
_TRUST_FILE = Path(os.path.dirname(os.path.abspath(__file__))) / "skills" / ".trusted.json"


# ---------------------------------------------------------------------------
# 静态扫描
# ---------------------------------------------------------------------------
def scan_entry(entry_path):
    """
    静态扫描 entry 文件。

    entry_path: 绝对路径 (str)
    返回 {"risky": bool, "max_severity": str, "findings": list, "summary": str}
    """
    findings = []
    max_sev = "INFO"

    try:
        content = Path(entry_path).read_text(encoding="utf-8", errors="replace")
    except (OSError, IOError):
        return {
            "risky": False,
            "max_severity": "INFO",
            "findings": [],
            "summary": "(无法读取)",
        }

    # 截断防 DoS
    if len(content) > 200_000:
        content = content[:200_000]

    # 首行 docstring / 注释作为 summary
    lines = content.split("\n")
    summary = ""
    for line in lines[:10]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('"""') or stripped.startswith("'''"):
            summary = stripped.strip("\"'")[:120]
            break
        elif stripped.startswith("#"):
            summary = stripped.lstrip("#").strip()[:120]
            break
        elif not stripped.startswith(("import ", "from ")):
            summary = stripped[:120]
            break
    if not summary:
        summary = "(无描述)"

    # 逐行匹配
    for line_no, line in enumerate(content.split("\n"), 1):
        for pattern, desc, severity in RISKY_PATTERNS:
            try:
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append({
                        "line": line_no,
                        "pattern_desc": desc,
                        "severity": severity,
                    })
                    if SEV_RANK.get(severity, 0) > SEV_RANK.get(max_sev, 0):
                        max_sev = severity
            except re.error:
                continue

    return {
        "risky": len(findings) > 0,
        "max_severity": max_sev,
        "findings": findings,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# 文件 hash
# ---------------------------------------------------------------------------
def file_hash(entry_path):
    """计算文件 SHA-256。"""
    h = hashlib.sha256()
    with open(entry_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 信任记录（skills/.trusted.json）
# ---------------------------------------------------------------------------
def load_trust():
    """读 skills/.trusted.json → {name: {source, sha256, confirmed_at}}"""
    if not _TRUST_FILE.exists():
        return {}
    try:
        with open(_TRUST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_trust(name, source, sha256):
    """原子写入信任记录。"""
    trust = load_trust()
    trust[name] = {
        "source": source,
        "sha256": sha256,
        "confirmed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _TRUST_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _TRUST_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(trust, f, ensure_ascii=False, indent=2)
    tmp.replace(_TRUST_FILE)


def verify_trust(name, sha256):
    """
    校验信任记录。

    返回 (status, detail):
      ("trusted",   name) → hash 一致
      ("untrusted", name) → 未记录
      ("tampered",  name) → hash 不匹配（防篡改）
    """
    trust = load_trust()
    record = trust.get(name)
    if record is None:
        return ("untrusted", name)
    if record.get("sha256") != sha256:
        return ("tampered", name)
    return ("trusted", name)
