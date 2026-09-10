from __future__ import annotations

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
context.py — 上下文自动管理（F4，叶子模块）

策略（由轻到重，逐级触发）：
1. 工具结果截断（最轻，无 LLM 成本）
2. 历史轮次摘要（有 LLM 成本，可选开关）
3. 滑动窗口兜底（最后手段）

零本地模块依赖。依赖注入 llm_chat_fn 避免 import llm。
"""
import json


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
TOOL_TRUNCATE_THRESHOLD = 2000  # tool result 超过此长度才截断
TOOL_KEEP_CHARS = 2000           # 截断后保留前 N 字符
TOOLCALL_ARG_KEEP_CHARS = 200    # 旧 tool_call 大参数保留的前 N 字符
SUMMARIZE_RATIO = 0.80          # > context_limit * 0.8 触发摘要
OVERFLOW_RATIO = 0.90           # > context_limit * 0.9 触发滑动窗口
RECENT_KEEP = 6                 # 摘要时保留最近 K 轮
SUMMARY_MAX_CHARS = 1500        # 摘要输出最大字符数
TRANSCRIPT_TOOL_MAX_CHARS = 20000  # 轨迹里单条 tool / write content 上限
KEEP_RECENT_TOKENS = 20000         # 窗口默认保留最近 token

_SUMMARY_PROMPT = """请将以下对话历史压缩为一段简短摘要（不超过{max_chars}字），要求保留：
1. 用户的原始目标/任务
2. 已完成的关键步骤
3. 重要决策
4. 涉及的文件路径
5. 未完成的事项

格式：「之前的进展：…」"""


def _estimate_tokens(messages: list) -> int:
    """
    无 LLM 成本的 token 粗估（用于摘要/截断后的重新估算）。
    启发式：字符数 / 2（中文约 1.5~2 字符/token，英文约 4，取折中偏保守）。
    另计每条消息的结构开销。
    """
    chars = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            chars += len(c)
        elif isinstance(c, list):
            chars += 200  # 多模态内容粗估
        tcs = m.get("tool_calls")
        if tcs:
            import json as _json
            chars += len(_json.dumps(tcs, ensure_ascii=False))
    return max(int(chars / 2) + 4 * len(messages), 1)


# ---------------------------------------------------------------------------
# 策略 1: 工具结果截断
# ---------------------------------------------------------------------------
def trim_tool_results(messages: list, keep_last: int = 1) -> tuple:
    """
    对非最近 keep_last 轮的 tool 消息做超长截断。

    参数：
        messages: full_messages 列表（含 system）
        keep_last: 保留最近 N 个 tool 消息不截断

    返回：
        (new_messages, saved_chars)
    """
    if not messages:
        return messages, 0

    # 找所有 tool 消息的索引
    tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    # 最近 keep_last 个不截断
    skip_set = set(tool_indices[-keep_last:] if keep_last > 0 else [])

    new_messages = [dict(m) for m in messages]  # 浅拷贝
    saved_chars = 0

    for i in tool_indices:
        if i in skip_set:
            continue
        content = new_messages[i].get("content", "")
        if isinstance(content, str) and len(content) > TOOL_TRUNCATE_THRESHOLD:
            original_len = len(content)
            truncated = content[:TOOL_KEEP_CHARS] + f"\n…(truncated {original_len - TOOL_KEEP_CHARS} chars)"
            new_messages[i]["content"] = truncated
            saved_chars += (original_len - len(truncated))

    return new_messages, saved_chars


def trim_tool_call_args(messages: list, keep_last: int = 2) -> tuple:
    """
    对较旧的 assistant.tool_calls 参数瘦身：
    write_file.content / edit_file.old_text / edit_file.new_text
    只留前 TOOLCALL_ARG_KEEP_CHARS 字符 + 长度提示。

    最近 keep_last 条带 tool_calls 的 assistant 消息不动。
    返回 (new_messages, saved_chars)
    """
    BIG_KEYS = ("content", "old_text", "new_text")
    idxs = [i for i, m in enumerate(messages)
            if m.get("role") == "assistant" and m.get("tool_calls")]
    skip = set(idxs[-keep_last:] if keep_last > 0 else [])

    new_messages = [dict(m) for m in messages]
    saved = 0
    for i in idxs:
        if i in skip:
            continue
        tcs = []
        changed = False
        for tc in new_messages[i]["tool_calls"]:
            fn = (tc.get("function") or {})
            raw = fn.get("arguments") or ""
            try:
                args = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                tcs.append(tc)
                continue
            if not isinstance(args, dict):
                tcs.append(tc)
                continue
            hit = False
            for k in BIG_KEYS:
                v = args.get(k)
                if isinstance(v, str) and len(v) > TOOLCALL_ARG_KEEP_CHARS:
                    args[k] = v[:TOOLCALL_ARG_KEEP_CHARS] + \
                        f"…(trimmed {len(v) - TOOLCALL_ARG_KEEP_CHARS} chars，需要时 read_file 重取)"
                    hit = True
            if not hit:
                tcs.append(tc)
                continue
            new_raw = json.dumps(args, ensure_ascii=False)
            saved += len(raw) - len(new_raw)
            new_tc = dict(tc)
            new_fn = dict(fn)
            new_fn["arguments"] = new_raw
            new_tc["function"] = new_fn
            tcs.append(new_tc)
            changed = True
        if changed:
            new_messages[i] = dict(new_messages[i])
            new_messages[i]["tool_calls"] = tcs
    return new_messages, saved


# ---------------------------------------------------------------------------
# 策略 2: 历史轮次摘要
# ---------------------------------------------------------------------------
def summarize_old(messages: list, llm_chat_fn, old_slice: list) -> str:
    """
    调 LLM 对 old_slice（最旧的 N 轮）生成摘要。

    参数：
        messages: 完整 messages（未使用，保留接口一致性）
        llm_chat_fn: callable(messages, ...) -> generator of chunks（依赖注入）
        old_slice: 需要被摘要的消息列表

    返回：
        摘要文本（≤ SUMMARY_MAX_CHARS 字）。失败返回空字符串。
    """
    if not llm_chat_fn or not old_slice:
        return ""

    # 构造摘要请求
    history_text = ""
    for m in old_slice:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            content = "[多模态内容]"
        if isinstance(content, str) and len(content) > 500:
            content = content[:500] + "..."
        if role == "tool":
            content = f"[tool result] {content[:200]}"
        history_text += f"{role}: {content}\n"

    prompt = _SUMMARY_PROMPT.format(max_chars=SUMMARY_MAX_CHARS)
    summarize_messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": history_text},
    ]

    try:
        result_parts = []
        for chunk in llm_chat_fn(summarize_messages):
            if chunk.get("type") == "text":
                result_parts.append(chunk["content"])
            elif chunk.get("type") == "error":
                return ""
        summary = "".join(result_parts).strip()
        if len(summary) > SUMMARY_MAX_CHARS:
            summary = summary[:SUMMARY_MAX_CHARS]
        return summary
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 策略 3: 滑动窗口兜底
# ---------------------------------------------------------------------------
def sliding_window(messages: list, recent_keep: int = RECENT_KEEP) -> tuple:
    """
    硬截断：只保留 system + 首条 user + 最近 recent_keep 轮。

    返回：(new_messages, removed_count)
    """
    if not messages:
        return messages, 0

    # 分离 system
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    if len(non_system) <= recent_keep:
        return messages, 0

    # 找首条 user
    first_user_idx = None
    for i, m in enumerate(non_system):
        if m.get("role") == "user":
            first_user_idx = i
            break

    # 最近 recent_keep 条非 system 消息
    recent = non_system[-recent_keep:]
    # 如果最近部分的第一条不是 user/assistant，向前找边界
    while recent and recent[0].get("role") == "tool":
        recent = recent[1:]

    new_messages = system_msgs
    if first_user_idx is not None:
        new_messages.append(non_system[first_user_idx])
    new_messages.extend(recent)

    removed = len(messages) - len(new_messages)
    return new_messages, removed


def _unit_start(messages, end_idx):
    """包含 end_idx 的完整单位起点：单条，或 assistant(tool_calls)+tools。"""
    if messages[end_idx].get("role") != "tool":
        return end_idx
    i = end_idx
    while i > 0 and messages[i].get("role") == "tool":
        i -= 1
    if messages[i].get("role") == "assistant" and messages[i].get("tool_calls"):
        return i
    return end_idx


def _cheap_summary(old_slice):
    bits = []
    for m in old_slice:
        role = m.get("role")
        c = m.get("content")
        if role == "user" and isinstance(c, str) and c.strip() and m.get("name") != "context_summary":
            bits.append("用户: " + c.strip().replace("\n", " ")[:200])
        elif role == "assistant" and m.get("tool_calls"):
            names = []
            for tc in m["tool_calls"]:
                fn = (tc.get("function") or {})
                names.append(fn.get("name") or "")
            bits.append("工具: " + ", ".join(n for n in names if n))
    text = "之前的进展：\n" + "\n".join(bits[:30])
    if len(text) > SUMMARY_MAX_CHARS:
        text = text[:SUMMARY_MAX_CHARS]
    return text


def window_from_transcript(transcript, system_content, keep_recent_tokens=None):
    """
    从完整轨迹派生 API 窗口。不修改 transcript。
    超出 keep_recent_tokens 的旧段变成 name=context_summary 的 user 消息。
    """
    keep = KEEP_RECENT_TOKENS if keep_recent_tokens is None else keep_recent_tokens
    system_msg = {"role": "system", "content": system_content}
    if not transcript:
        return [system_msg]

    kept_from = len(transcript)
    tokens = 0
    i = len(transcript) - 1
    while i >= 0:
        start = _unit_start(transcript, i)
        batch = transcript[start:i + 1]
        t = _estimate_tokens(batch)
        if kept_from < len(transcript) and tokens + t > keep:
            break
        tokens += t
        kept_from = start
        i = start - 1

    dropped = transcript[:kept_from]
    kept = [dict(m) for m in transcript[kept_from:]]
    window = [system_msg]
    if dropped:
        window.append({
            "role": "user",
            "name": "context_summary",
            "content": _cheap_summary(dropped),
        })
    window.extend(kept)
    return window


# ---------------------------------------------------------------------------
# 总入口
# ---------------------------------------------------------------------------
def plan(messages, used_tokens, limit, recent_keep=RECENT_KEEP) -> str:
    """
    预估将执行的动作（用于日志/预览）。
    返回描述字符串。
    """
    if not messages or limit <= 0:
        return "none"
    percent = used_tokens / limit * 100
    actions = []
    if percent > OVERFLOW_RATIO * 100:
        actions.append("sliding_window")
    elif percent > SUMMARIZE_RATIO * 100:
        actions.append("summarize_old")
    # tool 截断始终可以做（轻量）
    tool_count = len([m for m in messages if m.get("role") == "tool"])
    if tool_count > recent_keep:
        actions.append("trim_tools")
    return ",".join(actions) if actions else "none"


def apply(messages: list, used_tokens: int, limit: int,
          llm_chat_fn=None, auto_summarize: bool = True,
          recent_keep: int = RECENT_KEEP, budget: int = 0) -> tuple:
    """
    自动上下文管理入口。

    参数：
        messages: 完整 messages（含 system）
        used_tokens: 上一次 LLM 调用的 prompt_tokens（不是累计值）
        limit: context_limit
        llm_chat_fn: 可选的 LLM callable（用于摘要）
        auto_summarize: 是否启用摘要
        recent_keep: 保留最近 K 轮
        budget: 单次请求 prompt 预算，超出则加压。0 = 不启用预算档

    返回：
        (processed_messages, action_description)
    """
    if not messages or limit <= 0 or used_tokens <= 0:
        return messages, "none"

    actions = []
    over_budget = budget > 0 and used_tokens > budget

    tool_keep = 2 if over_budget else 6
    arg_keep = 1 if over_budget else 2

    messages, saved_tools = trim_tool_results(messages, keep_last=tool_keep)
    if saved_tools > 0:
        actions.append(f"trim_tools(saved {saved_tools} chars)")

    messages, saved_args = trim_tool_call_args(messages, keep_last=arg_keep)
    if saved_args > 0:
        actions.append(f"trim_args(saved {saved_args} chars)")

    if over_budget:
        actions.append(f"over_budget({used_tokens}>{budget})")

    est_tokens = _estimate_tokens(messages)

    if auto_summarize and llm_chat_fn and est_tokens / limit * 100 > SUMMARIZE_RATIO * 100:
        new_messages, summarized = _do_summarize(messages, llm_chat_fn, recent_keep)
        if new_messages is not None:
            messages = new_messages
            actions.append(f"summarize_old({summarized} rounds)")
            est_tokens = _estimate_tokens(messages)

    if est_tokens / limit * 100 > OVERFLOW_RATIO * 100:
        new_messages, removed = sliding_window(messages, recent_keep)
        if removed > 0:
            messages = new_messages
            actions.append(f"sliding_window(removed {removed} msgs)")

    return messages, (",".join(actions) if actions else "none")


def _do_summarize(messages: list, llm_chat_fn, recent_keep: int) -> tuple:
    """
    执行摘要：把最旧的 N 轮合并成摘要。
    返回 (new_messages_or_None, summarized_rounds)
    """
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    if len(non_system) <= recent_keep + 2:
        return None, 0  # 不够多，不值得摘要

    # old = 除了 system 和最近 recent_keep 条
    recent = non_system[-recent_keep:]
    # 确保 recent 从 user 或 assistant 开始
    while recent and recent[0].get("role") == "tool":
        recent = recent[1:]

    old_slice = non_system[:len(non_system) - len(recent)]
    if not old_slice:
        return None, 0

    # 调 LLM 摘要
    summary = summarize_old(messages, llm_chat_fn, old_slice)
    if not summary:
        return None, 0  # 摘要失败，降级（调用方只做 tool 截断）

    # 构建新 messages
    new_messages = list(system_msgs)
    # 首条 user 保留（任务目标）
    first_user = None
    for m in old_slice:
        if m.get("role") == "user":
            first_user = m
            break
    if first_user:
        new_messages.append(dict(first_user))

    new_messages.append({
        "role": "user",
        "name": "context_summary",
        "content": summary,
    })

    new_messages.extend(recent)

    return new_messages, len(old_slice)
