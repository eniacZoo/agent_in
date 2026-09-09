#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
llm.py — OpenAI 兼容 LLM 客户端（流式 + function calling）

零外部依赖，仅使用 stdlib。支持：
- 流式输出（SSE）
- Function Calling（tools 参数 + tool_calls 解析）
- Reasoning / Thinking 内容提取
- Token usage 统计
- v5.0 (H 系列)：
  - H1: 连接阶段自动重试 + 指数退避（retry.py；流读取阶段失败不重试，陷阱 B）
  - H2: per-model 限流（rate_limiter.py，key = providers.current_key()）
  - H4: 能力探测 + 缓存（capability.py；模块级 CAPABILITY 供 tools/agent 读）

依赖方向（防循环，见 plan5.0 §0.2/0.3）：
  llm → logger, config, providers, retry, rate_limiter, capability
  retry / rate_limiter / capability 互不 import；capability 不 import llm
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import logger
import config
import providers
import retry
import rate_limiter
import capability


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
BASE_URL = os.environ.get("BASE_URL", "https://api.deepseek.com").rstrip("/")
API_KEY = os.environ.get("API_KEY", "")
MODEL = os.environ.get("MODEL", "deepseek-v4.1-flash-expires-on-0910")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "8192"))
TIMEOUT = 600

# 连接健康跟踪
consecutive_failures = 0
CONSECUTIVE_FAILURE_THRESHOLD = 3

# H4: 当前模型能力（check_connection 填充），tools.py 读 llm.CAPABILITY 做降级
# 结构：{"tool_call": bool, "vision": bool, "max_context": int|None, "probed_at": ts}
CAPABILITY: dict = {}


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _headers():
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }


# ---------------------------------------------------------------------------
# 多模态：图片注入（v3.0）
# ---------------------------------------------------------------------------
def _inject_images(messages, images):
    """
    将图片（base64 data URL 列表）注入最后一条 user message，
    转为 OpenAI vision 的多模态数组 content 格式。

    不原地修改调用方传入的 messages（对每个 message 做浅拷贝），
    返回处理后的新 messages 列表。
    """
    if not images:
        return messages
    out = [dict(m) for m in messages]
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("role") == "user":
            out[i] = dict(out[i])
            original = out[i].get("content", "")
            if isinstance(original, list):
                # 已是数组（可能已含文本/图片），在其后追加新图
                parts = list(original)
            else:
                parts = [{"type": "text", "text": original if original else ""}]
            for url in images:
                parts.append({"type": "image_url", "image_url": {"url": url}})
            out[i]["content"] = parts
            return out
    # 兜底：找不到 user message 时追加一条
    out.append({
        "role": "user",
        "content": [{"type": "image_url", "image_url": {"url": u}} for u in images],
    })
    return out


# ---------------------------------------------------------------------------
# 核心接口
# ---------------------------------------------------------------------------
def chat(messages, tools=None, stream=True, model=None, max_tokens=None, images=None):
    """
    调用 LLM，返回 generator，逐块 yield Chunk dict。

    Chunk 格式：
      {"type": "reasoning", "content": "..."}   思考过程
      {"type": "text", "content": "..."}         正式回复
      {"type": "tool_call", "id": "...", "name": "...", "arguments": {...}}  工具调用
      {"type": "usage", "data": {...}}           token 统计
      {"type": "error", "content": "..."}        错误
      {"type": "done"}                           流结束

    参数：
      messages: OpenAI 格式 [{"role": "system", "content": "..."}, ...]
      tools: OpenAI function calling 格式 [{"type": "function", "function": {...}}]
      stream: 是否流式
      model: 覆盖默认模型
      max_tokens: 覆盖默认 max_tokens
      images: base64 data URL 列表，拼接到最后一条 user message（v3.0 多模态）
    """
    model = model or MODEL
    max_tokens = max_tokens or MAX_TOKENS
    t0 = time.time()

    # v3.0 多模态：将图片注入最后一条 user message
    if images:
        messages = _inject_images(messages, images)

    logger.info("llm_request", {
        "model": model,
        "messages_count": len(messages),
        "tools_count": len(tools) if tools else 0,
        "images_count": len(images) if images else 0,
    })

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": stream,
        "stream_options": {"include_usage": True},
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=_headers(),
        method="POST",
    )
    # 注（H1）：req.data 为 bytes、无 stateful 状态，连接阶段重发幂等安全

    # H2: per-model 限流（acquire 阻塞到满足最小间隔；key 对齐 providers，陷阱 C）
    on_429 = None
    if rate_limiter.enabled():
        key = providers.current_key(model=model)
        waited = rate_limiter.get_limiter(key).acquire()
        if waited > 0:
            logger.debug("rate_limited", {"key": key, "waited_s": round(waited, 3)})
        _key = key

        def on_429(retry_after, _k=_key):
            # 收到 429 → 解析 Retry-After 抬高该 model 间隔（不影响其他 model）
            rate_limiter.on_429(_k, retry_after)

    if not stream:
        yield from _chat_non_stream(req, on_429=on_429)
    else:
        yield from _chat_stream(req, on_429=on_429)


# ---------------------------------------------------------------------------
# H1: 连接阶段重试（陷阱 B：仅限「首 token 之前」）
# ---------------------------------------------------------------------------
def _sleep_backoff(attempt: int) -> None:
    """H1: 重试前指数退避 sleep（单测可 patch 加速）。"""
    delay = retry.backoff_delay(attempt)
    if delay > 0:
        time.sleep(delay)


def _open_with_retry(req, on_429=None):
    """
    H1: 建立连接（urlopen），**仅连接阶段重试**。
    返回 (resp, None) 成功；(None, err_chunk) 失败（err_chunk 为 error chunk）。

    重试条件：retry.is_retryable* 且 attempt < max_retries。
    每次重试前 backoff_delay(attempt) 秒 + logger.warn("llm_retry")。
    req 重发安全：data 为 bytes，无 stateful 副作用（幂等）。

    语义保持：重试成功 → consecutive_failures 归零；
    重试用尽/不可重试 → += 1 并触发阈值告警。
    """
    global consecutive_failures
    max_r = retry.max_retries()
    last_code = None
    last_body = ""
    for attempt in range(max_r + 1):
        try:
            resp = urllib.request.urlopen(req, timeout=TIMEOUT)
            consecutive_failures = 0
            return resp, None
        except urllib.error.HTTPError as e:
            last_code = e.code
            last_body = e.read().decode("utf-8", "replace")
            if on_429 is not None and e.code == 429:
                try:
                    on_429(e.headers.get("Retry-After") if e.headers else None)
                except Exception:
                    pass
            if retry.is_retryable_status(e.code) and attempt < max_r:
                _sleep_backoff(attempt)
                logger.warn("llm_retry", {"attempt": attempt, "status": e.code})
                continue
            break
        except Exception as e:  # URLError / Timeout / Connection 等
            last_code = None
            last_body = str(e)
            if retry.is_retryable_exception(e) and attempt < max_r:
                _sleep_backoff(attempt)
                logger.warn("llm_retry", {"attempt": attempt, "error": str(e)[:120]})
                continue
            break

    # 重试用尽 / 不可重试
    consecutive_failures += 1
    if last_code is not None:
        logger.error("llm_error", {"status": last_code, "error": last_body[:200]})
        err_chunk = {"type": "error", "content": f"HTTP {last_code}: {last_body[:500]}"}
    else:
        logger.error("llm_error", {"error": last_body[:200]})
        err_chunk = {"type": "error", "content": last_body}
    _check_consecutive_failures()
    return None, err_chunk


def _chat_non_stream(req, on_429=None):
    """非流式调用（H1：建连走重试；拿 resp 后读 body 失败不重试）。"""
    global consecutive_failures
    t0 = time.time()
    resp, err_chunk = _open_with_retry(req, on_429=on_429)
    if resp is None:
        yield err_chunk
        return
    try:
        with resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        # 连接已建立、body 传输失败 → 不重试（陷阱 B）
        consecutive_failures += 1
        logger.error("llm_error", {"error": str(e)[:200]})
        _check_consecutive_failures()
        yield {"type": "error", "content": str(e)}
        return

    logger.info("llm_response", {
        "elapsed_ms": int((time.time() - t0) * 1000),
        "non_stream": True,
    })

    choices = data.get("choices", [])
    if choices:
        msg = choices[0].get("message", {})
        # reasoning
        reasoning = msg.get("reasoning_content") or msg.get("thinking") or ""
        if reasoning:
            yield {"type": "reasoning", "content": reasoning}
        # text
        content = msg.get("content") or ""
        if content:
            yield {"type": "text", "content": content}
        # tool calls
        for tc in msg.get("tool_calls", []):
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {"_raw": fn.get("arguments", "")}
            yield {
                "type": "tool_call",
                "id": tc.get("id", ""),
                "name": fn.get("name", ""),
                "arguments": args,
            }

    # usage
    if data.get("usage"):
        yield {"type": "usage", "data": data["usage"]}

    yield {"type": "done"}


def _chat_stream(req, on_429=None):
    """流式调用，解析 SSE（H1：建连走重试；SSE 读取阶段失败不重试，陷阱 B）。"""
    global consecutive_failures
    t0 = time.time()
    resp, err_chunk = _open_with_retry(req, on_429=on_429)
    if resp is None:
        yield err_chunk
        return

    logger.debug("llm_stream_start", {"t0_ms": int(time.time() * 1000)})

    # 用于拼接 tool_call arguments（可能分多个 chunk 到达）
    pending_tool_calls = {}  # index -> {"id":..., "name":..., "args_parts": []}

    stream_ok = True
    try:
        with resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if not data_str or data_str == "[DONE]":
                    continue
                try:
                    obj = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                # --- usage（通常在最后一个 chunk）---
                if obj.get("usage"):
                    yield {"type": "usage", "data": obj["usage"]}

                choices = obj.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})

                # --- reasoning / thinking ---
                reasoning = (
                    delta.get("reasoning_content", "")
                    or delta.get("reasoning", "")
                    or delta.get("thinking", "")
                )
                if reasoning:
                    yield {"type": "reasoning", "content": reasoning}

                # --- text content ---
                content = delta.get("content", "")
                if content:
                    yield {"type": "text", "content": content}

                # --- tool_calls（可能分片）---
                tool_calls_delta = delta.get("tool_calls", [])
                for tc in tool_calls_delta:
                    idx = tc.get("index", 0)
                    if idx not in pending_tool_calls:
                        pending_tool_calls[idx] = {
                            "id": tc.get("id", ""),
                            "name": "",
                            "args_parts": [],
                        }
                    # 首次可能带 id
                    if tc.get("id"):
                        pending_tool_calls[idx]["id"] = tc["id"]
                    fn = tc.get("function", {})
                    if fn.get("name"):
                        pending_tool_calls[idx]["name"] = fn["name"]
                    if fn.get("arguments"):
                        pending_tool_calls[idx]["args_parts"].append(fn["arguments"])

    except Exception as e:
        # 流读取阶段失败（陷阱 B：可能已 yield 内容）→ 不重试，记 llm_error
        stream_ok = False
        consecutive_failures += 1
        logger.error("llm_error", {"error": str(e)[:200], "mid_stream": True})
        _check_consecutive_failures()
        yield {"type": "error", "content": f"流中断: {e}"}

    if not stream_ok:
        return

    # 流结束后，发出完整的 tool_calls
    for idx in sorted(pending_tool_calls.keys()):
        tc = pending_tool_calls[idx]
        args_str = "".join(tc["args_parts"])
        try:
            args = json.loads(args_str) if args_str else {}
        except json.JSONDecodeError:
            args = {"_raw": args_str}
        yield {
            "type": "tool_call",
            "id": tc["id"],
            "name": tc["name"],
            "arguments": args,
        }

    yield {"type": "done"}


# ---------------------------------------------------------------------------
# 连接健康
# ---------------------------------------------------------------------------
def _check_consecutive_failures():
    """连续失败超阈值时终端警告。"""
    global consecutive_failures
    if consecutive_failures >= CONSECUTIVE_FAILURE_THRESHOLD:
        logger.error("connection_lost", {"consecutive_failures": consecutive_failures})
        print(f"\n    \033[31m[!!] 连续 {consecutive_failures} 次连接失败，API 可能不可用\033[0m")


# ---------------------------------------------------------------------------
# 连通性检查
# ---------------------------------------------------------------------------
def check_connection(model=None, probe=None):
    """
    测试 API 连通性（H4：连通 OK 后懒加载能力探测），返回 (ok, msg, capability)。

    参数：
        model: 覆盖默认模型
        probe: None=按 config["auto_probe"]（默认 1）；True=强制重探；False=仅连通。

    返回：
        (ok: bool, msg: str, capability: dict)
        capability 结构见 CAPABILITY；未探测时为 {}。
        向后兼容：调用方仍可用 `ok, msg = ...` 取前两个，第三项按需取。
    """
    model = model or MODEL
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
        "stream": False,
    }
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=_headers(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            pass
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        logger.error("connection_lost", {"status": e.code, "body": body[:100]})
        if e.code == 401:
            return False, f"认证失败 (401): {body}", {}
        elif e.code == 404:
            return False, f"模型不存在 (404): {body}", {}
        else:
            return False, f"HTTP {e.code}: {body}", {}
    except Exception as e:
        logger.error("connection_lost", {"error": str(e)[:100]})
        return False, f"连接失败: {e}", {}

    logger.info("connection_ok", {"model": model})

    # H4: 连通 OK 后懒加载能力（有缓存不 probe）
    global CAPABILITY
    do_probe = (config.get("auto_probe", 1) in (1, True, "1", "true", "yes")) if probe is None else bool(probe)
    cap = {}
    if do_probe:
        force = bool(probe)
        ttl = float(config.get("capability_ttl_days", 7))
        try:
            if not force and capability.cache_valid(model, ttl):
                cap = capability.load_cache(model) or {}
                logger.info("capability_cache_hit", {"model": model})
            else:
                cap = capability.get(BASE_URL, API_KEY, model, force=force, ttl_days=ttl)
                logger.info("capability_probed", {
                    "model": model,
                    "tool_call": cap.get("tool_call"),
                    "vision": cap.get("vision"),
                    "max_context": cap.get("max_context"),
                })
        except Exception as e:
            # 探测失败不影响连通结果（优雅降级）
            logger.warn("capability_probe_failed", {"model": model, "error": str(e)[:120]})
            cap = {}
    CAPABILITY = cap
    return True, f"OK (model={model})", cap
