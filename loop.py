#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
loop.py — Agent 核心循环（对应 PI 的 agent-core）

职责：
  发消息给 LLM → 执行 tool_call → 回填结果，直到最终文本。
  含上下文预警、能力降级接线、Ctrl-C 中止本轮。

依赖方向（plan8.0）：
  loop → llm, tools, ui, context, memory_manager, vision, logger, config, session
  不 import commands / agent。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import llm
import ui
import tools
import vision
import memory_manager
import logger
import config
import context
import session


# ---------------------------------------------------------------------------
# 配置（main 在 config.load 之后可覆盖 CONTEXT_LIMIT / MAX_TOOL_ITERATIONS）
# ---------------------------------------------------------------------------
SHOW_REASONING = os.environ.get("SHOW_REASONING", "1") in ("1", "true", "yes")
STREAM_CODE = ui.STREAM_CODE
MAX_TOOL_ITERATIONS = 20
CONTEXT_LIMIT = 196_000
LAST_TOOL_ROUNDS = 0       # 最近一次 agent_loop 的工具轮次（/status）
LAST_PROMPT_TOKENS = 0     # 最近一次 LLM 请求的 prompt tokens（上下文占用）
DEFAULT_SYSTEM_PROMPT = """你是一个极简 CLI Agent，运行在用户的本地机器上。
可用工具：read_file、write_file、edit_file、shell、view_image。

规则：
1. 简洁，直接执行，不要寒暄
2. 先读后改：改文件前先 read_file
3. 路径相对于工作目录：{work_dir}
4. Windows 用 dir / Get-ChildItem；不要用 shell 做删除，除非用户明确要求。临时文件只写 {work_dir}/temp/，该目录可删
5. 用户提到图片时先 view_image
6. 完成后用 1-2 句话总结
7. 读 pptx/xlsx/docx/pdf 先 read_file skills/读pptx.md（或读xlsx.md / 读docx.md / 读pdf.md）。读网页先 skills/读网页.md。包在 vendor/，shell 已带 PYTHONPATH。禁止 pip/npm/conda install。不要虚构 skill 工具。周报见 skills/周报转docx.md"""


# ---------------------------------------------------------------------------
# 颜色
# ---------------------------------------------------------------------------
_C_GRAY = ui.C_GRAY
_C_GREEN = ui.C_GREEN
_C_CYAN = ui.C_CYAN
_C_YELLOW = ui.C_YELLOW
_C_RED = ui.C_RED


def _c(text, color):
    return f"{color}{text}{ui.C_RESET}"


# ---------------------------------------------------------------------------
# 上下文监控
# ---------------------------------------------------------------------------
_context_warned_70 = False
_context_warned_90 = False


def reset_context_warnings():
    """新会话开始时清 70%/90% 预警旗标。"""
    global _context_warned_70, _context_warned_90
    _context_warned_70 = False
    _context_warned_90 = False


def _check_context(used_tokens):
    """检查上下文占用，>70% 黄色提醒，>90% 红色提醒。"""
    global _context_warned_70, _context_warned_90
    percent = used_tokens / CONTEXT_LIMIT * 100

    if percent > 90 and not _context_warned_90:
        _context_warned_90 = True
        _context_warned_70 = True
        logger.error("context_overflow", {"used_tokens": used_tokens, "limit": CONTEXT_LIMIT, "percent": int(percent)})
        _ctx_overflow_msg = f'{ui.ICO_ERR} 上下文即将用尽（{percent:.0f}%，{ui.fmt_tokens(used_tokens)}/{ui.fmt_tokens(CONTEXT_LIMIT)}），建议 /new'
        print(f"    {ui._color(_ctx_overflow_msg, ui.C_RED)}")
    elif percent > 70 and not _context_warned_70:
        _context_warned_70 = True
        logger.warn("context_warning", {"used_tokens": used_tokens, "limit": CONTEXT_LIMIT, "percent": int(percent)})
        _ctx_warn_msg = f'{ui.ICO_WARN} 上下文已用 {percent:.0f}%（{ui.fmt_tokens(used_tokens)}/{ui.fmt_tokens(CONTEXT_LIMIT)}）'
        print(f"    {ui._color(_ctx_warn_msg, ui.C_YELLOW)}")


# ---------------------------------------------------------------------------
# H4: 能力降级接线
# ---------------------------------------------------------------------------
_TOOL_CALL_DEGRADE_HINT = (
    "\n\n## 重要：当前模型可能不支持原生 function calling\n"
    "若你无法直接发起工具调用，请改用如下文本协议表达工具意图，我会解析执行：\n"
    "TOOL: read_file {\"path\": \"...\"}\n"
    "TOOL: write_file {\"path\": \"...\", \"content\": \"...\"}\n"
    "TOOL: edit_file {\"path\": \"...\", \"old\": \"...\", \"new\": \"...\"}\n"
    "TOOL: shell {\"command\": \"...\"}\n"
    "完成所有工具后直接输出最终回答。"
)


def apply_capability(cap):
    """
    用探测到的能力结果做优雅降级（mutate 本模块全局）。

    参数：
        cap: capability.probe 的 dict，或 None
    返回：
        None
    副作用：
        可能改 CONTEXT_LIMIT、DEFAULT_SYSTEM_PROMPT。
    """
    global CONTEXT_LIMIT, DEFAULT_SYSTEM_PROMPT
    if not isinstance(cap, dict):
        return
    mc = cap.get("max_context")
    if isinstance(mc, int) and mc > 0:
        CONTEXT_LIMIT = mc
        logger.info("capability_context_overridden", {"max_context": mc})
    if cap.get("tool_call") is False and _TOOL_CALL_DEGRADE_HINT not in DEFAULT_SYSTEM_PROMPT:
        DEFAULT_SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT + _TOOL_CALL_DEGRADE_HINT
        logger.warn("capability_toolcall_degraded", {})


def print_capability(cap):
    """展示能力探测结果（/probe、/provider 用）。"""
    cap = cap or {}
    tc = cap.get("tool_call")
    vi = cap.get("vision")
    mc = cap.get("max_context")
    tc_s = "yes" if tc else ("no" if tc is False else "?")
    vi_s = "yes" if vi else ("no" if vi is False else "?")
    mc_s = str(mc) if mc else "-"
    model = llm.MODEL
    print(f"\n   模型 {_c(model, _C_CYAN)}：tool_call {tc_s}   vision {vi_s}   max_context {mc_s}")
    print()


# ---------------------------------------------------------------------------
# Agent 核心循环
# ---------------------------------------------------------------------------
def agent_loop(messages, work_dir, session_id="", verbose=True, initial_images=None, tracker=None,
               prev_prompt=0, prev_completion=0, interactive=True):
    """
    发送 messages 给 LLM，若有 tool_call 则执行，直到最终文本。

    参数：
        messages: 不含 system 的对话（本函数会前置 system）
        work_dir: 工作目录，写入 system prompt
        session_id: 审计与增量保存用
        verbose: 是否打印流式/工具摘要
        initial_images: 首轮注入的 data URL 列表
        tracker: usage.Tracker 或 None
        prev_prompt / prev_completion: 恢复会话时的累计 token
        interactive: False 时需确认的操作直接拒绝
    返回：
        (最终回复文本, prompt tokens, completion tokens, 耗时秒, full_messages)
        full_messages 含 system。Ctrl-C 中止时文本为「[已中止]」。
    """
    global LAST_PROMPT_TOKENS
    system_prompt = os.environ.get("SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT.format(work_dir=work_dir))

    memory_content, mem_chars = memory_manager.load()
    if memory_content.strip() and memory_content.strip() != "# Agent Memory":
        system_prompt += "\n\n## 长期记忆（跨会话）\n" + memory_content
        logger.info("memory_loaded", {"chars": mem_chars})

    full_messages = [{"role": "system", "content": system_prompt}] + messages

    total_prompt = 0
    total_completion = 0
    total_elapsed = 0.0
    tool_iterations = 0
    final_text = ""
    confirm_fn = ui.confirm if interactive else (lambda _p: False)
    input_fn = ui.text_input if interactive else None

    def _done(text):
        global LAST_TOOL_ROUNDS
        LAST_TOOL_ROUNDS = tool_iterations
        return text, total_prompt, total_completion, total_elapsed, full_messages

    while tool_iterations <= MAX_TOOL_ITERATIONS:
        t0 = time.time()

        pending_imgs = []
        if initial_images:
            pending_imgs = list(initial_images)
            initial_images = None
        pending_imgs.extend(tools.drain_pending_images())

        if verbose and tool_iterations > 0:
            ui.turn_sep(f"tool round {tool_iterations}")

        disp = ui.StreamDisplay(show_reasoning=SHOW_REASONING)
        text_parts = []
        tool_calls = []

        if tool_iterations > 0 and total_prompt > 0:
            full_messages, ctx_action = context.apply(
                full_messages, total_prompt, CONTEXT_LIMIT,
                llm_chat_fn=llm.chat,
                auto_summarize=config.get("auto_summarize", True),
            )
            if ctx_action != "none":
                logger.info("context_manage", {"action": ctx_action, "used_tokens": total_prompt})
                if verbose:
                    print(f"    {_c(f'{ui.ICO_FILE} 上下文管理: {ctx_action}', _C_GRAY)}")

        try:
            for chunk in llm.chat(full_messages, tools=tools.TOOLS, images=pending_imgs or None):
                ctype = chunk["type"]

                if ctype in ("reasoning", "text"):
                    disp.handle(chunk)
                    if ctype == "text":
                        text_parts.append(chunk["content"])
                elif ctype == "tool_call":
                    tool_calls.append(chunk)
                    disp.handle(chunk)
                elif ctype == "usage":
                    u = chunk["data"]
                    total_prompt += u.get("prompt_tokens", 0)
                    total_completion += u.get("completion_tokens", 0)
                    if tracker:
                        tracker.add(llm.MODEL, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
                    LAST_PROMPT_TOKENS = u.get("prompt_tokens", 0)
                    _check_context(LAST_PROMPT_TOKENS)
                elif ctype == "error":
                    disp.handle(chunk)
                    if verbose:
                        ui.turn_footer(None, time.time() - t0)
                    return _done("")
        except KeyboardInterrupt:
            disp.finish()
            if verbose:
                print()
                print(f"    {_c('[已中止本轮，会话保留]', _C_YELLOW)}")
            logger.warn("turn_aborted", {"session_id": session_id, "phase": "llm"})
            return _done("[已中止]")

        disp.finish()
        elapsed = time.time() - t0
        total_elapsed += elapsed
        current_text = "".join(text_parts).strip()

        if not tool_calls:
            final_text = current_text
            break

        tool_iterations += 1

        assistant_msg = {"role": "assistant", "content": current_text or None}
        assistant_msg["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                },
            }
            for tc in tool_calls
        ]
        full_messages.append(assistant_msg)

        for tc in tool_calls:
            tool_name = tc["name"]
            tool_args = tc["arguments"]

            if verbose and STREAM_CODE:
                if tool_name == "write_file":
                    content = tool_args.get("content", "")
                    if content:
                        ui.preview_code(tool_args.get("path", ""), content)
                elif tool_name == "edit_file":
                    old_t = tool_args.get("old_text", "")
                    new_t = tool_args.get("new_text", "")
                    if old_t or new_t:
                        ui.preview_edit(
                            tool_args.get("path", ""),
                            old_t, new_t
                        )

            try:
                if verbose and ui.SPINNER_ENABLED:
                    _spin_text = f"{tool_name}: {str(tool_args)[:50]}"
                    with ui.Spinner(_spin_text, delay=2.0):
                        result = tools.execute(
                            tool_name, tool_args,
                            confirm_fn=confirm_fn, input_fn=input_fn,
                            session_id=session_id,
                        )
                else:
                    result = tools.execute(
                        tool_name, tool_args,
                        confirm_fn=confirm_fn, input_fn=input_fn,
                        session_id=session_id,
                    )
            except KeyboardInterrupt:
                if verbose:
                    print()
                    print(f"    {_c('[已中止本轮，会话保留]', _C_YELLOW)}")
                logger.warn("turn_aborted", {"session_id": session_id, "tool": tool_name})
                return _done("[已中止]")

            if verbose:
                ui.print_tool_status(tool_name, tool_args, tools.LAST_VERDICT, result)
                preview = (result or "").split("\n")[0]
                if tool_name == "view_image" and "Error" not in preview:
                    try:
                        _info = vision.image_info(str(tools._resolve_path(tool_args.get("path", ""))))
                        ui.print_image_loaded(_info["path"], _info["size_kb"])
                    except Exception:
                        pass

            full_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

        if session_id:
            try:
                t = tracker.session_total() if tracker else {"prompt": 0, "completion": 0}
                session.save(session_id, [m for m in full_messages if m.get("role") != "system"], meta={
                    "model": llm.MODEL,
                    "work_dir": work_dir,
                    "total_prompt": prev_prompt + t.get("prompt", 0),
                    "total_completion": prev_completion + t.get("completion", 0),
                })
            except Exception:
                pass

    if tool_iterations > MAX_TOOL_ITERATIONS:
        final_text = f"[警告] 达到最大工具调用轮数 ({MAX_TOOL_ITERATIONS})，任务可能未完成。"
        ui.print_warning(final_text)

    return _done(final_text)
