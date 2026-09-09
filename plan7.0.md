# agent_in v7.0 — PI 式收核心

> 设计主轴：**PI**（小核心、短工具表、可中断、能看清模型吃了什么）。
> 原则：减法。stdlib only、不拆 `agent.py`、**不新增工具**、不写 SKILL.md 平台。
> 唯一偏离 PI：权限留在核心（办公机无 Docker）。

## 前置

建立在 plan6.0（S3/S4/S5 + 审批 + 审计）已落地的代码上。本拷贝缺 `rules/`，`safe_mode` 默认 False，`_exec_write_file` 仍有 `"Created."` 谎言。

## 明确不做

glob、load_skill、SKILL.md 骨架、学习环、回收站、审计报表、API Key、网络告警、拆 `agent.py`、事件总线。

## 概述

| # | 做什么 | 现状 | 目标 |
|---|--------|------|------|
| P1 | Windows 规则 + SAFE_MODE 默认开 | rules 缺失；Unix 兜底；safe_mode=False | 补 JSON 规则；默认 True；出界 BLOCK |
| P2 | 写/改确认 | 覆盖无确认；edit 全局替换无确认；Created. 撒谎 | 覆盖 CONFIRM；多处替换 CONFIRM；真写或真拒 |
| P3 | 钉死工具表 | skill 注册成 function | 模型只看见 5 个基础工具 |
| P4 | Ctrl-C 中止本轮 | 打断退出进程 | 中止本轮、会话保留 |
| P5 | 交互卫生 | banner v4.0；/help 不全；中文意图偷偷写盘 | help 对齐；只走斜杠命令 |

---

## Phase A — 操作安全

### A1 `config.py`

- `_DEFAULTS["safe_mode"] = True`
- 语义不变：工作目录内新建 ALLOW；出界写 BLOCK（SAFE_MODE 下 CRITICAL）；HIGH shell 升级 BLOCK

### A2 `rules/dangerous_shell.json`

Windows 优先，Unix 交叉保留。至少覆盖：

- CRITICAL：`Format-Volume`、`diskpart`、`mkfs`、`dd ... of=/dev/`、删根
- HIGH：`Remove-Item`/`ri` + `-Recurse`/`-Force`、`del /s`、`rd /s /q`、`rmdir /s`、`rm -rf`/`rm -r`、`iex`/`Invoke-Expression`
- MEDIUM：`Clear-Content`、`sudo`、`kill -9`

evasion 定级沿用 plan6：管道下载执行 CRITICAL，命令替换 HIGH。

### A3 `rules/sensitive_paths.json`

Unix 根 + Windows：`C:\Windows`、`C:\Windows\System32`、`%USERPROFILE%\.ssh`。敏感文件名：`.env`、`id_rsa`、`.pem`、`credentials`。

桌面/文档不列入保护根（那是常见 work_dir）。

### A4 `tools.py` 网关

- 删除 `_exec_write_file` 内 `_is_within_workdir` + `"Created."`；出界只走 `execute()` → `tool_guard.assess_write`
- `write_file`：目标已存在且是文件 → MEDIUM CONFIRM（覆盖）
- `edit_file`：`old_text` 出现次数 > 1 → MEDIUM CONFIRM
- `read_file` / `edit_file` 读盘：utf-8 → gb18030 → locale 默认；写回用检出的编码
- `_is_within_workdir` 若仅被谎言分支使用则删除

### A5 Ctrl-C

`agent_loop` 捕获 `KeyboardInterrupt`：停 LLM/工具、返回「已中止」、**不** `sys.exit`。交互 `input()` 处仍退出循环（现有行为）。

`run_single`：`confirm_fn` 恒 False（管道下需确认则拒）。

### A6 验收

- 工作目录新建：直接成功
- 工作目录覆盖：y/N
- `Remove-Item -Recurse`：STRONG_CONFIRM 或 SAFE_MODE 下 BLOCK
- 写 `C:\Windows\...`：BLOCK
- 出界写不再出现 `"Created."`
- 非交互遇覆盖/危险命令：直接拒，不卡 input

---

## Phase B — 缩短工具表

### B1 `tools._load_tools`

返回 `BASE_TOOLS` only。不再 `skill_manager.to_tool_schemas()`。

`reload_tools()` 仍可调用，结果仍是 5 个工具（`agent.py` 里保存 skill 后的调用不崩）。

`skill_manager` / `to_tool_schemas` / `/ls skills` / `/use` / `/del` 保留：人用斜杠调，模型默认看不见。

### B2 system prompt

只描述 5 个工具 + `{work_dir}` + 先读后改 + Windows 用 dir/PowerShell。删掉与 schema 重复的长说明。

### B3 验收

`TOOLS` 无 `skill_*`。无 skills 目录时行为与现在一致（本来也是 5 个）。

---

## Phase C — 交互卫生

### C1 `ui.print_banner`

版本改为 v7.0。可显示 SAFE_MODE on/off（从调用方传入或读 config）。

### C2 `ui.print_help`

列出真实命令：`/quit` `/new` `/sessions` `/resume` `/save` `/status` `/history` `/config` `/provider` `/probe` `/memory` `/ls skills` `/use` `/del` `/logs` `/help`。

### C3 去掉隐式写盘

删除 `run_interactive` 里「记住…」「保存为 skill」「以后都…」句子匹配。记忆只走 `/memory add|del|clear`。

### C4 验收

`/help` 与实际分支一致。输入「记住明天开会」走普通对话，不写 MEMORY.md。

---

## 开发任务清单

- [ ] A1 config 默认 safe_mode True
- [ ] A2 rules/dangerous_shell.json
- [ ] A3 rules/sensitive_paths.json
- [ ] A4 修 Created.；覆盖/多替换 CONFIRM；编码回退
- [ ] A5 agent_loop Ctrl-C；run_single 非交互拒确认
- [ ] B1 TOOLS = BASE_TOOLS
- [ ] B2 缩短 DEFAULT_SYSTEM_PROMPT
- [ ] C1–C3 banner / help / 去掉中文意图
- [ ] `_test_plan7.py`：Windows 删除规则、出界 BLOCK、覆盖 CONFIRM、TOOLS 无 skill_

## 成功标准

```
python agent.py -w <workdir>   # 横幅 v7.0，SAFE_MODE on
TOOLS 仅 5 项
写 work_dir 新文件 → 无确认
覆盖已有文件 → [y/N]
shell Remove-Item -Recurse → 强确认或阻断
写 C:\Windows\Temp\x.txt → Blocked
Ctrl-C 在思考/工具中 → 回到 >
```

无新 pip 依赖。不拆文件（除新增 rules JSON 与测试脚本）。
