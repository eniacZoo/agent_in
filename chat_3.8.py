#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chat_3.8.py — OpenAI 兼容 API 对话客户端（结构化展示 / 流式输出 / Token 明细 / 思考过程）

默认连接 DeepSeek V4.1 Flash，也可通过环境变量切换到任意 OpenAI 兼容 API。

用法:
  python3 chat_3.8.py                          # 交互模式
  python3 chat_3.8.py "你好"                    # 单次提问
  SHOW_REASONING=1 python3 chat_3.8.py         # 显示思考过程

环境变量:
  BASE_URL      OpenAI 兼容 API 基础地址（默认 https://api.deepseek.com）
  API_KEY       Bearer Token
  MODEL         模型名称（默认 deepseek-v4.1-flash-expires-on-0910）
  SYSTEM_PROMPT 自定义系统提示词
  SHOW_REASONING 设为 1 显示思考/推理过程
  MAX_TOKENS    最大回复 token 数（默认 8192）
"""
import sys
if sys.version_info[0] < 3:
    sys.stderr.write("此脚本需要 Python 3，请用 python3 chat_3.8.py\n")
    sys.exit(1)

import json
import os
import time
import urllib.request
import urllib.error
import uuid

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
BASE_URL      = os.environ.get("BASE_URL", "https://api.deepseek.com").rstrip("/")
API_KEY       = os.environ.get("API_KEY", "")
MODEL         = os.environ.get("MODEL", "deepseek-v4.1-flash-expires-on-0910")
SYSTEM_PROMPT = os.environ.get("SYSTEM_PROMPT", "你是一个有帮助的 AI 助手，请用中文回答。")
SHOW_REASONING = os.environ.get("SHOW_REASONING", "0") in ("1", "true", "yes")
MAX_TOKENS    = int(os.environ.get("MAX_TOKENS", "8192"))

W = 58  # 结构化框宽度


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _headers():
    h = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + API_KEY,
    }
    return h


def fmt_tokens(n):
    if n >= 1_000_000:
        return "{:.2f}M".format(n / 1_000_000)
    if n >= 1_000:
        return "{:.1f}K".format(n / 1_000)
    return str(n)


def _estimate_tokens(text):
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other = len(text) - cjk
    return int(cjk / 1.5 + other / 4)


def _now():
    return time.strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# 结构化输出
# ---------------------------------------------------------------------------
def turn_header(turn, session_id):
    """轮次顶部框线。"""
    left = " #{} ".format(turn)
    right = "  {}  ".format(_now())
    mid = session_id[:8]
    total = len(left) + len(right) + len(mid) + 4
    filler = max(W - total, 4)
    print()
    print(" " + "\u2500" * 2 + left + " " + mid + " " + "\u2500" * (filler - 4) + right + "\u2500" * 1)


def turn_sep(label=""):
    """轮次中间分隔线。"""
    if label:
        pad = max(W - 4 - len(label), 0)
        print("   " + "\u2500" * 6 + " " + label + " " + "\u2500" * pad)
    else:
        print("   " + "\u2500" * (W - 4))


def turn_footer(usage, elapsed=None):
    """轮次底部 token 信息。"""
    turn_sep()
    if usage:
        line = "  in {:<8} out {:<8} model {}".format(
            fmt_tokens(usage.get("prompt_tokens", 0)),
            fmt_tokens(usage.get("completion_tokens", 0)),
            usage.get("model", MODEL),
        )
        if elapsed is not None:
            line += "   \u23f1 {:.1f}s".format(elapsed)
        # 额外展示 reasoning tokens
        rt = usage.get("reasoning_tokens", 0)
        if rt > 0:
            line += "   (think: {})".format(fmt_tokens(rt))
        print(line)
    print("   " + "\u2500" * (W - 4))


# ---------------------------------------------------------------------------
# 状态 & 横幅
# ---------------------------------------------------------------------------
def print_banner():
    """启动横幅。"""
    print()
    print("  " + "=" * W)
    print("   OpenAI Chat Client (chat_3.8)")
    print("   " + "-" * W)
    print("   Base URL   : {}".format(BASE_URL))
    print("   Model      : {}  (ctx: 196K, text)".format(MODEL))
    print("   API Key    : {}...{}".format(API_KEY[:8], API_KEY[-4:]))
    print("   System     : {}".format(SYSTEM_PROMPT[:40] + ("…" if len(SYSTEM_PROMPT) > 40 else "")))
    print("   Reasoning  : {}".format("ON" if SHOW_REASONING else "OFF"))
    print("   Max Tokens : {}".format(MAX_TOKENS))
    print("  " + "=" * W)
    print()
    print("   命令: /quit 退出 | /new 新会话 | /status 状态 | /prompt 查看 prompt 估算")
    print()


def print_status(session_prompt, session_completion, turn):
    print()
    print("  " + "-" * W)
    print("   Session    : #{}".format(turn))
    print("   This turn  : in {} | out {}".format(
        fmt_tokens(session_prompt), fmt_tokens(session_completion)))
    print("  " + "-" * W)


def show_prompt_detail():
    """估算 system prompt 的 token 消耗。"""
    print()
    print("  " + "=" * W)
    print("   Prompt 估算 (仅 system + tools)")
    print("   " + "-" * W)
    sys_tokens = _estimate_tokens(SYSTEM_PROMPT)
    print("   {:<28} {:>10}".format("Component", "~Tokens"))
    print("   " + "-" * W)
    print("   {:<28} {:>10}".format("system prompt", sys_tokens))
    print("   {:<28} {:>10}".format("user message (avg)", 50))
    print("   " + "-" * W)
    print("   上下文窗口: 196K tokens (模型上限)")
    print("   剩余可用  : ~{} tokens".format(fmt_tokens(196_000 - sys_tokens - 50)))
    print("  " + "=" * W)


# ---------------------------------------------------------------------------
# Chat（流式 + 结构化）
# ---------------------------------------------------------------------------
def chat_once(prompt, session_id, turn, history):
    """
    发送一条消息，流式输出，结构化展示。
    返回 (完整回复, usage_dict, 耗时秒)
    """
    t0 = time.time()

    # ── 轮次头 ──
    turn_header(turn, session_id)

    # ── 用户输入 ──
    print("  \U0001F9D1 user : {}".format(prompt))

    # ── 构建 messages ──
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)  # 历史对话
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": MAX_TOKENS,
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    req = urllib.request.Request(
        BASE_URL + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers=_headers(),
        method="POST",
    )

    full_text = ""
    full_reasoning = ""
    usage = {}
    first_msg = False
    first_reasoning = False
    model_name = MODEL

    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue

                # OpenAI 格式: choices[0].delta
                choices = obj.get("choices")
                if choices:
                    delta = choices[0].get("delta", {})

                    # 检查 reasoning / thinking 内容
                    # 不同 API 可能用不同字段名
                    reasoning_text = (
                        delta.get("reasoning_content", "")
                        or delta.get("reasoning", "")
                        or delta.get("thinking", "")
                    )
                    content_text = delta.get("content", "")

                    if reasoning_text:
                        if SHOW_REASONING:
                            if not first_reasoning:
                                first_reasoning = True
                                turn_sep("thinking")
                                print("  \U0001F9E0 think: ", end="", flush=True)
                            print(reasoning_text, end="", flush=True)
                        full_reasoning += reasoning_text

                    if content_text:
                        if not first_msg:
                            first_msg = True
                            turn_sep("ai")
                            print("  \U0001F916 ai   : ", end="", flush=True)
                        print(content_text, end="", flush=True)
                        full_text += content_text

                # usage（最后一个 chunk 通常携带 usage）
                if obj.get("usage"):
                    u = obj["usage"]
                    usage = {
                        "prompt_tokens": u.get("prompt_tokens", 0),
                        "completion_tokens": u.get("completion_tokens", 0),
                        "total_tokens": u.get("total_tokens", 0),
                        "model": obj.get("model", MODEL),
                    }
                    # 尝试提取 reasoning tokens
                    comp_details = u.get("completion_tokens_details", {})
                    if comp_details:
                        usage["reasoning_tokens"] = comp_details.get("reasoning_tokens", 0)

                # 记录实际 model name
                if obj.get("model"):
                    model_name = obj["model"]

    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", "replace")
        print("  \U0001F6A8 ERROR [{}]: {}".format(e.code, err_body[:200]))
        elapsed = time.time() - t0
        turn_footer({}, elapsed)
        return "", {}, elapsed
    except Exception as e:
        print("  \U0001F6A8 ERROR: {}".format(str(e)[:200]))
        elapsed = time.time() - t0
        turn_footer({}, elapsed)
        return "", {}, elapsed

    if first_msg or first_reasoning:
        print()  # 流式结束后换行

    elapsed = time.time() - t0

    # ── 轮次尾 ──
    if not usage.get("model"):
        usage["model"] = model_name
    turn_footer(usage, elapsed)

    return full_text.strip(), usage, elapsed


# ---------------------------------------------------------------------------
# 会话汇总
# ---------------------------------------------------------------------------
def print_session_summary(turn, session_prompt, session_completion):
    print()
    print("  " + "=" * W)
    print("   Session Summary")
    print("   " + "-" * W)
    print("   Model        : {}".format(MODEL))
    print("   Turns        : {}".format(turn))
    print("   Total tokens : in {} | out {}".format(
        fmt_tokens(session_prompt), fmt_tokens(session_completion)))
    print("  " + "=" * W)


# ---------------------------------------------------------------------------
# 主程序
# ---------------------------------------------------------------------------
def main():
    # 检查 API key
    if not API_KEY:
        print("  \U0001F6A8 请设置 API_KEY 环境变量")
        sys.exit(1)

    # 快速连通性检查
    try:
        req = urllib.request.Request(
            BASE_URL + "/chat/completions",
            data=json.dumps({"model": MODEL, "messages": [{"role": "user", "content": "hi"}],
                             "max_tokens": 1, "stream": False}).encode(),
            headers=_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                print("  \u26A0\uFE0F API 返回异常状态: {}".format(resp.status))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print("  \U0001F6A8 API Key 认证失败 (401)，请检查 API_KEY")
            sys.exit(1)
        elif e.code == 404:
            print("  \U0001F6A8 模型 '{}' 不存在 (404)，请检查 MODEL 名称".format(MODEL))
            sys.exit(1)
        # 其他错误不阻止启动
    except Exception:
        print("  \u26A0\uFE0F 无法连接 {}（网络不通？）".format(BASE_URL))
        # 不阻止，可能网络稍后恢复

    print_banner()

    if len(sys.argv) > 1:
        # 单次模式
        prompt = " ".join(sys.argv[1:])
        session = uuid.uuid4().hex[:8]
        reply, usage, elapsed = chat_once(prompt, session, 1, [])
        print()
        return

    # ---- 交互模式 ----
    session = uuid.uuid4().hex[:8]
    history = []  # [{role, content}, ...]
    session_prompt = 0
    session_completion = 0
    turn = 0

    while True:
        try:
            prompt = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not prompt:
            continue
        if prompt in ("/quit", "/exit", "/q"):
            break
        if prompt == "/new":
            session = uuid.uuid4().hex[:8]
            history = []
            session_prompt = 0
            session_completion = 0
            turn = 0
            print("  [new session: {}]".format(session))
            continue
        if prompt == "/status":
            print_status(session_prompt, session_completion, turn)
            continue
        if prompt == "/prompt":
            show_prompt_detail()
            continue
        if prompt == "/history":
            print("  [当前会话历史: {} 条消息]".format(len(history)))
            for h in history[-10:]:
                preview = h["content"][:50]
                if len(h["content"]) > 50:
                    preview += "…"
                print("    {}: {}".format(h["role"], preview))
            continue

        turn += 1
        reply, usage, elapsed = chat_once(prompt, session, turn, history)

        # 更新历史（只保留有内容的）
        history.append({"role": "user", "content": prompt})
        if reply:
            history.append({"role": "assistant", "content": reply})

        if usage:
            session_prompt += usage.get("prompt_tokens", 0)
            session_completion += usage.get("completion_tokens", 0)

    if turn > 0:
        print_session_summary(turn, session_prompt, session_completion)
    print("  bye")


if __name__ == "__main__":
    main()
