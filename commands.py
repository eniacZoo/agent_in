#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
commands.py — 交互斜杠命令表（对应 PI 的 CLI 层）

职责：解析 `/xxx`，改 CliState 或打印结果。core 循环不知道命令存在。

依赖方向（plan8.0）：
  commands → session, memory_manager, skill_manager, config, providers,
             ui, usage, logger, llm, loop
  不 import agent。
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import llm
import ui
import tools
import logger
import config
import providers
import session
import usage
import memory_manager
import skill_manager
import loop
import heartbeat
import debug
import taskdir


_C_GRAY = ui.C_GRAY
_C_GREEN = ui.C_GREEN
_C_CYAN = ui.C_CYAN
_C_YELLOW = ui.C_YELLOW
_C_RED = ui.C_RED


def _c(text, color):
    return f"{color}{text}{ui.C_RESET}"


class CliState:
    """交互会话可变状态。handler 直接改字段。"""

    def __init__(self, work_dir, model, sid, messages, total_prompt, total_completion, turn, tracker,
                 leaf_id=None):
        self.work_dir = work_dir
        self.model = model
        self.sid = sid
        self.messages = messages
        self.total_prompt = total_prompt
        self.total_completion = total_completion
        self.turn = turn
        self.tracker = tracker
        self.quit = False
        self.leaf_id = leaf_id


def handle(prompt, state):
    """
    处理斜杠命令。

    参数：
        prompt: 用户输入（已 strip）
        state: CliState
    返回：
        True 已消费（含 quit / 未知 /xxx）；False 交给 agent_loop。
    """
    if not prompt.startswith("/"):
        return False

    parts = prompt.split(maxsplit=1)
    cmd = parts[0]
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("/quit", "/exit", "/q"):
        n = tools.kill_all_jobs()
        if n:
            print(f"  {_c('已停止 ' + str(n) + ' 个后台任务', _C_GRAY)}")
        state.quit = True
        return True

    if cmd == "/continue":
        # 不拦截。交接摘要已在上下文里，原句交给模型接着做。
        return False

    if cmd == "/ls" and arg == "skills":
        _cmd_ls_skills(state)
        return True

    if cmd == "/memory":
        _cmd_memory(state, arg)
        return True

    if cmd == "/logs":
        _cmd_logs(state, arg)
        return True

    fn = _COMMANDS.get(cmd)
    if fn is None:
        print(f"  {_c('未知命令，输入 /help', _C_YELLOW)}")
        return True
    fn(state, arg)
    return True


def _cmd_new(state, _arg):
    state.sid = uuid.uuid4().hex[:8]
    state.messages = []
    state.leaf_id = None
    state.total_prompt = 0
    state.total_completion = 0
    state.turn = 0
    loop.ledger_clear()
    print(f"  {_c('[new session: ' + state.sid + ']', _C_GRAY)}")


def _cmd_sessions(state, _arg):
    sessions_list = session.list_sessions(limit=10)
    print()
    if not sessions_list:
        print("  (暂无会话)")
    else:
        for s in sessions_list:
            row_id = s["session_id"]
            marker = " ←" if row_id == state.sid else ""
            print(f"  {_c(row_id, _C_CYAN)}{marker}  {s['updated'][-16:-9]}  "
                  f"turns={s['num_messages']//2 or 1}  "
                  f"model={s.get('model','')}  {s.get('first_user','')[:25]}")
    print()


def _cmd_resume(state, arg):
    if arg:
        target = arg.strip()
        data = session.load(target)
        if data:
            state.messages = data["messages"]
            meta = data["meta"]
            state.leaf_id = meta.get("leaf_id")
            state.total_prompt = meta.get("total_prompt", 0)
            state.total_completion = meta.get("total_completion", 0)
            path = session.path_to_leaf(state.messages, state.leaf_id)
            state.turn = len([m for m in path if m.get("role") == "user"])
            state.sid = target
            state.tracker = usage.Tracker(state.sid)
            print(f"  {_c(f'{ui.ICO_OK} 已恢复会话 {target}（{len(state.messages)} 条消息）', _C_GREEN)}")
        else:
            print(f"  {_c(f'{ui.ICO_FAIL} 会话 {target} 不存在或已损坏', _C_RED)}")
        return
    sessions_list = session.list_sessions(limit=5)
    if not sessions_list:
        print("  (暂无可恢复的会话)")
    else:
        print(f"\n  最近 5 个会话:")
        for i, s in enumerate(sessions_list):
            marker = " ←" if s["session_id"] == state.sid else ""
            print(f"   [{i+1}] {_c(s['session_id'], _C_CYAN)}{marker}  "
                  f"{s['updated'][-16:-9]}  {s.get('first_user','')[:30]}")
        print(f"  输入 /resume <id> 恢复")
    print()


def _cmd_save(state, _arg):
    try:
        p = session.save(state.sid, state.messages, meta={
            "provider": providers.get_active_name(),
            "model": state.model or llm.MODEL,
            "work_dir": state.work_dir,
            "total_prompt": state.total_prompt,
            "total_completion": state.total_completion,
            "leaf_id": state.leaf_id,
        })
        print(f"  {_c(f'{ui.ICO_OK} 已保存会话 {state.sid} → {p.name}', _C_GREEN)}")
    except Exception as e:
        print(f"  {_c(f'{ui.ICO_FAIL} 保存失败: {e}', _C_RED)}")


def _cmd_status(state, _arg):
    print()
    print(f"   version: {ui.APP_VERSION}")
    print(f"   turns: {state.turn}  |  session: {state.sid}")
    st = state.tracker.session_total() if state.tracker else {"prompt": 0, "completion": 0, "calls": 0}
    print(f"   本会话: prompt {ui.fmt_tokens(st['prompt'])} / completion {ui.fmt_tokens(st['completion'])}  ({st['calls']} calls)")
    day = usage.day_total()
    print(f"   今日:   prompt {ui.fmt_tokens(day['prompt'])} / completion {ui.fmt_tokens(day['completion'])}  ({day['sessions']} sessions)")
    week = usage.range_total(7)
    print(f"   近7天:  prompt {ui.fmt_tokens(week['prompt'])} / completion {ui.fmt_tokens(week['completion'])}  ({week['sessions']} sessions)")
    bd = usage.model_breakdown(7)
    if bd:
        print(f"   按模型:")
        for mname, mv in sorted(bd.items(), key=lambda x: x[1]['prompt'], reverse=True):
            print(f"     {mname}: in {ui.fmt_tokens(mv['prompt'])} / out {ui.fmt_tokens(mv['completion'])}")
    print(f"   SAFE_MODE: {'ON' if tools.SAFE_MODE else 'OFF'}")
    print(f"   work_dir: {state.work_dir}")
    limit = loop.CONTEXT_LIMIT or 1
    used = loop.LAST_PROMPT_TOKENS
    pct = used / limit * 100
    print(f"   上下文: {pct:.0f}% ({ui.fmt_tokens(used)}/{ui.fmt_tokens(limit)})")
    print(f"   本轮工具轮次: {loop.LAST_TOOL_ROUNDS} / 上限 {loop.MAX_TOOL_ITERATIONS}")
    tel = loop.LAST_TELEMETRY
    if tel is not None:
        for line in tel.summary_lines():
            print(f"   {line}")
    print(f"   task_temp: {tools.task_temp(create=False)}")
    print(f"   连接: {'yes' if getattr(llm, 'CONNECTED', False) else 'DISCONNECTED'}")
    hb = heartbeat.LAST or {}
    if hb:
        ok = "ok" if hb.get("ok") else "fail"
        print(f"   心跳: {ok}  {hb.get('elapsed_ms', 0)}ms")
    ttfb = getattr(llm, "LAST_TTFB_MS", 0)
    if ttfb:
        print(f"   上次 TTFB: {ttfb}ms")


def _cmd_tree(state, _arg):
    if not state.messages:
        print("  (空会话)")
        return
    print()
    print(f"   当前叶: {_c(state.leaf_id or '-', _C_GREEN)}")
    for line in session.format_tree(state.messages, state.leaf_id):
        print(line)
    print()


def _cmd_fork(state, arg):
    key = (arg or "").strip()
    if not key:
        print(f"  {_c(ui.ICO_FAIL + ' 用法: /fork <id>', _C_RED)}")
        return
    node = session.find_node(state.messages, key)
    if not node:
        print(f"  {_c(f'{ui.ICO_FAIL} 找不到节点 {key}', _C_RED)}")
        return
    state.leaf_id = node.get("id")
    path = session.path_to_leaf(state.messages, state.leaf_id)
    state.turn = len([m for m in path if m.get("role") == "user"])
    print(f"  {_c(f'{ui.ICO_OK} 已将叶设为 {state.leaf_id}，下一句将从此分叉', _C_GREEN)}")


def _cmd_history(state, _arg):
    path = session.path_to_leaf(state.messages, state.leaf_id)
    print(f"  [{len(path)} messages on current path]")
    for m in path[-6:]:
        role = m["role"]
        content = m.get("content", "") or ""
        if role == "tool":
            content = f"[tool result] {content[:50]}"
        elif role == "assistant" and m.get("tool_calls"):
            names = [tc["function"]["name"] for tc in m["tool_calls"]]
            content = f"[tool calls: {', '.join(names)}]"
        if len(content) > 60:
            content = content[:57] + "..."
        print(f"    {role}: {content}")


def _cmd_help(_state, _arg):
    ui.print_help()


def _cmd_config(_state, _arg):
    cfg = config.load()
    active_name = providers.get_active_name()
    print()
    print(f"   {'Key':<20} {'Value'}")
    print(f"   {'-'*20} {'-'*30}")
    for k, v in cfg.items():
        if k == "providers":
            print(f"   {k:<20} {list(v.keys())}")
        elif k == "api_key":
            continue
        elif k == "provider":
            print(f"   {k:<20} {active_name}")
        else:
            print(f"   {k:<20} {v}")
    provs = cfg.get("providers", {})
    print(f"\n   Providers (active: {_c(active_name, _C_GREEN)}):")
    for pname, pinfo in provs.items():
        marker = " ←" if pname == active_name else ""
        print(f"     {_c(pname, _C_CYAN)}{marker}: model={pinfo.get('model','')} url={pinfo.get('base_url','')}")
    print()


def _save_provider(name):
    cfg = dict(config.load())
    cfg["provider"] = name
    config.save(cfg)
    config.load(force_reload=True)


def _switch_to_provider(state, target):
    """切到 named provider，探测连接。target 必须已在 providers 里。"""
    active_p = providers.get_active(target)
    providers.apply_to_llm(active_p)
    providers.set_active_name(target)
    _save_provider(target)
    state.model = None
    logger.info("provider_switched", {"target": target, "model": active_p.get("model", "")})
    hb = heartbeat.ping(llm.BASE_URL)
    if hb.get("ok"):
        logger.info("heartbeat_ok", hb)
    else:
        logger.warn("heartbeat_fail", hb)
    loop.apply_profile(target)
    ok, msg, cap = llm.check_connection(model=active_p.get("model"))
    if ok:
        loop.apply_capability(cap)
        _m = active_p.get("model", "")
        print(f"  {_c(f'{ui.ICO_OK} 已切换到 {target} (model={_m})', _C_GREEN)}")
        loop.print_capability(cap)
    else:
        print(f"  {_c(f'{ui.ICO_WARN} {msg}', _C_YELLOW)}")


def _cmd_provider(state, arg):
    if arg:
        target = arg.strip()
        provs = providers.get_all()
        if target not in provs:
            print(f"  {_c(f'{ui.ICO_FAIL} 未知 provider: {target}', _C_RED)}")
            print(f"  可用: {', '.join(providers.list_names())}")
            return
        _switch_to_provider(state, target)
        return
    provs = providers.get_all()
    active_name = providers.get_active_name()
    print(f"\n   当前: {_c(active_name, _C_GREEN)}")
    for pname, pinfo in provs.items():
        marker = " ←" if pname == active_name else ""
        print(f"     {_c(pname, _C_CYAN)}{marker}: {pinfo.get('model','')} @ {pinfo.get('base_url','')}")
    print()


# /model 短名 → provider。DeepSeek=default，办公 Qwen=office。
_MODEL_ALIASES = {
    "deepseek": "default",
    "ds": "default",
    "default": "default",
    "qwen": "office",
    "qw": "office",
    "office": "office",
    "home": "home",
    "qwen-home": "home",
    "openrouter": "home",
}
_EFFORTS = ("low", "medium", "xhigh")


def _save_effort(effort):
    cfg = dict(config.load())
    cfg["reasoning_effort"] = effort
    config.save(cfg)
    config.load(force_reload=True)


def _resolve_model_to_provider(arg):
    """
    把 /model 参数解析成 provider 名。
    认短名（deepseek/qwen）、profile 名、完整 model id。
    无法识别返回 None。
    """
    key = (arg or "").strip()
    if not key:
        return None
    low = key.lower()
    if low in _MODEL_ALIASES:
        return _MODEL_ALIASES[low]
    provs = providers.get_all()
    if key in provs:
        return key
    for name, pinfo in provs.items():
        model = (pinfo.get("model") or "")
        if key == model or low == model.lower():
            return name
    return None


def _cmd_model(state, arg):
    tokens = arg.split() if arg else []
    effort = None
    if tokens:
        last = tokens[-1].lower()
        if last in ("high", "max", "minimal", "none"):
            print(f"  {_c(ui.ICO_FAIL + ' 思考强度只用 low / medium / xhigh', _C_RED)}")
            return
        if last in _EFFORTS:
            effort = last
            tokens = tokens[:-1]
    provider_arg = " ".join(tokens).strip()

    if effort:
        _save_effort(effort)
        print(f"  {_c(ui.ICO_OK + ' 思考强度: ' + effort, _C_GREEN)}")

    if not provider_arg:
        if effort:
            return
        provs = providers.get_all()
        active = providers.get_active_name()
        cur = llm.MODEL
        think = config.get("reasoning_effort", "low")
        print()
        print(f"   当前: {_c(cur, _C_GREEN)}  ({active})  think={think}")
        print(f"   {_c('/model deepseek', _C_CYAN)}  — DeepSeek  {provs.get('default', {}).get('model', '')}")
        print(f"   {_c('/model qwen [low|medium|xhigh]', _C_CYAN)}  — 办公 Qwen  {provs.get('office', {}).get('model', '')}")
        print(f"   {_c('/model office2 [low|medium|xhigh]', _C_CYAN)}  — office2  {provs.get('office2', {}).get('model', '')}")
        print(f"   {_c('/model home [low|medium|xhigh]', _C_CYAN)}  — 家里 Qwen（OpenRouter）  {provs.get('home', {}).get('model', '')}")
        print()
        return
    target = _resolve_model_to_provider(provider_arg)
    if target is None:
        print(f"  {_c(ui.ICO_FAIL + ' 未知模型，用 /model deepseek、/model qwen、/model office2 或 /model home', _C_RED)}")
        return
    _switch_to_provider(state, target)


def _cmd_probe(_state, arg):
    tokens = arg.split() if arg else []
    force = "force" in tokens or "强制" in tokens
    mode = "强制重探" if force else "用缓存"
    print(f"  {_c('正在探测模型能力…（' + mode + '）', _C_GRAY)}")
    # probe=False 会跳过能力、把 CAPABILITY 清空；无 force 应走缓存（probe=None）
    ok, msg, cap = llm.check_connection(model=llm.MODEL, probe=True if force else None)
    if ok:
        loop.apply_capability(cap)
        loop.print_capability(cap)
    else:
        print(f"  {_c(f'{ui.ICO_FAIL} {msg}', _C_RED)}")


def _cmd_memory(_state, arg):
    if not arg:
        print()
        print(memory_manager.get_full_content())
        return
    if arg.startswith("add "):
        text = arg[4:].strip()
        if text:
            memory_manager.append("用户偏好", text)
            logger.info("memory_written", {"section": "用户偏好", "text": text[:50]})
            print(f"  {_c(ui.ICO_OK + ' 已记录到 memory（用户偏好）', _C_GREEN)}")
        return
    if arg.startswith("del "):
        keyword = arg[4:].strip()
        removed = memory_manager.delete(keyword)
        print(f"  删除了 {removed} 条记录" if removed else "  未找到匹配记录")
        return
    if arg == "clear":
        if ui.confirm("确认清空所有 memory？[y/N]"):
            memory_manager.clear()
            print(f"  {_c(ui.ICO_OK + ' 已清空', _C_GREEN)}")
        return
    print(f"  {_c('未知命令，输入 /help', _C_YELLOW)}")


def _cmd_ls_skills(_state):
    scripts = skill_manager.list_skills()
    mds = skill_manager.list_markdown()
    print()
    if not scripts and not mds:
        print("  (暂无 skill)")
        return
    if mds:
        print("  流程（markdown，read_file 或 /read-skill；不进工具表）:")
        for s in mds:
            print(f"    {_c(s['name'], _C_CYAN)}  — {s.get('description', '')[:50]}")
    if scripts:
        print("  脚本（人用 /use 触发；不进工具表）:")
        for s in scripts:
            print(f"    {_c(s['name'], _C_CYAN)}  — {s.get('description', '')[:50]}")


def _cmd_read_skill(_state, arg):
    name = arg.strip()
    if not name:
        mds = skill_manager.list_markdown()
        print()
        if not mds:
            print("  (暂无 markdown 流程)")
            return
        print("  用法: /read-skill <name>")
        for s in mds:
            print(f"    {_c(s['name'], _C_CYAN)}  — {s.get('description', '')[:50]}")
        return
    text, err = skill_manager.read_markdown(name)
    if err:
        print(f"  {_c(ui.ICO_FAIL + ' ' + err, _C_RED)}")
        return
    print()
    print(text)


def _cmd_use(state, arg):
    raw = arg.strip()
    if not raw:
        print("  用法: /use <name> [path]")
        return
    parts = raw.split(maxsplit=1)
    skill_name = parts[0]
    params = {}
    if len(parts) > 1:
        params["path"] = parts[1].strip()
    print(f"  {_c(ui.ICO_SKILL + ' 执行 skill: ' + skill_name, _C_GRAY)}")
    result = skill_manager.execute_skill(
        skill_name, params,
        session_id=state.sid,
        confirm_fn=ui.confirm, input_fn=ui.text_input,
    )
    print(f"  {result[:500]}")


def _cmd_del(_state, arg):
    skill_name = arg.strip()
    if ui.confirm(f"确认删除 skill '{skill_name}'？[y/N]"):
        ok, msg = skill_manager.delete_skill(skill_name)
        print(f"  {msg}")


def _cmd_debug(state, arg):
    a = (arg or "").strip().lower()
    if a in ("on", "1", "true"):
        debug.start(state.sid)
        print(f"  {_c(ui.ICO_OK + ' debug 已开启 → logs/debug/' + state.sid, _C_GREEN)}")
        return
    if a in ("off", "0", "false"):
        debug.stop()
        print(f"  {_c(ui.ICO_OK + ' debug 已关闭', _C_GREEN)}")
        return
    if a in ("report", "rep"):
        p = debug.write_report(state.sid)
        print(f"  {_c(ui.ICO_OK + ' 报告: ' + str(p), _C_GREEN)}")
        return
    print(f"  debug: {'ON' if debug.ENABLED else 'OFF'}  session={state.sid}")
    print(f"  {_c('/debug on|off|report', _C_CYAN)}")


def _cmd_logs(_state, arg):
    n = 20
    if arg:
        first = arg.split()[0]
        n = int(first) if first.isdigit() else 20
    entries = logger.recent(n)
    print()
    if not entries:
        print("  (无日志)")
        return
    for e in entries:
        level = e.get("level", "INFO")
        ts = e.get("ts", "")[-8:]
        event = e.get("event", "")
        data = e.get("data", {})
        color = _C_RED if level == "ERROR" else _C_YELLOW if level == "WARN" else _C_GRAY
        data_str = str(data)[:60] if data else ""
        print(f"  {_c(ts, color)}  {_c(level, color):<5}  {event}  {data_str}")


def _cmd_clean(state, arg):
    """清理过期临时目录。/clean 按保留天数；/clean now 立刻清掉除当前会话外的全部；/clean list 只列出。"""
    flag = (arg or "").strip().lower()
    ttl = 0 if flag == "now" else float(config.get("task_temp_ttl_days", 7) or 7)
    dry = flag in ("list", "ls")
    res = taskdir.sweep(state.work_dir, ttl_days=ttl, protect=(state.sid,), dry_run=dry or flag == "list")
    n_cur, freed_cur = (0, 0)
    if flag == "now":
        n_cur, freed_cur = taskdir.clean_task(state.work_dir, state.sid)
    verb = "可清理" if dry else "已清理"
    print(f"  {_c(verb, _C_GREEN)} 任务目录 {res['task_dirs']} 个，旧临时文件 {res['legacy']} 个，"
          f"{taskdir.fmt_bytes(res['bytes'] + freed_cur)}")
    if n_cur:
        print(f"  当前会话临时文件 {n_cur} 项（plan.md / todo.json / task_notes.md 保留）")
    for p in res["items"][:20]:
        print(f"    {p}")


# 首词 → handler(state, arg)。/ls /memory /logs /quit 在 handle() 里特判。
_COMMANDS = {
    "/new": _cmd_new,
    "/sessions": _cmd_sessions,
    "/resume": _cmd_resume,
    "/save": _cmd_save,
    "/status": _cmd_status,
    "/history": _cmd_history,
    "/tree": _cmd_tree,
    "/fork": _cmd_fork,
    "/help": _cmd_help,
    "/config": _cmd_config,
    "/provider": _cmd_provider,
    "/model": _cmd_model,
    "/probe": _cmd_probe,
    "/use": _cmd_use,
    "/del": _cmd_del,
    "/read-skill": _cmd_read_skill,
    "/debug": _cmd_debug,
    "/clean": _cmd_clean,
}
