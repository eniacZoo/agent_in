# agent_in v4.0 — 功能增强规划（F 系列）

> 分期开发计划。基准参照物：**szclaw / qwenpaw**（`/opt/szclaw/Python-3.12.12/lib/python3.12/site-packages/qwenpaw/`）。
> 原则：**保持零外部依赖（stdlib only）、保持 CLI 模式、不封装 App**。
> 本版本落地 F1–F5 五项功能优化。

## 概述

在 v3.0（Agent 循环 + 5 工具 + Skill + Memory + 日志 + 流式回显 + 多模态）基础上，补齐「工程化」能力，对齐 szclaw 的对应功能点：

| # | 功能 | szclaw 参照 | agent_in 现状 | 目标 |
|---|------|-------------|---------------|------|
| F1 | 配置文件系统 | `config/config.py`(69KB) + `config/context.py` | 全是环境变量，无配置文件 | 引入 `agent_config.json`，优先级 CLI > 配置 > 环境变量 |
| F2 | 会话持久化 | `app/inbox*` / `app/trace_store` / `app/workspace` | 进程重启会话即丢失 | `sessions/<id>.json` + `/resume`，崩溃可恢复 |
| F3 | 多 Provider / 模型切换 | `providers/provider_manager` + 6 家 provider + `model_capability_cache` | 单 provider 硬编码 | 配置里多 profile + `--provider` 切换 |
| F4 | 上下文自动管理 | `agents/context` | 只有 70%/90% 预警，无自动处理 | 逼近上限自动摘要 / 滑动窗口截断 tool_result |
| F5 | Token/成本聚合 | `token_usage` 模块 | 仅本轮 token | 会话级聚合 + 跨会话成本持久化 |

> 明确不做：**API Key 加密**（按用户要求保持明文），本版本不引入 `cryptography` 等加密依赖。

---

## 0. 解耦与耦合关系梳理（动手前必读）

> 用户要求：涉及现有文件拆分时，必须梳理清楚文件间耦合，解耦不得有功能遗漏或调用关系遗漏。
> 本节是**当前真实依赖图 + 迁移约束**，四个 Phase 的拆分都必须遵守。

### 0.1 当前 import 依赖图（实测）

```
agent.py ──→ llm, ui, tools, vision, memory_manager, skill_manager, logger
llm.py   ──→ logger
tools.py ──→ logger, skill_manager, vision
logger.py──→ ui        ← ★ 基础设施层反向依赖表现层（颜色），见 0.3 陷阱A
ui.py    ──→ （无本地依赖，叶子）
vision.py──→ （叶子）
skill_manager.py ──→ （叶子，运行时 subprocess 调 skill 脚本）
memory_manager.py ──→ （叶子）
```

### 0.2 跨模块共享符号清单（拆分时**一个都不能丢**）

`agent.py` 实际调用的外部符号（grep 实测）：

- `llm`：`chat`, `check_connection`, `MODEL`, `BASE_URL`
- `tools`：`TOOLS`, `execute`, `drain_pending_images`, `_resolve_path`(私有，被 agent 直接引用), `WORK_DIR`(被 agent **赋值**)
- `ui`：`STREAM_CODE`, `SPINNER_ENABLED`, `fmt_tokens`, `_color`(私有), `C_RED/C_YELLOW/C_GRAY/C_GREEN/C_CYAN/C_RESET`(私有常量), `turn_sep`, `StreamDisplay`, `turn_footer`, `preview_code`, `preview_edit`, `Spinner`, `print_image_loaded`, `print_error`, `print_banner`, `turn_header`, `print_summary`, `confirm`
- `vision`：`image_info`, `image_to_base64`
- `memory_manager`：`load`（交互模式下还有 `append/delete/clear/get_full_content`）
- `skill_manager`：`reload_tools`（`tools.py` 内部用）、交互命令用 `list_skills/save_skill/delete_skill`
- `logger`：`info`, `warn`, `error`, `close`

`tools.py` 内部：`BASE_TOOLS`(read/write/edit/shell/view_image 5 个 schema) + `reload_tools()` 动态合并 `skill_manager.to_tool_schemas()` → 产出 `TOOLS`；`PENDING_IMAGES` 全局 + `drain_pending_images()` 是多模态图片的唯一通道。

`llm.py` 内部：`consecutive_failures` 全局 + `_check_consecutive_failures()` + `check_connection()`（**已存在**，F4/H 系列直接复用）。

### 0.3 两个耦合陷阱（拆文件必守）

**陷阱 A：`logger.py` 反向 import `ui`**（只用了 `ui._color` / `ui.C_YELLOW` / `ui.C_RED` 三个符号）。
- 风险：`logger` 是底层设施，`ui` 是表现层，方向反了。后续 F2 若让 `session.py` 也 `import logger` 没问题，但若哪天 `ui.py` 想 `import logger` 记日志 → **循环 import 崩溃**。
- 处置（F1 顺带做，成本极低）：在 `ui.py` 里已有颜色常量；把 logger 需要的最小 ANSI 能力**内联进 `logger.py`**（一个 `__init__` 里根据 `COLOR` 环境变量算 `C_YELLOW/C_RED` 两个串 + 一个 3 行的 `_c(text,color)`），然后**删掉 `logger.py` 的 `import ui`**。
- 验收：`grep "import ui" logger.py` 无结果；`logger` 仍可彩色打印 WARN/ERROR。

**陷阱 B：运行期改写全局 `tools.WORK_DIR` + 模块级路径常量**
- `agent.run_once` / `agent.interactive` 在入口执行 `tools.WORK_DIR = work_dir`（agent.py:295/337）。
- 但 `skill_manager.SKILLS_DIR`、`memory_manager.MEMORY_DIR`、`logger.LOG_DIR` 都在各自 **import 期**用 `__file__` 锁定，**不随 WORK_DIR 变**（这是对的：skill/memory/log 固定在项目目录，不随工作目录漂移）。
- 约束：**新引入的 `config.py` / `providers.py` 读取配置与定 `work_dir` 的动作，必须发生在 agent 入口最顶部，早于任何依赖 work_dir 的模块被真正使用**。`config.py` / `providers.py` 本身是叶子（不依赖其他本地模块），可安全放在最底层，不产生循环。

### 0.4 拆分后目标依赖图（F 系列完成后）

```
新增叶子模块（不 import 任何本地模块，零依赖，可放最底层）：
  config.py        F1  配置加载（env + agent_config.json，带缓存）
  providers.py     F1/F3 provider profile 注册表 + get_active()
  session.py       F2  会话持久化（save/load/list/resume）
  context.py       F4  上下文管理（summarize/trim）
  usage.py         F5  token/成本聚合（含跨会话持久化）

改造模块：
  logger.py        去掉 import ui（陷阱A）
  llm.py           读 providers.get_active() 取 BASE_URL/API_KEY/MODEL；保留模块常量做默认兜底（陷阱B：保持向后兼容）
  agent.py         入口先 config.load()；接 session/context/usage；新增 /resume
  tools.py         不变（WORK_DIR 赋值时机不变）
```

新叶子模块之间**互相不 import**（`config`/`providers`/`session`/`context`/`usage` 彼此独立），避免新循环。若 `session.py` 需要记日志，`import logger` 是允许的（logger 已是叶子方向）。

---

## Phase 1（F1 + F3）：配置系统 + 多 Provider

> 先做配置，因为 F2/F4/F5 的参数都要从配置读。F3 与 F1 天然绑定（provider 就是配置里的一组 profile）。

### 1.1 新增 `config.py`（叶子）

**职责**：单一事实来源（single source of truth）。优先级 `CLI 参数 > agent_config.json > 环境变量 > 内置默认`。

**`agent_config.json`（项目根，运行时生成；不存在则用默认）**：
```json
{
  "work_dir": "",
  "provider": "default",
  "max_tokens": 8192,
  "context_limit": 196000,
  "shell_timeout": 60,
  "log_level": "INFO",
  "safe_mode": false,
  "safe_mode": false,
  "max_tool_iterations": 20,
  "providers": {
    "default": {"base_url": "http://118.4.78.6:8088/api/v1", "api_key": "sk-...", "model": "AngelOrDevil"},
    "backup":  {"base_url": "http://127.0.0.1:11434/v1",       "api_key": "ollama", "model": "qwen2.5:14b"}
  }
}
```

**对外接口**（叶子，内部用 `functools.lru_cache` 或模块级 `_loaded` 缓存）：
```python
def load(cli_overrides: dict | None = None) -> dict   # 合并三层，返回扁平 dict
def get(key: str, default=None)                       # 供各模块按需取
def path() -> Path                                     # agent_config.json 绝对路径
```

**规则**：
- 文件不存在 → 用环境变量 + 默认，不报错；`/config` 命令可首次落盘。
- 只认白名单 key，未知 key 忽略并 DEBUG 日志。
- **保持零依赖**：`json` + `os` + `pathlib`。

### 1.2 新增 `providers.py`（叶子）

**职责**：provider profile 注册表。对齐 szclaw `provider_manager` 的「多 provider + 能力缓存」思路，但极简。

**对外接口**：
```python
def get_all() -> dict                       # 读 config 的 "providers"
def get_active(name: str | None = None) -> dict  # 返回 {base_url, api_key, model}，默认取 config["provider"]
def list_names() -> list[str]
def apply_to_llm(p: dict) -> None           # 把 provider 写回 llm 模块的 BASE_URL/API_KEY/MODEL
```

**与 `llm.py` 的对接（关键，防遗漏）**：
- `llm.py` **保留**模块常量 `BASE_URL/API_KEY/MODEL` 作为默认兜底（agent.py 仍读 `llm.MODEL`/`llm.BASE_URL` 做 banner 展示 → **接口不破坏**）。
- agent 入口在 `config.load()` 后调用 `providers.apply_to_llm(providers.get_active(name))`，改写 `llm.BASE_URL/API_KEY/MODEL`。
- `llm.check_connection(model=...)`（已存在）复用，无需改动。
- 这样 llm.py 内部逻辑（`_headers`/`chat`）零改动，只是常量被入口刷新一次。

### 1.3 改造 `agent.py`
- 入口最顶：`config.load(cli_overrides)` → `providers.apply_to_llm(...)` →（保持）`tools.WORK_DIR = work_dir`。**顺序固定**：config → providers → tools.WORK_DIR。
- 新增 CLI：`--provider NAME`、`--config PATH`。
- `CONTEXT_LIMIT` 改从 `config.get("context_limit")` 取（不再硬编码 196000）。
- `MAX_TOOL_ITERATIONS` 从 `config.get("max_tool_iterations")` 取。
- 新增交互命令：`/config`（显示当前生效配置）、`/provider [name]`（查看/切换，切换后 `apply_to_llm` + 重连检测）。

### 1.4 改造 `logger.py`（陷阱 A 处置）
- 删 `import ui`；内联最小 ANSI（`C_YELLOW`/`C_RED`/`_c()`），行为不变。

### 1.5 开发任务（Phase 1）
- [ ] `config.py`：load/get/path + 三层合并 + 白名单 + 缓存
- [ ] `providers.py`：get_all/get_active/apply_to_llm
- [ ] `llm.py`：确认常量可被 `apply_to_llm` 覆盖（不改内部逻辑）
- [ ] `agent.py`：入口接线（config→providers→tools.WORK_DIR 顺序）、`--provider/--config`、`/config`、`/provider`
- [ ] `logger.py`：去 `import ui`，内联颜色
- [ ] 验证：
  - [ ] 不写 `agent_config.json` → 行为与 v3.0 完全一致（回归）
  - [ ] 写配置切 `provider=backup` → banner 显示 backup 的 model/base_url，请求打到 backup
  - [ ] `grep "import ui" logger.py` 无结果；`python -c "import logger"` 无循环 import
  - [ ] `--provider x` 覆盖配置文件里的 `provider`

**Phase 1 成功标准**：
```bash
python agent.py --provider backup "你好"   # 走 backup profile
python agent.py                             # 交互
> /config         # 打印生效配置（脱敏 api_key 显示前6位+***）
> /provider       # 列出 profile
> /provider default  # 切换
```

---

## Phase 2（F2）：会话持久化

### 2.1 新增 `session.py`（叶子）

**职责**：把「system 之外的 messages 列表 + 会话元数据」序列化/恢复。对齐 szclaw `trace_store`（记录轨迹）思路。

**存储**：`sessions/<session_id>.json`（session_id 用已有的 uuid）。
```json
{
  "session_id": "abc123",
  "created": "2026-09-09T14:00:00",
  "updated": "2026-09-09T14:20:00",
  "provider": "default",
  "model": "AngelOrDevil",
  "work_dir": "/abs/path",
  "total_prompt": 12345,
  "total_completion": 678,
  "messages": [ {"role":"user","content":"..."}, {"role":"assistant",...}, ... ]
}
```
> 注意：`messages` **不含** system（system 由 prompt+memory 动态拼），只存对话轨迹，恢复时重新拼 system，保证 memory 更新能生效。

**对外接口**：
```python
def save(session_id, messages, meta=None) -> Path
def load(session_id) -> dict | None          # 返回 {"messages":..., "meta":...}
def list_sessions(limit=20) -> list[dict]    # 按 updated 倒序，含首条 user 摘要
def latest() -> str | None                   # 最近一个 session_id
def delete(session_id) -> bool
```

### 2.2 改造 `agent.py`
- `agent_loop` 每轮工具执行后 / 每轮结束，调用 `session.save(session_id, messages, meta)`（**增量保存**，防崩溃丢轨迹）。meta 带累计 token（接 F5）。
- 单次任务 `run_once` 结束也 save（便于 `-r` 续跑）。
- 新增 CLI：
  - `--resume / -r [session_id]`：不带 id 用 `session.latest()`。恢复后把 `messages` 接回主循环。
  - `--session-id`：手动指定（高级）。
- 交互命令：`/resume [id]`（列出最近 5 个供选择）、`/sessions`（列表）、`/save`（强制存当前）。
- `/new`（已有）→ 同时生成新 session_id 并开始新文件。

### 2.3 开发任务（Phase 2）
- [ ] `session.py`：save/load/list/latest/delete（原子写：先写 `.tmp` 再 `os.replace`，防写一半损坏）
- [ ] `agent.py`：`agent_loop` 增量 save；`run_once` 结束 save；`-r/--resume`、`/resume`、`/sessions`
- [ ] 恢复时重建 system（prompt+memory），校验 messages 里 tool_call/tool 配对完整（残缺则截断到最后一个完整 assistant 边界）
- [ ] 验证：
  - [ ] 跑一段多轮 → 中途 Ctrl-C → `-r` → 上下文完整继续
  - [ ] `/sessions` 列出、`/resume` 恢复
  - [ ] 损坏的 json（手动截断）→ 友好报错 + 自动备份为 `.corrupt`，不崩

**Phase 2 成功标准**：
```bash
python agent.py "创建 a.py 打印 hello"   # session 自动保存
python agent.py -r                        # 续跑，记得刚才建了 a.py
python agent.py                            # 交互
> /sessions
> /resume <id>
```

---

## Phase 3（F4）：上下文自动管理

> 目标：把「只预警」升级为「预警 + 自动处理」。对齐 szclaw `agents/context` 的压缩/截断能力。
> 依赖 F1（`context_limit`、阈值都从 config 读）。

### 3.1 新增 `context.py`（叶子）

**策略（由轻到重，逐级触发）**：
1. **工具结果截断**（最轻，无 LLM 成本）：`full_messages` 中历史 `role=tool` 的超长 content（> 2000 字符）替换为「前 500 字符 + …(truncated N chars)」。只对**非最近一轮**的 tool 结果做，保留最新上下文完整。
2. **历史轮次摘要**（有 LLM 成本，可选开关 `auto_summarize`）：当估算 token > 阈值（默认 `context_limit * 0.8`），把**最旧的 N 轮**（保留最近 K 轮 + 首条 user）合并，调一次 LLM 摘要成一段「之前的进展：…」，替换原文。
3. **滑动窗口兜底**（最后手段）：仍超 90% → 只保留 system + 最近 K 轮，硬截断，并 log `context_overflow`。

**对外接口**：
```python
def plan(messages, used_tokens, limit, recent_keep=6) -> str   # 返回将执行的动作(用于日志/预览)
def trim_tool_results(messages, keep_last=1) -> tuple[list,int]  # 返回新列表 + 节省字符数
def summarize_old(messages, llm_chat_fn, old_slice) -> str     # 调 llm_chat_fn 生成摘要（依赖注入，避免 import llm）
def apply(messages, used_tokens, limit, llm_chat_fn=None, auto_summarize=True) -> tuple[list, str]
#   返回 (处理后的 messages, 动作描述)
```
> **依赖注入** `llm_chat_fn`：`context.py` 不 `import llm`（保持叶子），由 agent 传入 `llm.chat` 的可调用对象。这样依赖图不被污染。

### 3.2 改造 `agent.py`
- 每轮 LLM 调用**前**：`full_messages, action = context.apply(full_messages, last_prompt_tokens, CONTEXT_LIMIT, llm_chat_fn=llm.chat)`；若有 action → `logger.info("context_manage", {"action": action})` + 终端灰字提示「已压缩 N 轮历史」。
- 与现有 `_check_context` 预警共存：预警仍触发，但**不再只提示 /new**，而是「提示 + 已自动处理」。
- 摘要的 token 成本计入 F5 的 completion。

### 3.3 开发任务（Phase 3）
- [ ] `context.py`：trim_tool_results / summarize_old / apply（依赖注入 llm_chat_fn）
- [ ] `agent.py`：每轮前接 `context.apply`；`auto_summarize` 从 config 读（默认 1）
- [ ] 摘要提示词固定（中文，要求「保留：目标/已完成/关键决策/文件路径/未完成项」）
- [ ] 摘要失败（LLM 报错）→ 降级只做 tool 截断，不中断主循环
- [ ] 验证：
  - [ ] 构造 > 70% 上下文 → 触发 tool 截断（无 LLM 调用）
  - [ ] 构造 > 80% → 触发摘要，历史被压缩，agent 仍记得首条任务目标
  - [ ] 摘要路径 LLM 断网 → 降级 tool 截断，agent 不崩

**Phase 3 成功标准**：
```bash
# 长任务（反复 read 大文件 / 多次工具）逼近上下文上限
python agent.py "逐个读取 logs/ 下所有文件并总结"
# 观察终端：黄字预警 → 「已压缩 8 轮历史」→ 任务继续完成，不报 context_overflow
```

---

## Phase 4（F5）：Token / 成本聚合

> 对齐 szclaw `token_usage`。会话级 + 跨会话持久化。

### 4.1 新增 `usage.py`（叶子）

**存储**：`usage/usage_YYYY-MM.json`（按天，追加）。每行/每项：`{ts, session_id, model, prompt, completion}`。

**对外接口**：
```python
class Tracker:
    def __init__(self, session_id): ...
    def add(self, model, prompt, completion): ...   # 累加内存 + 落盘
    def session_total(self) -> dict                  # {prompt, completion}
def day_total(date=None) -> dict                     # 跨会话按天聚合
def range_total(days=7) -> dict                      # 近 N 天
def model_breakdown(days=7) -> dict                  # 按 model 聚合
```

### 4.2 改造 `agent.py`
- `agent_loop` 每次收到 `usage` chunk → `tracker.add(model, prompt, completion)`。
- `/status`（已有）→ 升级为显示：本会话累计 + 今日累计 + 近 7 天累计 + 按模型分布。
- `session_end` 日志带上会话累计 token（已有 per-turn，补 session 级）。
- `print_summary` 显示会话累计。

### 4.3 开发任务（Phase 4）
- [ ] `usage.py`：Tracker + day_total/range_total/model_breakdown（原子追加）
- [ ] `agent.py`：每 usage chunk → tracker.add；`/status` 增强；`print_summary` 带会话累计
- [ ] 成本换算（可选，`config` 里配 `price_per_1k_in/out`，默认不显示金额，只显示 token）
- [ ] 验证：
  - [ ] 跑两次会话 → `/status` 今日累计 = 两次之和
  - [ ] 换 `--provider` 不同 model → model_breakdown 分开展示
  - [ ] 跨天 → 按天文件正确分片

**Phase 4 成功标准**：
```bash
python agent.py "任务A"; python agent.py "任务B"
python agent.py            # 交互
> /status
  本会话: prompt 5,120 / completion 890
  今日:   prompt 32,410 / completion 5,203  (3 sessions)
  近7天:  prompt 128,000 / completion 21,000
  按模型: AngelOrDevil 32,410/5,203 ; qwen2.5:14b 95,590/15,797
```

---

## 风险 & 注意

| 项 | 风险 | 应对 |
|----|------|------|
| 陷阱A 循环 import | logger↔ui 循环 | Phase 1 先拆，logger 内联颜色，`grep import ui logger.py` 验收 |
| 陷阱B 路径漂移 | config/WORK_DIR 定值晚于模块使用 | 固定入口顺序 config→providers→tools.WORK_DIR；skill/memory/log 目录保持 `__file__` 锁定（不随 work_dir 变） |
| llm 常量被改写 | agent 读 `llm.MODEL` 与 banner 不一致 | `apply_to_llm` 后 agent 一律读 `llm.MODEL/BASE_URL`（单一来源） |
| 会话 json 损坏 | 写一半崩溃 | 原子写 `.tmp`→`os.replace`；损坏自动 `.corrupt` 备份 |
| 摘要吃 token | 摘要本身消耗上下文 | 摘要输出限长（≤500 字）；只摘要最旧 N 轮；失败降级 |
| tool 截断丢信息 | 截断掉关键内容 | 只截**非最近一轮** tool 结果；保留完整路径/文件名 |
| 跨会话 token 统计误差 | 多进程并发写 usage 文件 | 追加用 O_APPEND + 短临界；CLI 单用户场景够用 |
| 配置向后兼容 | 老用户只有环境变量 | 无配置文件时完全走 env+默认，行为不变（回归测试） |

## 开发顺序总览

```
Phase 1 (F1+F3)  配置 + 多provider + logger解耦   ← 基础设施，先做
   ↓
Phase 2 (F2)     会话持久化 + /resume
   ↓
Phase 3 (F4)     上下文自动管理（依赖 F1 阈值）
   ↓
Phase 4 (F5)     token/成本聚合
```
> Phase 2/3/4 相对独立，但都依赖 Phase 1 的 config。建议严格按 1→2→3→4，每个 Phase 独立可交付、可回归。

## 完成后项目结构（v4.0）

```
agent_in/
├── agent.py             # 入口（config→providers→tools.WORK_DIR 接线；/resume /status /config /provider）
├── llm.py               # LLM 客户端（常量被 providers 覆盖，内部逻辑不变）
├── tools.py             # 工具（不变，WORK_DIR 赋值时机不变）
├── ui.py                # 终端 UI（不变）
├── vision.py            # 图片（不变）
├── skill_manager.py     # Skill（不变）
├── memory_manager.py    # Memory（不变）
├── logger.py            # 日志（去 import ui）
├── config.py            # 新建 F1 配置
├── providers.py         # 新建 F1/F3 provider 注册表
├── session.py           # 新建 F2 会话持久化
├── context.py           # 新建 F4 上下文管理
├── usage.py             # 新建 F5 token/成本
├── sessions/            # 运行时（F2）
├── usage/               # 运行时（F5）
├── agent_config.json    # 运行时生成（F1）
└── memory/  logs/  skills/
```
