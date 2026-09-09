# agent_in v5.0 — 运行时 Harness 规划（H 系列）

> 分期开发计划。基准参照物：**szclaw / qwenpaw**（`providers/retry_chat_model.py`、`providers/rate_limiter.py`、`providers/multimodal_prober.py` + `capability_baseline.py` + `model_capability_cache.py`）。
> 原则：**保持零外部依赖（stdlib only）、保持 CLI 模式**。
> 本版本**只做 H1、H2、H4**，其余 H 系列（H3 hook / H5 取消 / H6 统一错误恢复）明确不做。

## 前置依赖

本 plan 建立在 **plan4.0（F 系列）已完成** 的基础上：
- `config.py` 存在（读 `shell_timeout`、`max_retries`、`rate_limit` 等）
- `providers.py` 存在（提供 `get_active()` 返回 `{base_url, api_key, model}`，per-model key = `f"{provider_name}:{model}"`）
- `logger.py` 已解耦（不 import ui）

## 概述

| # | 功能 | szclaw 参照 | agent_in 现状 | 目标 |
|---|------|-------------|---------------|------|
| H1 | Retry + 指数退避 | `providers/retry_chat_model.py` | 只计数 `consecutive_failures` 告警，**不重试** | 区分可重试/不可重试，自动重试 + 指数退避 + jitter |
| H2 | Rate Limiting | `providers/rate_limiter.py`（per `provider_id:model`） | 无 | per-model 最小间隔/令牌桶，防 429 风暴 |
| H4 | 启动自检 / 能力探测 | `multimodal_prober` + `capability_baseline` + `model_capability_cache` | plan3 手动测过 vision，未自动化 | 自动探测 tool_call / vision / 上下文上限，缓存 + 优雅降级 |

> 明确不做：H3（hook 点）、H5（Ctrl-C 取消 / 任务超时）、H6（统一错误恢复策略）。

---

## 0. 解耦与耦合关系梳理（动手前必读）

> 用户要求：涉及拆分时梳理清楚耦合，解耦不得有功能遗漏 / 调用关系遗漏。

### 0.1 plan4 完成后的依赖图（本 plan 的基线）

```
agent.py ──→ llm, tools, ui, vision, memory_manager, skill_manager, logger,
             config, providers, session, context, usage
llm.py   ──→ logger
tools.py ──→ logger, skill_manager, vision
logger.py──→ （无 ui，plan4 已解耦；叶子方向）
config/providers/session/context/usage ──→ 叶子
```

### 0.2 本 plan 的改造集中在 `llm.py`，新增 3 个叶子

```
新增叶子模块（不 import 任何本地模块，零依赖）：
  retry.py         H1  可重试判定 + 退避计算（纯函数，无副作用，可单测）
  rate_limiter.py  H2  per-model 最小间隔 / 令牌桶
  capability.py    H4  能力探测 + 缓存读写

改造模块（仅 1 个）：
  llm.py           chat() 套 retry + rate_limit；新增 probe_capabilities()；
                   check_connection() 扩展为「连通 + 能力」入口
```

**依赖方向（关键，防循环）**：
```
llm.py ──→ logger, retry, rate_limiter, capability
retry / rate_limiter / capability ──→ （各自独立叶子，互不 import）
```
`retry`/`rate_limiter`/`capability` 之间**互相不 import**（capability 探测若要发请求，由 llm.py 传入可调用对象，**不直接 import llm**，避免 `llm ↔ capability` 循环，见 0.3）。

### 0.3 三个耦合陷阱（拆 llm 时必守）

**陷阱 A：capability 探测不能 `import llm`**
- 能力探测要发 HTTP 请求，若 `capability.py` 直接 `import llm` 复用 `chat()`，而 `llm.py` 又 `import capability`（调 `probe_capabilities`）→ **循环 import**。
- 处置：`capability.py` 提供**自包含的最小请求函数**（自己用 `urllib` 发探测请求，读 `providers.get_active()`），或 llm.py 通过**依赖注入**把底层 `urlopen` 请求函数传进去。本 plan 选前者（capability 自带最小请求，代码 ~40 行），保持 llm 与 capability 单向：`llm → capability`。
- 验收：`grep "import llm" capability.py` 无结果。

**陷阱 B：retry 只能在「尚未产出内容」时触发**
- `llm.chat()` 是 generator，边发边 `yield`。若流已开始 yield 了 `text`/`tool_call` 给 agent 后再失败，自动重发会让 agent 收到**重复内容**（assistant 消息重复、tool 重复执行）。
- 处置：**retry 只覆盖「连接建立阶段」+「首 token 之前」的失败**（即 `urlopen` 返回前 / 首个 data 到达前）。一旦 `yield` 出任何 `text`/`tool_call` chunk，后续流中断 → 记 `llm_error`，**不自动重试**，交由上层/用户（对齐幂等 + 不重复原则）。
- 落点：在 `_chat_stream` / `_chat_non_stream` 内部，`urlopen` 处包一个 `for attempt in range(max_retries+1)` 循环，**只有 `emitted_any=False` 时才允许重试**。

**陷阱 C：rate limiter 的 per-model key 与 providers 对齐**
- szclaw 用 `provider_id:model_name` 作 key，保证「一个 model 限流不影响其他 model」。
- 处置：key = `providers.current_key()`（plan4 的 providers 返回 `{name, model}`，拼成 `f"{name}:{model}"`）。若 plan4 的 providers 未暴露 name，本 plan 在 `providers.py` 补一个 `current_key()`（小改，不破坏现有接口）。
- 验收：切 `--provider backup` → 用 backup 的 limiter 槽，与 default 互不阻塞。

### 0.4 拆分后目标依赖图（H 系列完成后）

```
agent.py ──→ ...(plan4 不变) + llm(能力结果用于降级提示)
llm.py   ──→ logger, retry, rate_limiter, capability
retry.py / rate_limiter.py / capability.py ──→ 叶子（互不依赖）
tools.py / 其余 ──→ 不变
```

---

## Phase 1（H1）：Retry + 指数退避

### 1.1 新增 `retry.py`（叶子，纯函数，可单测）

```python
# 可重试判定
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
def is_retryable_status(status: int) -> bool
def is_retryable_exception(exc) -> bool
    # 网络类可重试：URLError / ConnectionError / TimeoutError / socket.timeout
    # HTTPError → 转 is_retryable_status(e.code)
    # 4xx（除 429）/ 鉴权 401/403 → 不可重试
def backoff_delay(attempt: int, base=1.0, cap=30.0, jitter=True) -> float
    # delay = min(cap, base * 2**attempt) (+ 0~0.3*delay 的 jitter)

# 配置
def max_retries() -> int     # 读 config.get("max_retries", 3)
```

### 1.2 改造 `llm.py`（`_chat_stream` / `_chat_non_stream`）

**当前结构**（保持其余逻辑不变）：
- `_chat_non_stream(req)`：`urlopen` → 成功 yield 结果；HTTPError/异常 → `consecutive_failures += 1` + yield error。
- `_chat_stream(req)`：`urlopen` → 逐行读 SSE；中途异常 → yield error。

**改造**：把「建连」抽成带重试的循环。

```python
def _open_with_retry(req):
    """
    建立连接（urlopen）。仅在此阶段重试；返回 (resp, None) 或 (None, err_chunk)。
    重试条件：is_retryable 且 attempt < max_retries。
    每次重试前 backoff_delay(attempt) 秒 sleep + logger.warn("llm_retry")。
    """
    max_r = retry.max_retries()
    last_err = None
    for attempt in range(max_r + 1):
        try:
            resp = urllib.request.urlopen(req, timeout=TIMEOUT)
            consecutive_failures = 0
            return resp, None
        except urllib.error.HTTPError as e:
            last_err = ("http", e)
            if e.code in retry.RETRYABLE_STATUS and attempt < max_r:
                _sleep_backoff(attempt); logger.warn("llm_retry", {"attempt": attempt, "status": e.code}); continue
            break
        except Exception as e:   # URLError/Timeout/Connection
            last_err = ("net", e)
            if retry.is_retryable_exception(e) and attempt < max_r:
                _sleep_backoff(attempt); logger.warn("llm_retry", {"attempt": attempt, "error": str(e)[:120]}); continue
            break
    # 重试用尽 / 不可重试
    consecutive_failures += 1
    _check_consecutive_failures()
    return None, _err_chunk(last_err)

# _chat_non_stream: 用 _open_with_retry；None 则 yield err_chunk
# _chat_stream:     用 _open_with_retry 拿 resp；之后进入 SSE 读取
#                   —— 读取阶段（已拿到 resp=已开始流）失败 → 不重试，记 llm_error + yield error
```

**关键**：`_chat_stream` 拿到 `resp` 后进入 SSE 循环，**此阶段失败不重试**（陷阱 B）。`urllib.request.Request` 对象重发无副作用（幂等），所以连接阶段重发是安全的。

### 1.3 配置项（config.py 白名单补 2 个 key）
- `max_retries`（默认 3）
- `retry_base_delay`（默认 1.0）

### 1.4 开发任务（Phase 1）
- [x] `retry.py`：is_retryable_status/exception + backoff_delay + max_retries
- [x] `llm.py`：抽 `_open_with_retry()`；`_chat_stream`/`_chat_non_stream` 接入；**流读取阶段失败不重试**
- [x] `llm.py`：重试时 `req` 需可复用 —— `urllib.request.Request` 的 data 是 bytes，重发 OK；确认无 stateful 副作用
- [x] `config.py`：白名单加 `max_retries`/`retry_base_delay`
- [x] 纯函数冒烟验证：`is_retryable_status`(429/5xx 可、401/404 不可)、`is_retryable_exception`(Timeout/Connection 可、401 不可)、`backoff_delay` 递增（d0≈1.0 < d3≈9.6）、`max_retries()=3`
- [ ] 验证（**需 mock 服务端**，活测待跑）：
  - [ ] mock 一个前 2 次 503、第 3 次成功的端点 → agent 正常拿到结果，日志有 2 条 `llm_retry`
  - [ ] 401 → 不重试，直接报错（不浪费时间）
  - [ ] 流中途断 → 不重复 yield，记 `llm_error`
  - [ ] `consecutive_failures` 语义保持（重试成功仍归零）

**Phase 1 成功标准**：
```bash
# 服务端偶发 503
python agent.py "你好"
  ⚠️ [12:00:01] llm_retry attempt=0 status=503
  ⚠️ [12:00:03] llm_retry attempt=1 status=503
  ✅ 正常回复（2 次退避后成功）
```

---

## Phase 2（H2）：Rate Limiting

### 2.1 新增 `rate_limiter.py`（叶子）

对齐 szclaw per-model：每个 `provider:model` 一个 limiter 实例，`429` 只影响该 model。

```python
class ModelRateLimiter:
    def __init__(self, min_interval: float):  # 两次请求最小间隔
        self._last = 0.0; self._lock = threading.Lock()
    def acquire(self):                         # 阻塞到满足间隔；返回等待秒数
        with self._lock:
            now = time.monotonic()
            wait = self._last + self.min_interval - now
            if wait > 0: time.sleep(wait)
            self._last = time.monotonic()
            return max(0.0, wait)

_limiters: dict[str, ModelRateLimiter] = {}
def get_limiter(key: str) -> ModelRateLimiter  # key = "provider:model"，惰性创建
def enabled() -> bool                            # 读 config.get("rate_limit", 0) > 0
def on_429(key: str, retry_after: float | None):  # 收到 429 时临时抬高该 model 间隔
```

> 极简版用「最小间隔」而非完整令牌桶（够防 429 风暴）。`on_429` 配合 H1：收到 429 → 抬高间隔 + 重试。

### 2.2 改造 `llm.py`
- `chat()` 发请求前：`if rate_limiter.enabled(): wait = rate_limiter.get_limiter(providers.current_key()).acquire()`（wait>0 时 DEBUG 日志）。
- 收到 429（H1 的连接阶段）：`rate_limiter.on_429(key, retry_after_header)` → 解析 `Retry-After` 头抬高间隔。

### 2.3 配置项
- `rate_limit`（默认 0=关闭；设 0.5 表示同 model 两次请求间隔 ≥0.5s）

### 2.4 开发任务（Phase 2）
- [x] `rate_limiter.py`：ModelRateLimiter(min_interval/锁) + acquire + bump + get_limiter + enabled + on_429
- [x] `providers.py`：补 `set_active_name()` + `current_key(model=None)`（`f"{name}:{model}"`）——小改，不破坏现有接口
- [x] `llm.py`：chat 前 `acquire`；429 → `on_429(key, Retry-After)` 闭包串到 `_open_with_retry`
- [x] `agent.py`：启动 + `/provider` 切换时 `providers.set_active_name(...)`，保证限流 key 随 provider 更新
- [ ] 验证（**需 mock 服务端**，活测待跑）：
  - [ ] `rate_limit=0.5` 连发 3 个 prompt → 时间戳间隔 ≥0.5s
  - [ ] mock 429 + Retry-After:2 → 该 model 间隔临时升高，其他 model 不受影响
  - [ ] 关闭（默认 0）→ 行为与无 rate limit 一致（回归）
  - [x] 结构性验证：`current_key()` 切 provider 后随 name 变化（`default:M` → `other:M`）；`enabled()` 在 `rate_limit=0` 时为 False（默认关闭不改变原行为）

**Phase 2 成功标准**：
```bash
# 高频连续任务不触发服务端 429
python agent.py "批量处理这 10 个文件"
  # 内部每个 LLM 调用自动限速，无 429
```

---

## Phase 3（H4）：启动自检 / 能力探测 + 优雅降级

### 3.1 新增 `capability.py`（叶子，自带最小请求，见陷阱 A）

```python
# 探测结果结构
# {"tool_call": True/False, "vision": True/False, "max_context": int|None, "probed_at": ts}

def probe(base_url, api_key, model, timeout=15) -> dict
    # 1) tool_call：发 {"messages":[{"role":"user","content":"ping"}],
    #    "tools":[最小单参数tool], "tool_choice":"auto", "max_tokens":1}
    #    - 返回结构正常（有 choices，未报 tools-unsupported 类 400）→ True
    #    - 报 "tools is not supported" 类错误 → False
    # 2) vision：发 1x1 PNG base64 image_url，max_tokens=1
    #    - 正常返回 → True；报 image-unsupported 400 → False
    # 3) max_context：可选，从 400 错误的 "context_length_exceeded" 里解析；取不到 → None
    # 全程 try/except，单项失败记该 False，不抛

def load_cache(model) -> dict | None      # 读 capability_cache.json
def save_cache(model, cap) -> None        # 原子写
def get(base_url, api_key, model, force=False, ttl_days=7) -> dict
    # 有缓存且未过期 → 返回缓存；否则 probe + save_cache
```

**缓存**：`capability_cache.json`（项目根）。key = model，带 `probed_at`，TTL 默认 7 天（`capability_ttl_days`）。

### 3.2 改造 `llm.py`
- 扩展 `check_connection()`：连通 OK 后**懒加载**能力（有缓存不 probe）。返回 `(ok, msg, capability)`。
- 保持现有 `check_connection()` 的调用方（agent.py）签名兼容：新增返回值字段，agent 侧按需取。

### 3.3 改造 `agent.py`（优雅降级接线）
- 启动时：`ok, msg, cap = llm.check_connection(...)`。
- `cap["vision"] == False` → `view_image` 工具执行时返回「当前模型不支持图像分析」降级提示（tools.py 的 `_exec_view_image` 读全局 `llm.CAPABILITY`）。
- `cap["tool_call"] == False` → system prompt 追加强提示 + （可选）降级 ReAct 文本协议（plan1 提过；本 plan 先做「提示 + 仍尝试 tools」，完整 ReAct 降级列为 P2）。
- `cap["max_context"]` 非空 → 覆盖 `config` 的 `context_limit`（更准）。
- 交互命令 `/probe [force]`：手动重探 + 显示能力。

### 3.4 配置项
- `capability_ttl_days`（默认 7）
- `auto_probe`（默认 1；0 = 不自动探测，仅连通检测）

### 3.5 开发任务（Phase 3）
- [x] `capability.py`：probe（tool_call/vision/max_context）+ load/save_cache + get（TTL）+ cache_valid
- [x] `capability.py` **不 import llm**（自带 urllib 最小请求）—— 验收 `grep import llm capability.py` 空（仅注释提及）
- [x] `llm.py`：`check_connection(model, probe)` 扩展返回 `(ok, msg, capability)`；模块级 `CAPABILITY` 供 tools 读
- [x] `agent.py`：启动/`run_single`/`run_interactive`/`/provider` 均接 `cap`；`_apply_capability`（max_context 覆盖 + tool_call 降级强提示）；`/probe [force]`；`--probe` CLI
- [x] `tools.py`：`_exec_view_image` 读 `llm.CAPABILITY["vision"]`，False 时返回降级提示（函数内延迟 import，不破坏正常路径）
- [ ] 验证（**需真实 LLM 端点**，活测待跑）：
  - [ ] AngelOrDevil → probe 得 `tool_call=True, vision=True`（与 plan3 一致）
  - [ ] 换纯文本 model → `vision=False`，`view_image` 返回降级提示，不报错
  - [ ] 有缓存 → 二次启动不重新 probe（日志 `capability_cache_hit`）
  - [ ] `--probe`/`/probe force` → 强制重探
  - [x] 结构性验证：capability API 齐全（probe/load/save/cache_valid/get）；`check_connection` 探测失败优雅降级不影响连通结果（try/except 兜底 `cap={}`）

**Phase 3 成功标准**：
```bash
python agent.py            # 启动自动探测（有缓存则跳过）
> /probe
  模型 AngelOrDevil：tool_call ✅  vision ✅  max_context 196000
> 看看 xxx.png
  ⚠️ 当前模型不支持图像分析（已自动降级）   # 换纯文本模型时
```

---

## 风险 & 注意

| 项 | 风险 | 应对 |
|----|------|------|
| 陷阱A 循环 import | capability↔llm | capability 自带最小请求，不 import llm；`grep` 验收 |
| 陷阱B 重复内容 | 流中断后重试导致 assistant/tool 重复 | retry 仅限「首 token 前」；流读取阶段失败不重试 |
| 陷阱C 限流串扰 | 一个 model 429 拖垮其他 | per-model key 对齐 providers.current_key() |
| Request 重发副作用 | urllib Request 重发 | data 为 bytes，无 stateful，幂等安全；确认无自定义 state |
| 退避过久卡住 | 3 次退避最坏 ~7s+ | cap=30s；`max_retries` 默认 3 可配；连接超时另计 |
| 探测吃 token | probe 发真实请求 | max_tokens=1，单 model 缓存 7 天；`auto_probe=0` 可关 |
| 探测误判 | 端点行为不标准 | 单项失败记 False 不抛；保守：拿不准 tool_call 记 True（宁可尝试）|
| 向后兼容 | 老配置无新 key | 新 key 均有默认（rate_limit=0 关、max_retries=3）；无配置行为不变 |

## 开发顺序总览

```
Phase 1 (H1)  Retry + 指数退避        ← 稳定性核心，先做
   ↓
Phase 2 (H2)  Rate Limiting（per-model）← 依赖 providers.current_key()
   ↓
Phase 3 (H4)  能力探测 + 优雅降级        ← 依赖 providers + 缓存
```
> Phase 1/2 都改 llm.py 的 `_open_with_retry`/chat，**建议先 1 后 2**（2 的 429 处理挂接 1 的重试路径）。Phase 3 独立。每个 Phase 独立可交付、可回归。

## 完成后项目结构（v5.0）

```
agent_in/
├── agent.py             # 入口（+ capability 接线 / view_image 降级 / /probe）
├── llm.py               # LLM 客户端（+ _open_with_retry / rate limit / check_connection 扩展）
├── retry.py             # 新建 H1 重试
├── rate_limiter.py      # 新建 H2 限流
├── capability.py        # 新建 H4 能力探测
├── config.py providers.py session.py context.py usage.py  # plan4
├── tools.py ui.py vision.py skill_manager.py memory_manager.py logger.py  # 不变（tools 小改 view_image 降级）
├── capability_cache.json # 运行时（H4）
└── memory/ logs/ skills/ sessions/ usage/
```
