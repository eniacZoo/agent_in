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
import re
import sys
import time
import hashlib

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
import debug


# ---------------------------------------------------------------------------
# 配置（main 在 config.load 之后可覆盖 CONTEXT_LIMIT / MAX_TOOL_ITERATIONS）
# ---------------------------------------------------------------------------
SHOW_REASONING = os.environ.get("SHOW_REASONING", "1") in ("1", "true", "yes")
STREAM_CODE = ui.STREAM_CODE
MAX_TOOL_ITERATIONS = 80
CONTEXT_LIMIT = 196_000
LAST_TOOL_ROUNDS = 0       # 最近一次 agent_loop 的工具轮次（/status）
LAST_PROMPT_TOKENS = 0     # 最近一次 LLM 请求的 prompt tokens（上下文占用）
LAST_PEAK_PROMPT = 0       # 本回合单次 prompt 峰值
TURN_LEDGER = []            # 本会话已完成的关键操作，跨回合保留
LEDGER_MAX_LINES = 40
LEDGER_LINE_MAX = 120
DEFAULT_SYSTEM_PROMPT = """你是一个极简 CLI Agent，运行在用户的本地机器上。
可用工具：read_file、write_file、edit_file、shell、view_image、glob、grep。

规则：
1. 简洁，直接执行，不要寒暄
2. 先读后改：改文件前先 read_file
3. 路径相对于工作目录：{work_dir}
4. 当前 shell 是 {shell}。PowerShell 下多条命令用 `;` 分隔，不要用 `&&` 或 `&`，不要用 `cd /d`，用 `Set-Location`。Windows 列目录用 Get-ChildItem。不要用 shell 做删除，除非用户明确要求。草稿只写 {work_dir}/temp/（可删）。用户要的 html/xlsx/docx/pptx/pdf 写到指定路径（可在工作目录外，会先确认），写完就停。长任务摘要写入 {work_dir}/temp/task_notes.md
5. 用户提到图片时先 view_image
6. 完成后用 1-2 句话总结
7. 办公文件先 read_file 对应流程（不要一次读完全部）：skills/xlsx.md、skills/docx.md、skills/pptx.md、skills/pdf.md；网页 skills/网页.md。周报见 skills/周报转docx.md（先读 docx.md）。包在 vendor/，shell 已带 PYTHONPATH。禁止 pip/npm/conda install。不要虚构 skill 工具。探结构只把摘要写入 temp/，写一份脚本再跑，报错改脚本，不要把整表整文打进对话。"""

_OFFICE_DISCIPLINE = """
## 办公任务纪律
思考保持简短。先读对应 skill；列映射一旦清楚立刻写脚本，不要一路 `python -c`。
探查摘要写 temp/，不要把整表打进对话。表里已有的排名列直接用，不要为公式空转。
xlsx 用 read_file 看结构，不要按 max_column 扫全表。
产物写到用户指定路径且校验通过后**立即停止**：不要再截图、不要再 dump、不要重复校验同一结论。
"""

ABORT_NOTE = "（上一轮被用户中止，未完成。已完成的操作见系统提示中的台账。）"


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


def ledger_add(line):
    """记一条已完成操作。去重 + 截断 + 限长。"""
    line = (line or "").strip().replace("\n", " ")[:LEDGER_LINE_MAX]
    if not line or line in TURN_LEDGER:
        return
    TURN_LEDGER.append(line)
    if len(TURN_LEDGER) > LEDGER_MAX_LINES:
        del TURN_LEDGER[0:len(TURN_LEDGER) - LEDGER_MAX_LINES]


def ledger_clear():
    """/new 时清空。"""
    TURN_LEDGER.clear()


def ledger_block():
    """拼成 system prompt 片段；空台账返回空串。"""
    if not TURN_LEDGER:
        return ""
    lines = "\n".join(f"- {x}" for x in TURN_LEDGER)
    return ("\n\n## 本会话已完成的操作（这些已经做过，不要重复做，也不要重复校验）\n"
            + lines)


def _tool_sig(name, args):
    """(tool_name, args_hash[:12])，用于重复调用检测。"""
    raw = json.dumps(args, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return (name, hashlib.sha1(raw).hexdigest()[:12])


def _is_tool_ok(result):
    """成功结果才算 ok。阻断文案是「操作被拒绝…」，不能当产出。"""
    s = (result or "").lstrip()
    return not (s.startswith("Error") or s.startswith("操作被拒绝"))


def _resolved_tool_path(path, work_dir=None):
    wd = os.path.abspath(work_dir or tools.WORK_DIR)
    raw = str(path or "")
    if not raw:
        return wd, None
    rp = os.path.abspath(raw if os.path.isabs(raw) else os.path.join(wd, raw))
    return wd, rp


def _is_temp_path(path, work_dir=None):
    """草稿：{work_dir}/temp/ 下。"""
    wd, rp = _resolved_tool_path(path, work_dir)
    if rp is None:
        return False
    temp = os.path.abspath(os.path.join(wd, "temp"))
    try:
        return os.path.commonpath([temp, rp]) == temp
    except ValueError:
        return False


def _is_product_write(path, work_dir=None):
    """用户侧产物：不是 temp/ 草稿。工作目录内正式文件、桌面等出界路径都算。"""
    if not str(path or ""):
        return False
    return not _is_temp_path(path, work_dir)


def _shell_mutates_temp(command, work_dir=None):
    """shell 在跑/写 work_dir/temp 下的脚本。"""
    cmd = command or ""
    if not re.search(r'(?i)temp[/\\].+\.(py|ps1)\b', cmd):
        return False
    wd = os.path.abspath(work_dir or tools.WORK_DIR)
    temp = os.path.abspath(os.path.join(wd, "temp"))
    return ("temp" in cmd.replace("/", "\\").lower()) or (temp.lower() in cmd.lower())


_PRODUCT_FILE = re.compile(
    r'(?i)["\']([^"\']+\.(?:html?|xlsx|xlsm|docx|pptx|pdf|csv))["\']'
    r'|([A-Za-z]:\\[^\s"\']+\.(?:html?|xlsx|xlsm|docx|pptx|pdf|csv))'
)
_PRODUCT_MTIME_SEC = 180


def _product_paths_in_text(text):
    out = []
    for m in _PRODUCT_FILE.finditer(text or ""):
        p = m.group(1) or m.group(2)
        if p:
            out.append(p)
    return out


def _shell_wrote_product(command, result, work_dir=None):
    """脚本写出 html/xlsx 等（路径在命令、输出或源码里），且文件是刚写的。"""
    blobs = [command or "", result or ""]
    m = tools._PY_SCRIPT.search(command or "")
    if m:
        _, script = _resolved_tool_path(m.group(1), work_dir)
        if script:
            try:
                blobs.append(open(script, encoding="utf-8", errors="replace").read(80000))
            except OSError:
                pass
    now = time.time()
    seen = set()
    for blob in blobs:
        for path in _product_paths_in_text(blob):
            if not _is_product_write(path, work_dir):
                continue
            _, rp = _resolved_tool_path(path, work_dir)
            if not rp or rp in seen:
                continue
            seen.add(rp)
            try:
                if os.path.isfile(rp) and now - os.path.getmtime(rp) <= _PRODUCT_MTIME_SEC:
                    return True
            except OSError:
                continue
    return False


def should_stall_stop(mutated, rounds_since_write):
    """已开始改用户侧文件，且连续 12 轮没有产物 → 停。草稿不停这条。"""
    return bool(mutated) and rounds_since_write >= 12


def _for_save(transcript):
    """存盘轨迹：去掉窗口摘要，不含 system。"""
    out = []
    for m in transcript:
        if m.get("role") == "system":
            continue
        if m.get("name") == "context_summary":
            continue
        out.append(m)
    return out


def _cap_transcript_msg(msg):
    """单条 tool / write_file 参数存盘上限。"""
    m = dict(msg)
    cap = context.TRANSCRIPT_TOOL_MAX_CHARS
    content = m.get("content")
    if m.get("role") == "tool" and isinstance(content, str) and len(content) > cap:
        m["content"] = content[:cap] + f"\n…(truncated {len(content) - cap} chars)"
        return m
    if m.get("role") == "assistant" and m.get("tool_calls"):
        tcs = []
        changed = False
        for tc in m["tool_calls"]:
            fn = dict(tc.get("function") or {})
            raw = fn.get("arguments") or ""
            try:
                args = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                tcs.append(tc)
                continue
            if isinstance(args, dict) and isinstance(args.get("content"), str) and len(args["content"]) > cap:
                n = len(args["content"])
                args["content"] = args["content"][:cap] + f"…(truncated {n - cap} chars, path={args.get('path', '')})"
                new_tc = dict(tc)
                new_fn = dict(fn)
                new_fn["arguments"] = json.dumps(args, ensure_ascii=False)
                new_tc["function"] = new_fn
                tcs.append(new_tc)
                changed = True
            else:
                tcs.append(tc)
        if changed:
            m["tool_calls"] = tcs
    return m


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
    "TOOL: glob {\"pattern\": \"*.xlsx\"}\n"
    "TOOL: grep {\"pattern\": \"openpyxl\"}\n"
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
        full_messages = [system] + transcript（未裁剪）。Ctrl-C 中止时文本为「[已中止]」。
    """
    global LAST_PROMPT_TOKENS, LAST_PEAK_PROMPT
    LAST_PEAK_PROMPT = 0
    system_prompt = os.environ.get(
        "SYSTEM_PROMPT",
        DEFAULT_SYSTEM_PROMPT.format(work_dir=work_dir, shell=tools.shell_name()),
    )
    system_prompt += _OFFICE_DISCIPLINE
    system_prompt += ledger_block()

    memory_content, mem_chars = memory_manager.load()
    if memory_content.strip() and memory_content.strip() != "# Agent Memory":
        system_prompt += "\n\n## 长期记忆（跨会话）\n" + memory_content
        logger.info("memory_loaded", {"chars": mem_chars})

    transcript = list(messages)
    system_msg = {"role": "system", "content": system_prompt}

    total_prompt = 0
    total_completion = 0
    total_elapsed = 0.0
    tool_iterations = 0
    final_text = ""
    confirm_fn = ui.confirm if interactive else (lambda _p: False)
    input_fn = ui.text_input if interactive else None
    _writes_this_turn = 0
    seen_calls = {}
    rounds_since_write = 0
    _mutated = False
    scratch_rounds = 0
    _scratching = False

    if debug.ENABLED and messages:
        last = messages[-1]
        if last.get("role") == "user":
            debug.emit("USER", {"content": (last.get("content") or "")[:2000]})

    def _packed():
        return [system_msg] + transcript

    def _done(text):
        global LAST_TOOL_ROUNDS
        LAST_TOOL_ROUNDS = tool_iterations
        return text, total_prompt, total_completion, total_elapsed, _packed()

    def _save_mid():
        if not session_id:
            return
        try:
            t = tracker.session_total() if tracker else {"prompt": 0, "completion": 0}
            session.save(session_id, _for_save(transcript), meta={
                "model": llm.MODEL,
                "work_dir": work_dir,
                "total_prompt": prev_prompt + t.get("prompt", 0),
                "total_completion": prev_completion + t.get("completion", 0),
            })
        except Exception:
            pass

    def _mark_abort():
        if not transcript or transcript[-1].get("content") != ABORT_NOTE:
            transcript.append({"role": "assistant", "content": ABORT_NOTE})

    def _fill_remaining_tools(start_idx, result_text):
        for rest in tool_calls[start_idx:]:
            transcript.append(_cap_transcript_msg({
                "role": "tool",
                "tool_call_id": rest["id"],
                "content": result_text,
            }))

    while tool_iterations < MAX_TOOL_ITERATIONS:
        t0 = time.time()

        pending_imgs = []
        if initial_images:
            pending_imgs = list(initial_images)
            initial_images = None
        pending_imgs.extend(tools.drain_pending_images())

        if verbose and tool_iterations > 0:
            ui.turn_sep(
                f"round {tool_iterations}/{MAX_TOOL_ITERATIONS}"
                f" | last {ui.fmt_tokens(LAST_PROMPT_TOKENS)}"
                f" | cum {ui.fmt_tokens(total_prompt)}"
                f" | 产出 {_writes_this_turn}"
            )

        disp = ui.StreamDisplay(show_reasoning=SHOW_REASONING)
        text_parts = []
        tool_calls = []
        reasoning_parts = []

        window = context.window_from_transcript(
            transcript, system_prompt,
            keep_recent_tokens=config.get("keep_recent_tokens", 20000),
        )
        ctx_action = "none"
        if tool_iterations > 0 and LAST_PROMPT_TOKENS > 0:
            window, ctx_action = context.apply(
                window, LAST_PROMPT_TOKENS, CONTEXT_LIMIT,
                llm_chat_fn=llm.chat,
                auto_summarize=config.get("auto_summarize", True),
                budget=config.get("compact_at_tokens", 40000),
            )
            if ctx_action != "none":
                logger.info("context_manage", {
                    "action": ctx_action,
                    "last_prompt": LAST_PROMPT_TOKENS,
                    "cumulative": total_prompt,
                })
                if verbose:
                    print(f"    {_c(f'{ui.ICO_FILE} 上下文管理: {ctx_action}', _C_GRAY)}")

        try:
            for chunk in llm.chat(window, tools=tools.TOOLS, images=pending_imgs or None):
                ctype = chunk["type"]

                if ctype in ("reasoning", "text"):
                    disp.handle(chunk)
                    if ctype == "text":
                        text_parts.append(chunk["content"])
                    elif ctype == "reasoning":
                        reasoning_parts.append(chunk.get("content") or "")
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
                    LAST_PEAK_PROMPT = max(LAST_PEAK_PROMPT, LAST_PROMPT_TOKENS)
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
            _mark_abort()
            return _done("[已中止]")

        disp.finish()
        elapsed = time.time() - t0
        total_elapsed += elapsed
        current_text = "".join(text_parts).strip()
        if debug.ENABLED:
            if reasoning_parts:
                debug.emit("THINK", {"content": "".join(reasoning_parts)[:4000]})
            if current_text:
                debug.emit("AI", {"content": current_text[:2000]})

        if not tool_calls:
            final_text = current_text
            asst = {"role": "assistant", "content": current_text}
            if reasoning_parts:
                asst["reasoning_content"] = "".join(reasoning_parts)
            transcript.append(asst)
            break

        tool_iterations += 1
        _writes_this_turn_before = _writes_this_turn

        assistant_msg = {"role": "assistant", "content": current_text or None}
        if reasoning_parts:
            assistant_msg["reasoning_content"] = "".join(reasoning_parts)
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
        transcript.append(_cap_transcript_msg(assistant_msg))

        aborted = False
        for ti, tc in enumerate(tool_calls):
            tool_name = tc["name"]
            tool_args = tc["arguments"]
            _sig = _tool_sig(tool_name, tool_args)
            seen_calls[_sig] = seen_calls.get(_sig, 0) + 1
            logger.info("tool_call_sig", {
                "name": tool_name,
                "args_hash": _sig[1],
                "count": seen_calls[_sig],
            })
            if seen_calls[_sig] >= 2:
                logger.warn("tool_repeat", {
                    "name": tool_name,
                    "args_hash": _sig[1],
                    "count": seen_calls[_sig],
                })
                if verbose:
                    ui.print_warning(f"重复调用 {tool_name}（第 {seen_calls[_sig]} 次，参数相同）")
            if debug.ENABLED:
                debug.emit("TOOL_CALL", {"name": tool_name, "args": str(tool_args)[:400]})
                if tool_name == "read_file":
                    sp = str(tool_args.get("path") or "")
                    if debug.is_skill_path(sp):
                        debug.emit("SKILL_LOAD", {"name": os.path.splitext(os.path.basename(sp))[0], "path": sp})

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
                transcript.append(_cap_transcript_msg({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": "Error: 用户中止",
                }))
                _fill_remaining_tools(ti + 1, "Error: 用户中止")
                _mark_abort()
                aborted = True
                break

            if verbose:
                ui.print_tool_status(tool_name, tool_args, tools.LAST_VERDICT, result)
                preview = (result or "").split("\n")[0]
                if tool_name == "view_image" and "Error" not in preview:
                    try:
                        _info = vision.image_info(str(tools._resolve_path(tool_args.get("path", ""))))
                        ui.print_image_loaded(_info["path"], _info["size_kb"])
                    except Exception:
                        pass

            if tool_name in ("write_file", "edit_file"):
                _path = tool_args.get("path", "")
                ledger_add(f"{tool_name} {_path}")
                if _is_temp_path(_path, work_dir):
                    _scratching = True
                else:
                    _mutated = True
                if _is_tool_ok(result) and _is_product_write(_path, work_dir):
                    _writes_this_turn += 1
            elif tool_name == "shell":
                cmd = str(tool_args.get("command", ""))
                if _shell_mutates_temp(cmd, work_dir):
                    _scratching = True
                if _is_tool_ok(result):
                    ledger_add(f"shell {cmd[:80]} -> ok")
                    if _shell_wrote_product(cmd, result, work_dir):
                        _writes_this_turn += 1

            transcript.append(_cap_transcript_msg({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            }))

        if aborted:
            return _done("[已中止]")

        if _writes_this_turn > _writes_this_turn_before:
            rounds_since_write = 0
            scratch_rounds = 0
        elif _mutated:
            rounds_since_write += 1
            if rounds_since_write == 5 and verbose:
                ui.print_warning("已开始改文件但还没有用户侧产物，可能在原地打转")
            if should_stall_stop(_mutated, rounds_since_write):
                final_text = ("[停止] 连续 12 轮无文件产出，已停止。"
                              + ledger_block())
                ui.print_warning("连续 12 轮无产出，已停止本轮")
                transcript.append({"role": "assistant", "content": final_text})
                break
        elif _scratching:
            scratch_rounds += 1
            if scratch_rounds in (20, 40) and verbose:
                ui.print_warning("还在 temp/ 里打转，记得写出用户要的文件")

        _save_mid()

    if tool_iterations >= MAX_TOOL_ITERATIONS:
        final_text = f"[警告] 达到最大工具调用轮数 ({MAX_TOOL_ITERATIONS})，任务可能未完成。"
        ui.print_warning(final_text)
        transcript.append({"role": "assistant", "content": final_text})

    return _done(final_text)
