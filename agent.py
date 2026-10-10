#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent.py — CLI 入口（对应 PI 的 coding-agent）

职责：argparse、config → providers → WORK_DIR/SAFE_MODE 接线、
      单次任务 / 交互模式外壳。斜杠命令见 commands.py，循环见 loop.py。

依赖方向（plan8.0）：
  agent → loop, commands, config, providers, tools, ui, llm, vision, logger, session, usage
  不 import skill_manager / context（由 loop / commands 使用）。
"""
import argparse
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import llm
import ui
import tools
import vision
import logger
import config
import providers
import session
import usage
import loop
import commands
import heartbeat
import taskdir


_C_GRAY = ui.C_GRAY
_C_CYAN = ui.C_CYAN
_C_RED = ui.C_RED


def _c(text, color):
    return f"{color}{text}{ui.C_RESET}"


def _encode_image_paths(image_paths, work_dir):
    """
    将 --image 路径编码为 base64 data URL 列表。
    失败的路径打印报错并跳过。返回 list[str]（可能为空）。
    """
    urls = []
    for raw in image_paths:
        p = raw
        if not os.path.isabs(p):
            p = os.path.join(work_dir, p)
        try:
            urls.append(vision.image_to_base64(p))
            info = vision.image_info(p)
            ui.print_image_loaded(info["path"], info["size_kb"])
        except (FileNotFoundError, ValueError) as e:
            print(f"  {_c(ui.ICO_FAIL + ' ' + str(e), _C_RED)}")
    return urls


def _sweep_temp(work_dir, protect_sid):
    """启动时清掉过期的任务目录和旧版 temp 平铺文件。当前要恢复的会话不动。"""
    try:
        res = taskdir.sweep(
            work_dir,
            ttl_days=float(config.get("task_temp_ttl_days", 7) or 7),
            protect=[protect_sid] if protect_sid else [],
        )
    except Exception:
        return
    n = res["task_dirs"] + res["legacy"]
    if n:
        print(f"  {_c('已清理过期临时文件 ' + str(n) + ' 项，释放 ' + taskdir.fmt_bytes(res['bytes']), _C_GRAY)}")


def _load_resume(resume, session_id):
    """
    解析 resume 目标，返回 (sid, messages, prev_prompt, prev_completion, leaf_id)。
    resume 为 None 时新建。
    """
    messages = []
    sid = session_id or uuid.uuid4().hex[:8]
    prev_p, prev_c = 0, 0
    if not resume:
        return sid, messages, prev_p, prev_c, None
    target_id = resume if resume != "latest" else session.latest()
    if not target_id:
        print(f"  {_c(ui.ICO_FAIL + ' 没有可恢复的会话', _C_RED)}")
        return sid, messages, prev_p, prev_c, None
    data = session.load(target_id)
    if not data:
        print(f"  {_c(f'{ui.ICO_FAIL} 会话 {target_id} 不存在或已损坏，新建会话', _C_RED)}")
        return uuid.uuid4().hex[:8], [], 0, 0, None
    meta = data["meta"]
    nmsg = len(data["messages"])
    print(f"  {_c(f'[已恢复会话 {target_id}，{nmsg} 条消息]', _C_GRAY)}")
    return (
        target_id,
        data["messages"],
        meta.get("total_prompt", 0),
        meta.get("total_completion", 0),
        meta.get("leaf_id"),
    )


def run_single(prompt, work_dir, model=None, image_paths=None, resume=None, session_id=None, probe=None):
    """执行单次任务。"""
    work_dir = os.path.abspath(work_dir)
    tools.WORK_DIR = work_dir
    tools.ensure_temp_dir(work_dir)
    loop.reset_context_warnings()

    sid, messages, total_prompt_prev, total_completion_prev, leaf_id = _load_resume(resume, session_id)
    _sweep_temp(work_dir, sid)
    initial_images = _encode_image_paths(image_paths or [], work_dir)

    logger.info("session_start", {"session_id": sid, "model": model or llm.MODEL, "resumed": bool(resume)})

    ok, msg, cap = llm.check_connection(model=model, probe=probe)
    if not ok:
        ui.print_error(msg)
        logger.error("connection_lost", {"error": msg})
        sys.exit(1)
    loop.apply_capability(cap)

    ui.print_banner(model or llm.MODEL, llm.BASE_URL, work_dir, loop.SHOW_REASONING,
                    safe_mode=tools.SAFE_MODE, context_limit=loop.CONTEXT_LIMIT,
                    capability=llm.CAPABILITY, connected=True,
                    thinking=config.get("reasoning_effort", "low"))
    ui.turn_header(1, sid)
    print(f"  {ui.ICO_USER} {prompt}")

    user = {"role": "user", "content": prompt}
    session.stamp_missing([user], fallback_parent=leaf_id)
    messages.append(user)
    leaf_id = user["id"]
    path = session.path_to_leaf(messages, leaf_id)
    tracker = usage.Tracker(sid)
    reply, pt, ct, elapsed, full_messages = loop.agent_loop(
        path, work_dir, sid, initial_images=initial_images, tracker=tracker,
        prev_prompt=total_prompt_prev, prev_completion=total_completion_prev,
        interactive=False,
    )
    saved_path = [m for m in full_messages if m.get("role") != "system"]
    leaf_id = session.absorb_path(messages, saved_path)

    if reply and reply != "[已中止]":
        print()
    elif reply == "[已中止]":
        print()
    ui.turn_footer(
        {"prompt_tokens": pt, "completion_tokens": ct} if pt else None,
        elapsed,
        rounds=loop.LAST_TOOL_ROUNDS,
        peak=loop.LAST_PEAK_PROMPT,
    )
    total_prompt = total_prompt_prev + pt
    total_completion = total_completion_prev + ct
    ui.print_summary(1, total_prompt, total_completion)

    try:
        session.save(sid, messages, meta={
            "provider": providers.get_active_name(),
            "model": model or llm.MODEL,
            "work_dir": work_dir,
            "total_prompt": total_prompt,
            "total_completion": total_completion,
            "leaf_id": leaf_id,
        })
    except Exception:
        pass

    logger.info("session_end", {"session_id": sid, "turns": 1, "total_prompt_tokens": pt, "total_completion_tokens": ct,
                                "session_total_prompt": total_prompt, "session_total_completion": total_completion})
    logger.close()
    print("  bye\n")


def _read_multiline():
    """多行输入，单独一行 . 结束。"""
    print("  (多行模式：粘贴内容，单独一行输入 . 结束)")
    lines = []
    while True:
        try:
            line = input("  | ")
        except (EOFError, KeyboardInterrupt):
            break
        if line.strip() == ".":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _drain_pasted_lines(first):
    """粘贴时后续行已在控制台缓冲区，尽力一次读完并合并。仅 Windows。"""
    if sys.platform != "win32":
        return first
    try:
        import msvcrt
        import time as _t
        extra = []
        _t.sleep(0.05)
        while msvcrt.kbhit():
            extra.append(input())
            _t.sleep(0.02)
        if extra:
            return "\n".join([first] + extra).strip()
    except Exception:
        pass
    return first


def run_interactive(work_dir, model=None, resume=None, session_id=None, probe=None):
    """交互模式：斜杠命令交给 commands.handle，其余走 loop.agent_loop。"""
    work_dir = os.path.abspath(work_dir)
    tools.WORK_DIR = work_dir
    tools.ensure_temp_dir(work_dir)

    hb = heartbeat.ping(llm.BASE_URL)
    if hb.get("ok"):
        logger.info("heartbeat_ok", hb)
    else:
        logger.warn("heartbeat_fail", hb)

    ok, msg, cap = llm.check_connection(model=model, probe=probe)
    if ok:
        loop.apply_capability(cap)
    else:
        ui.print_error(msg)
        logger.error("connection_lost", {"error": msg})
        cap = {}

    ui.print_banner(model or llm.MODEL, llm.BASE_URL, work_dir, loop.SHOW_REASONING,
                    safe_mode=tools.SAFE_MODE, context_limit=loop.CONTEXT_LIMIT,
                    capability=llm.CAPABILITY, connected=ok,
                    thinking=config.get("reasoning_effort", "low"))

    sid, messages, total_prompt, total_completion, leaf_id = _load_resume(resume, session_id)
    _sweep_temp(work_dir, sid)
    path0 = session.path_to_leaf(messages, leaf_id)
    turn = len([m for m in path0 if m.get("role") == "user"]) if path0 else 0
    loop.reset_context_warnings()
    logger.info("session_start", {"session_id": sid, "model": model or llm.MODEL, "resumed": bool(resume)})
    tracker = usage.Tracker(sid)
    state = commands.CliState(
        work_dir, model, sid, messages, total_prompt, total_completion, turn, tracker,
        leaf_id=leaf_id,
    )

    while True:
        try:
            prompt = input(f"  {_c('>', _C_CYAN)} ")
        except (EOFError, KeyboardInterrupt):
            print()
            break

        prompt = _drain_pasted_lines(prompt.rstrip("\n")).strip()
        if not prompt:
            continue

        if "\n" in prompt and prompt.lstrip().startswith("/"):
            first, rest = prompt.split("\n", 1)
            if first.strip() == "/paste":
                prompt = rest.strip() or _read_multiline()
            else:
                print("  (已忽略粘贴的多余行，斜杠命令只取第一行)")
                prompt = first.strip()
        elif prompt == "/paste":
            prompt = _read_multiline()

        if not prompt:
            continue
        if commands.handle(prompt, state):
            if state.quit:
                break
            continue

        state.turn += 1
        logger.info("turn_start", {"turn": state.turn, "session_id": state.sid})
        ui.turn_header(state.turn, state.sid)
        print(f"  {ui.ICO_USER} {prompt}")

        turn_images = _encode_image_paths(vision.guess_image_files(prompt), work_dir)
        user = {"role": "user", "content": prompt}
        session.stamp_missing([user], fallback_parent=state.leaf_id)
        state.messages.append(user)
        state.leaf_id = user["id"]
        path = session.path_to_leaf(state.messages, state.leaf_id)

        reply, pt, ct, elapsed, full_messages = loop.agent_loop(
            path, work_dir, state.sid,
            initial_images=turn_images or None, tracker=state.tracker,
            prev_prompt=state.total_prompt, prev_completion=state.total_completion,
        )

        saved_path = [m for m in full_messages if m.get("role") != "system"]
        state.leaf_id = session.absorb_path(state.messages, saved_path)

        if reply == "[已中止]":
            print()
        elif reply:
            print()

        state.total_prompt += pt
        state.total_completion += ct

        try:
            session.save(state.sid, state.messages, meta={
                "provider": providers.get_active_name(),
                "model": model or llm.MODEL,
                "work_dir": work_dir,
                "total_prompt": state.total_prompt,
                "total_completion": state.total_completion,
                "leaf_id": state.leaf_id,
            })
        except Exception:
            pass

        ui.turn_footer(
            {"prompt_tokens": pt, "completion_tokens": ct} if pt else None,
            elapsed,
            rounds=loop.LAST_TOOL_ROUNDS,
            peak=loop.LAST_PEAK_PROMPT,
        )
        logger.info("turn_end", {"turn": state.turn, "session_id": state.sid,
                                  "prompt_tokens": pt, "completion_tokens": ct,
                                  "total_prompt_tokens": state.total_prompt,
                                  "total_completion_tokens": state.total_completion})

    if state.turn > 0:
        ui.print_summary(state.turn, state.total_prompt, state.total_completion)

    logger.info("session_end", {
        "session_id": state.sid, "turns": state.turn,
        "total_prompt_tokens": state.total_prompt,
        "total_completion_tokens": state.total_completion,
    })
    logger.close()
    print("  bye\n")


def main():
    """解析 CLI，接线 config → providers → WORK_DIR/SAFE_MODE，再进单次或交互。"""
    parser = argparse.ArgumentParser(
        description="agent_in {} — Minimal CLI Agent".format(ui.APP_VERSION),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python agent.py
  python agent.py "列出当前目录文件"
  python agent.py -w /path/to/project "分析 src/ 下的代码结构"
  python agent.py --model OtherModel "你好"
  python agent.py --provider backup "你好"
  python agent.py -r "继续上次的任务"
        """,
    )
    parser.add_argument("prompt", nargs="*", help="任务描述（可选，不提供则进入交互模式）")
    parser.add_argument("-w", "--work-dir", default=".", help="工作目录（默认当前目录）")
    parser.add_argument("--model", default=None, help="覆盖默认模型")
    parser.add_argument("-i", "--image", action="append", default=[],
                        help="图片文件路径（可多次指定，v3.0 多模态）")
    parser.add_argument("--provider", default=None,
                        help="切换 provider profile（F3）")
    parser.add_argument("--config", default=None,
                        help="指定 agent_config.json 路径（F1）")
    parser.add_argument("-r", "--resume", action="store_true", default=False,
                        help="恢复最近会话（F2）")
    parser.add_argument("--resume-id", default=None,
                        help="恢复指定 session_id（F2，比 -r 更精确）")
    parser.add_argument("--session-id", default=None,
                        help="手动指定新 session_id（高级）")
    parser.add_argument("--probe", action="store_true", default=False,
                        help="启动时强制重探模型能力（H4，忽略缓存）")
    parser.add_argument("-V", "--version", action="version",
                        version="agent_in {}".format(ui.APP_VERSION))

    args = parser.parse_args()

    cli_overrides = {}
    if args.model:
        cli_overrides["model"] = args.model
    if args.provider:
        cli_overrides["provider"] = args.provider
    if args.work_dir and args.work_dir != ".":
        cli_overrides["work_dir"] = os.path.abspath(args.work_dir)
    if args.config:
        config.set_path(args.config)
    cfg = config.load(cli_overrides)

    loop.CONTEXT_LIMIT = cfg.get("context_limit", 196000)
    loop.MAX_TOOL_ITERATIONS = cfg.get("max_tool_iterations", 80)
    tools.SAFE_MODE = bool(cfg.get("safe_mode", True))

    active_name = args.provider or cfg.get("provider", "default")
    active_p = providers.get_active(active_name)
    providers.apply_to_llm(active_p)
    providers.set_active_name(active_name)
    loop.apply_profile(active_name)

    work_dir = os.path.abspath(args.work_dir)
    if args.model:
        llm.MODEL = args.model

    resume_target = None
    if args.resume_id:
        resume_target = args.resume_id
    elif args.resume:
        resume_target = "latest"

    if args.prompt:
        prompt = " ".join(args.prompt)
        run_single(prompt, work_dir, model=args.model, image_paths=args.image,
                   resume=resume_target, session_id=args.session_id, probe=args.probe)
    else:
        run_interactive(work_dir, model=args.model,
                        resume=resume_target, session_id=args.session_id, probe=args.probe)


if __name__ == "__main__":
    main()
