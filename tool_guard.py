#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tool_guard.py — S3 Shell 逃逸检测 + S4 文件守卫（纯函数式判定，叶子模块）

不 import 任何业务模块（tools / ui / logger / skill_manager）。
只收字符串/路径参数 + work_dir + safe_mode，返回 Verdict dict。

Verdict 结构: {"action", "severity", "findings", "reason", "detail"}

零依赖：json + os + re + pathlib + functools
"""
import json
import os
import re
from functools import lru_cache
from pathlib import Path


# ---------------------------------------------------------------------------
# 严重级 / 动作
# ---------------------------------------------------------------------------
SEV_RANK = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}

ACTION_ALLOW         = "allow"
ACTION_CONFIRM       = "confirm"
ACTION_STRONG_CONFIRM = "strong_confirm"
ACTION_BLOCK         = "block"

_DELETE_RULE_IDS = frozenset({
    "ps_rm_recurse", "rm_rf", "rm_r", "del_s", "rd_s",
})
_CMD_WORDS = frozenset({
    "remove-item", "ri", "del", "erase", "rm", "rmdir", "rd",
    "cmd", "powershell", "pwsh", "/c", "/s",
})

_PROJECT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# 规则加载（lru_cache 缓存，不读全局 WORK_DIR / SAFE_MODE）
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def load_shell_rules():
    """读 rules/dangerous_shell.json，缺失 → 内置兜底最小集。"""
    path = _PROJECT_DIR / "rules" / "dangerous_shell.json"
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    # 兜底（rules 缺失时仍覆盖 Windows 高危删除）
    return {
        "shell": {
            "rules": [
                {"id": "format_volume", "pattern": r"(?i)\bFormat-Volume\b",
                 "severity": "CRITICAL", "desc": "格式化磁盘"},
                {"id": "ps_rm_recurse", "pattern": r"(?i)(Remove-Item|\bri\b)[^\n|;]*-(Recurse|Force)",
                 "severity": "HIGH", "desc": "PowerShell 递归/强制删除"},
                {"id": "del_s", "pattern": r"(?i)\b(del|erase)\s+/[sS]",
                 "severity": "HIGH", "desc": "cmd 递归删除"},
                {"id": "rm_rf", "pattern": r"\brm\s+(-[a-z]*[rR][a-z]*\s+)?-?[a-z]*[fF]",
                 "severity": "HIGH", "desc": "递归强制删除"},
                {"id": "mkfs", "pattern": r"\bmkfs", "severity": "CRITICAL", "desc": "格式化文件系统"},
                {"id": "dd_dev", "pattern": r"\bdd\b[^|;&]*of=/?dev/", "severity": "CRITICAL", "desc": "dd 写裸设备"},
                {"id": "pkg_install", "pattern": r"(?i)(\bpip3?\s+install\b|\bpython(?:\d+(?:\.\d+)*)?\s+-m\s+pip\s+install\b|\bconda\s+install\b|\bnpm\s+install\b)",
                 "severity": "HIGH", "desc": "离线环境请用 vendor，禁止 pip/conda/npm install"},
            ],
            "evasion": {"piped_download_exec": "CRITICAL"},
        }
    }


@lru_cache(maxsize=1)
def load_sensitive_paths():
    """读 rules/sensitive_paths.json，缺失 → 内置兜底。"""
    path = _PROJECT_DIR / "rules" / "sensitive_paths.json"
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "protected_roots": ["~/.ssh", "~/.aws", "C:\\Windows", "C:\\Windows\\System32"],
        "critical_files": ["/etc/passwd", "/etc/shadow"],
        "sensitive_name_patterns": [r"\.env$", "id_rsa"],
    }


# ---------------------------------------------------------------------------
# Verdict helper
# ---------------------------------------------------------------------------
def _verdict(severity, action, findings, reason, detail=""):
    return {
        "severity": severity,
        "action": action,
        "findings": findings,
        "reason": reason,
        "detail": detail,
    }


def _action_for(severity, safe_mode):
    """严重级 → 动作基础映射（SAFE_MODE 升级 HIGH → BLOCK）。"""
    if severity == "CRITICAL":
        return ACTION_BLOCK
    if severity == "HIGH":
        return ACTION_BLOCK if safe_mode else ACTION_STRONG_CONFIRM
    if severity == "MEDIUM":
        return ACTION_CONFIRM
    return ACTION_ALLOW


# ---------------------------------------------------------------------------
# 编码解码（逃逸检测辅助）
# ---------------------------------------------------------------------------
def _norm_escapes(cmd):
    """解码 \\xNN / \\uNNNN / URL 编码(%xx) → 还原真实命令。"""
    result = cmd
    # \xNN hex escape
    result = re.sub(
        r"\\x([0-9a-fA-F]{2})",
        lambda m: chr(int(m.group(1), 16)),
        result,
    )
    # \uNNNN unicode escape
    result = re.sub(
        r"\\u([0-9a-fA-F]{4})",
        lambda m: chr(int(m.group(1), 16)),
        result,
    )
    # URL 编码：%xx 连续 3 个以上才解码
    def _url_decode(m):
        hex_str = m.group(0)
        out = []
        i = 0
        while i < len(hex_str):
            if hex_str[i] == "%" and i + 2 < len(hex_str):
                try:
                    out.append(chr(int(hex_str[i + 1:i + 3], 16)))
                    i += 3
                except ValueError:
                    out.append(hex_str[i])
                    i += 1
            else:
                out.append(hex_str[i])
                i += 1
        return "".join(out)
    result = re.sub(r"(%[0-9a-fA-F]{2}){3,}", _url_decode, result)
    return result


# ---------------------------------------------------------------------------
# 引号感知
# ---------------------------------------------------------------------------
def _get_quoted_spans(cmd):
    """获取引号区域 span 列表 [(start, end), ...]。"""
    spans = []
    i = 0
    in_single = False
    in_double = False
    span_start = 0
    while i < len(cmd):
        ch = cmd[i]
        if ch == "'" and not in_double:
            if in_single:
                in_single = False
                spans.append((span_start, i + 1))
            else:
                in_single = True
                span_start = i
        elif ch == '"' and not in_single:
            if in_double:
                in_double = False
                spans.append((span_start, i + 1))
            else:
                in_double = True
                span_start = i
        i += 1
    return spans


def _is_inside_quotes(pos, quoted_spans):
    """判断位置 pos 是否在引号范围内。"""
    for start, end in quoted_spans:
        if start < pos < end:
            return True
    return False


# ---------------------------------------------------------------------------
# 逃逸检测
# ---------------------------------------------------------------------------
def _find_evasions(cmd, rules_data):
    """
    检测 shell 逃逸/混淆技术。
    返回 [{type, severity, snippet}]
    """
    findings = []
    evasion_cfg = rules_data.get("shell", {}).get("evasion", {})
    quoted_spans = _get_quoted_spans(cmd)

    # 1) 命令替换: $(...) 或 `...`（非引号内）
    for m in re.finditer(r"\$\(([^)]*)\)", cmd):
        if not _is_inside_quotes(m.start(), quoted_spans):
            findings.append({
                "type": "command_substitution",
                "severity": evasion_cfg.get("command_substitution", "HIGH"),
                "snippet": m.group(0)[:80],
            })

    for m in re.finditer(r"`([^`]*)`", cmd):
        if not _is_inside_quotes(m.start(), quoted_spans):
            findings.append({
                "type": "command_substitution",
                "severity": evasion_cfg.get("command_substitution", "HIGH"),
                "snippet": m.group(0)[:80],
            })

    # 2) 管道下载执行: (curl|wget|...) ... | (sh|bash|python|powershell)
    piped_pat = (r"(curl|wget|fetch|Invoke-WebRequest|iwr|iex)\b"
                 r".*\|\s*(sh|bash|python[0-9.]*|powershell|pwsh)\b")
    for m in re.finditer(piped_pat, cmd, re.IGNORECASE):
        findings.append({
            "type": "piped_download_exec",
            "severity": evasion_cfg.get("piped_download_exec", "CRITICAL"),
            "snippet": m.group(0)[:80],
        })

    # 3) base64 解码执行
    b64_pat = r"base64\s+(-d|--decode).*\|\s*(sh|bash|python[0-9.]*|powershell)\b"
    for m in re.finditer(b64_pat, cmd, re.IGNORECASE):
        findings.append({
            "type": "base64_decode_exec",
            "severity": evasion_cfg.get("base64_decode_exec", "HIGH"),
            "snippet": m.group(0)[:80],
        })
    # echo <b64> | base64 -d | sh
    b64_echo_pat = (r"echo\s+[\w+/=]{10,}\s*\|\s*"
                    r"base64\s+(-d|--decode).*\|\s*(sh|bash)\b")
    for m in re.finditer(b64_echo_pat, cmd, re.IGNORECASE):
        snippet = m.group(0)[:80]
        if not any(f["snippet"] == snippet for f in findings):
            findings.append({
                "type": "base64_decode_exec",
                "severity": evasion_cfg.get("base64_decode_exec", "HIGH"),
                "snippet": snippet,
            })

    # 4) 变量拆分: A=xxx; $A -rf /
    var_pat = r"^\s*([A-Za-z_][A-Za-z_0-9]{0,3})=(\S+)\s*;\s*\$\1\s+"
    for m in re.finditer(var_pat, cmd):
        findings.append({
            "type": "var_split_assign",
            "severity": evasion_cfg.get("var_split_assign", "HIGH"),
            "snippet": m.group(0)[:80],
        })

    # 5) unicode / hex escape
    hex_esc = re.search(r"\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}", cmd)
    if hex_esc:
        findings.append({
            "type": "unicode_hex_escape",
            "severity": evasion_cfg.get("unicode_hex_escape", "MEDIUM"),
            "snippet": hex_esc.group(0),
        })

    # 6) URL 编码载荷: 3+ 连续 %xx
    url_enc = re.search(r"(%[0-9a-fA-F]{2}){3,}", cmd)
    if url_enc:
        findings.append({
            "type": "url_encoded_payload",
            "severity": evasion_cfg.get("url_encoded_payload", "MEDIUM"),
            "snippet": url_enc.group(0)[:80],
        })

    return findings


# ---------------------------------------------------------------------------
# S3: Shell 评估
# ---------------------------------------------------------------------------
def assess_shell(command, safe_mode=False):
    """
    评估 shell 命令安全性 → Verdict。
    """
    rules_data = load_shell_rules()
    rules = rules_data.get("shell", {}).get("rules", [])

    # 1) 逃逸检测
    evasion_findings = _find_evasions(command, rules_data)

    # 2) 解码后匹配规则
    norm = _norm_escapes(command)

    rule_findings = []
    for rule in rules:
        try:
            if re.search(rule["pattern"], norm, re.IGNORECASE):
                rule_findings.append({
                    "id": rule["id"],
                    "severity": rule["severity"],
                    "desc": rule.get("desc", ""),
                })
        except re.error:
            continue

    # 3) 合并
    all_findings = []
    max_sev = "INFO"

    for f in evasion_findings:
        all_findings.append("evasion:{}".format(f["type"]))
        if SEV_RANK.get(f["severity"], 0) > SEV_RANK.get(max_sev, 0):
            max_sev = f["severity"]

    for f in rule_findings:
        all_findings.append("rule:{}".format(f["id"]))
        if SEV_RANK.get(f["severity"], 0) > SEV_RANK.get(max_sev, 0):
            max_sev = f["severity"]

    if not all_findings:
        return _verdict("INFO", ACTION_ALLOW, [], "no match", command)

    action = _action_for(max_sev, safe_mode)

    parts = []
    for f in evasion_findings:
        parts.append("检出逃逸:{}".format(f["type"]))
    for f in rule_findings:
        parts.append("命中规则 {}（{}）".format(f["id"], f["desc"]))
    reason = "；".join(parts)

    return _verdict(max_sev, action, all_findings, reason, command)


def _extract_cmd_paths(cmd):
    """从 shell 命令里抽出可能的路径（引号内 + 非选项 token）。"""
    paths = []
    for m in re.finditer(r'"([^"]+)"|\'([^\']+)\'', cmd):
        p = m.group(1) or m.group(2)
        if p:
            paths.append(p)
    stripped = re.sub(r'"[^"]*"|\'[^\']*\'', " ", cmd)
    for tok in stripped.split():
        low = tok.lower().strip()
        if not low or low.startswith("-") or low in _CMD_WORDS:
            continue
        if low in ("&&", "||", ";", "|", "&"):
            continue
        if low.startswith("http://") or low.startswith("https://"):
            continue
        paths.append(tok)
    return paths


def _is_under_temp(path_str, work_dir):
    """路径 resolve 后是否位于 {work_dir}/temp（含自身）。"""
    if not path_str or not work_dir:
        return False
    wd = Path(work_dir).expanduser().resolve()
    temp = (wd / "temp").resolve()
    p = Path(path_str).expanduser()
    if not p.is_absolute():
        p = wd / p
    try:
        p = p.resolve()
        p.relative_to(temp)
        return True
    except (OSError, ValueError):
        return False


def is_under_temp(path_str, work_dir):
    """路径是否位于 {work_dir}/temp（含自身）。"""
    return _is_under_temp(path_str, work_dir)


def _allow_temp_deletes(verdict, command, work_dir):
    """删除类规则：目标全部在 work_dir/temp 下则放行（其它命中仍保留）。"""
    findings = list(verdict.get("findings") or [])
    delete_hits = []
    for f in findings:
        if f.startswith("rule:") and f.split(":", 1)[1] in _DELETE_RULE_IDS:
            delete_hits.append(f)
    if not delete_hits:
        return verdict
    paths = _extract_cmd_paths(command)
    if not paths or not all(_is_under_temp(p, work_dir) for p in paths):
        return verdict
    kept = [f for f in findings if f not in delete_hits]
    if kept:
        return verdict
    return _verdict("INFO", ACTION_ALLOW, [], "temp dir delete allowed", command)


# ---------------------------------------------------------------------------
# S4: 文件写入评估
# ---------------------------------------------------------------------------
def _resolve_safe(p_str):
    """expanduser + resolve（解析符号链接），异常时返回原路径。"""
    p = Path(p_str).expanduser()
    try:
        return p.resolve()
    except OSError:
        return p


def _is_critical_file(p, rules_data):
    """检查是否为系统关键文件。"""
    critical_files = rules_data.get("critical_files", [])
    for cf in critical_files:
        cf_path = _resolve_safe(cf)
        if p == cf_path:
            return True
    return False


def _is_sensitive(p, rules_data):
    """检查是否为敏感路径（受保护根 或 文件名模式）。"""
    # 1) 受保护根目录
    for root in rules_data.get("protected_roots", []):
        root_path = _resolve_safe(root)
        try:
            p.relative_to(root_path)
            return True
        except (ValueError, OSError):
            continue
    # 2) 文件名模式
    for pat in rules_data.get("sensitive_name_patterns", []):
        try:
            if re.search(pat, p.name):
                return True
        except re.error:
            continue
    return False


def _within(p, work_dir_path):
    """检查 p 是否在 work_dir_path 内（均已 resolve）。"""
    try:
        p.relative_to(work_dir_path)
        return True
    except ValueError:
        return False


def assess_write(path, work_dir, safe_mode=False):
    """
    评估文件写入安全性 → Verdict。
    path:     目标文件路径 (str)
    work_dir: 工作目录 (str)
    safe_mode: SAFE_MODE 开关
    """
    rules_data = load_sensitive_paths()
    p = _resolve_safe(path)
    wd = _resolve_safe(work_dir)
    findings = []

    # 1) 系统关键文件 → CRITICAL（恒 BLOCK）
    if _is_critical_file(p, rules_data):
        findings.append("critical_file")
        return _verdict("CRITICAL", ACTION_BLOCK, findings, "系统关键文件", str(p))

    # 2) 敏感路径（受保护根 或 文件名模式）
    sensitive = _is_sensitive(p, rules_data)
    if sensitive:
        findings.append("sensitive_path")
        sev = "HIGH"
    elif _within(p, wd):
        # 3) 工作目录内 → 放行
        return _verdict("INFO", ACTION_ALLOW, [], "within work_dir", str(p))
    else:
        # 4) 出界：桌面报告等用户点名的路径走确认，SAFE_MODE 不再直接 BLOCK
        sev = "MEDIUM"
        findings.append("outside_workdir")

    action = _action_for(sev, safe_mode)

    why_parts = []
    if sensitive:
        why_parts.append("敏感路径")
    if "outside_workdir" in findings:
        why_parts.append("工作目录外")
    reason = "，".join(why_parts) if why_parts else "unknown"

    return _verdict(sev, action, findings, reason, str(p))


def assess_overwrite(path):
    """已存在的文件将被覆盖 → MEDIUM CONFIRM；新文件 ALLOW。"""
    p = _resolve_safe(path)
    try:
        exists = p.exists() and p.is_file()
    except OSError:
        exists = False
    if exists:
        return _verdict("MEDIUM", ACTION_CONFIRM, ["overwrite"], "覆盖已有文件", str(p))
    return _verdict("INFO", ACTION_ALLOW, [], "new file", str(p))


def assess_multi_replace(count, path):
    """edit_file 命中多处 → MEDIUM CONFIRM。"""
    if count > 1:
        return _verdict(
            "MEDIUM", ACTION_CONFIRM, ["multi_replace"],
            "将替换 {} 处".format(count), str(path),
        )
    return _verdict("INFO", ACTION_ALLOW, [], "single replace", str(path))


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------
def assess_call(name, args, work_dir, safe_mode=False):
    """
    统一安全评估入口（execute 网关调用）。

    name:     tool_name (str)
    args:     tool_args (dict)
    work_dir: 工作目录 (str)
    safe_mode: SAFE_MODE (bool)

    返回 Verdict。skill_* / read_file / view_image → ALLOW。
    """
    if name == "shell":
        command = args.get("command", "") if isinstance(args, dict) else ""
        v = assess_shell(command, safe_mode)
        return _allow_temp_deletes(v, command, work_dir)

    if name in ("write_file", "edit_file"):
        path = args.get("path", "") if isinstance(args, dict) else ""
        return assess_write(path, work_dir, safe_mode)

    # skill_* / read_file / view_image / glob / grep → ALLOW
    return _verdict("INFO", ACTION_ALLOW, [], "no guard needed", "")
