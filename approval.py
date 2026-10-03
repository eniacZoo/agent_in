#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
approval.py — 人工审批流（4 级，confirm_fn/input_fn 依赖注入，不 import ui）

ALLOW          → 静默放行（不审计）
CONFIRM        → y/N
STRONG_CONFIRM → y/N + 键入 token
BLOCK          → 直接拒绝（不给人机会）

零依赖：stdlib only（只 import audit）
"""
import audit


# ---------------------------------------------------------------------------
# 提示构建
# ---------------------------------------------------------------------------
_SEV_LABELS = {
    "INFO": "信息", "LOW": "低危", "MEDIUM": "中危",
    "HIGH": "高危", "CRITICAL": "严重",
}


def _prompt(verdict, ctx):
    """构建可读审批提示。"""
    sev = verdict.get("severity", "?")
    sev_label = _SEV_LABELS.get(sev, sev)
    reason = verdict.get("reason", "")
    detail = verdict.get("detail", "")
    source = ctx.get("source", "")

    lines = ["[!] [{}] {}".format(sev_label, reason)]
    if detail:
        if source == "shell":
            lines.append("   命令: {}".format(detail))
        elif source in ("file_write", "file_edit"):
            lines.append("   路径: {}".format(detail))
        else:
            lines.append("   目标: {}".format(detail))

    prompt = "\n".join(lines)
    return prompt


# ---------------------------------------------------------------------------
# 事件类型映射
# ---------------------------------------------------------------------------
_EVENT_MAP = {
    "shell": "shell",
    "file_write": "file_write",
    "file_edit": "file_edit",
    "skill": "skill_exec",
}


# ---------------------------------------------------------------------------
# 审批主入口
# ---------------------------------------------------------------------------
def resolve(verdict, confirm_fn, input_fn=None, ctx=None):
    """
    统一审批入口。

    verdict:    Verdict dict（tool_guard / skill_scanner 产出）
    confirm_fn: (prompt: str) -> bool，依赖注入
    input_fn:   () -> str，依赖注入（强确认键入 token 用）
    ctx:        {"session_id", "tool_name"/"tool", "source", "rule_id"}

    返回 Decision: {"approved": bool, "action": str, "reason": str}

    副作用：MEDIUM+ 或 REJECT/BLOCK → audit.log_security(...)
    """
    if ctx is None:
        ctx = {}

    action    = verdict.get("action", "allow")
    severity  = verdict.get("severity", "INFO")
    detail    = verdict.get("detail", "")
    rule_id   = ctx.get("rule_id", "")
    findings  = verdict.get("findings", [])
    session_id = ctx.get("session_id", "-")
    tool      = ctx.get("tool", "?")
    source    = ctx.get("source", "")

    event = _EVENT_MAP.get(source, "tool")

    # 确定 rule_id 兜底
    if not rule_id:
        if findings:
            rule_id = findings[0]
        else:
            rule_id = "severity:{}".format(severity)

    # ---------------------------------------------------------------
    # ALLOW → 静默放行，不审计
    # ---------------------------------------------------------------
    if action == "allow":
        return {"approved": True, "action": "allow", "reason": ""}

    # ---------------------------------------------------------------
    # BLOCK → 直接拒绝
    # ---------------------------------------------------------------
    if action == "block":
        audit.log_security(
            event=event, session_id=session_id, tool=tool,
            detail=detail, severity=severity,
            action="blocked", decision="blocked",
            rule_id=rule_id, findings=findings,
        )
        base = "严重级操作已阻断（SAFE 策略）"
        extra = (verdict.get("reason") or "").strip()
        return {
            "approved": False,
            "action": "blocked",
            "reason": (base + "：" + extra) if extra else base,
        }

    # ---------------------------------------------------------------
    # CONFIRM → y/N
    # ---------------------------------------------------------------
    if action == "confirm":
        if confirm_fn is None:
            audit.log_security(
                event=event, session_id=session_id, tool=tool,
                detail=detail, severity=severity,
                action="rejected", decision="non_interactive",
                rule_id=rule_id, findings=findings,
            )
            return {
                "approved": False, "action": "rejected",
                "reason": "非交互模式，确认类操作被拒绝",
            }

        prompt = _prompt(verdict, ctx)
        ok = confirm_fn(prompt)
        raw = getattr(confirm_fn, "last_answer", None)
        if ok:
            audit.log_security(
                event=event, session_id=session_id, tool=tool,
                detail=detail, severity=severity,
                action="confirmed", decision="yes",
                rule_id=rule_id, findings=findings,
                answer=raw,
            )
            return {"approved": True, "action": "confirmed", "reason": ""}
        else:
            audit.log_security(
                event=event, session_id=session_id, tool=tool,
                detail=detail, severity=severity,
                action="rejected", decision="no",
                rule_id=rule_id, findings=findings,
                answer=raw,
            )
            return {
                "approved": False, "action": "rejected",
                "reason": "用户拒绝",
            }

    # ---------------------------------------------------------------
    # STRONG_CONFIRM → y/N + 键入 token
    # ---------------------------------------------------------------
    if action == "strong_confirm":
        if confirm_fn is None:
            audit.log_security(
                event=event, session_id=session_id, tool=tool,
                detail=detail, severity=severity,
                action="rejected", decision="non_interactive",
                rule_id=rule_id, findings=findings,
            )
            return {
                "approved": False, "action": "rejected",
                "reason": "非交互模式，强确认操作被拒绝",
            }

        # 第一轮: y/N
        prompt = _prompt(verdict, ctx)
        ok = confirm_fn(prompt)
        raw = getattr(confirm_fn, "last_answer", None)
        if not ok:
            audit.log_security(
                event=event, session_id=session_id, tool=tool,
                detail=detail, severity=severity,
                action="rejected", decision="no",
                rule_id=rule_id, findings=findings,
                answer=raw,
            )
            return {
                "approved": False, "action": "rejected",
                "reason": "用户拒绝",
            }

        # 第二轮: 键入 token
        if input_fn is None:
            audit.log_security(
                event=event, session_id=session_id, tool=tool,
                detail=detail, severity=severity,
                action="rejected", decision="non_interactive",
                rule_id=rule_id, findings=findings,
            )
            return {
                "approved": False, "action": "rejected",
                "reason": "无输入通道，强确认操作被拒绝",
            }

        try:
            if source == "shell":
                label = "输入完整命令以确认"
            else:
                label = "输入完整路径以确认"
            token = input_fn("  [!] {}: {}".format(label, detail))
        except (EOFError, KeyboardInterrupt):
            print()
            audit.log_security(
                event=event, session_id=session_id, tool=tool,
                detail=detail, severity=severity,
                action="rejected", decision="non_interactive",
                rule_id=rule_id, findings=findings,
            )
            return {
                "approved": False, "action": "rejected",
                "reason": "输入中断，强确认操作被拒绝",
            }

        if token.strip() == detail.strip():
            audit.log_security(
                event=event, session_id=session_id, tool=tool,
                detail=detail, severity=severity,
                action="strong_confirmed", decision="yes",
                rule_id=rule_id, findings=findings,
            )
            return {
                "approved": True, "action": "strong_confirmed", "reason": "",
            }
        else:
            audit.log_security(
                event=event, session_id=session_id, tool=tool,
                detail=detail, severity=severity,
                action="rejected", decision="no",
                rule_id=rule_id, findings=findings,
            )
            return {
                "approved": False, "action": "rejected",
                "reason": "token 不匹配",
            }

    # ---------------------------------------------------------------
    # 未知 action → 安全默认拒绝
    # ---------------------------------------------------------------
    return {
        "approved": False,
        "action": "rejected",
        "reason": "未知审批动作: {}".format(action),
    }
