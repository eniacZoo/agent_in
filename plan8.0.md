# agent_in v8.0 — 按 PI 分层拆入口

> 前置：plan7.0 已完成并验收。
> 对齐 PI：`pi-ai` / `pi-agent-core` / CLI 边界。目的是改一处循环不必碰到 `/help`，不是多几个文件。
> 原则：行为不变。不为「将来可能」预留抽象。不引入事件总线，除非 abort 已经证明需要。

## 明确不做

新工具、SKILL.md 平台、JSONL 会话树、TUI、改安全网关（`tool_guard` / `approval` / `audit` 已是叶子）。

## 切分

```
agent.py     main / argparse / 启动接线（config → providers → WORK_DIR / SAFE_MODE）
loop.py      agent_loop + 上下文预警 + 能力降级接线 + Ctrl-C
commands.py  斜杠命令表 name → handler；core 不知道 /help 存在
llm.py       不变（对应 pi-ai）
tools.py     不变（5 工具 + 安全网关）
```

单向依赖：

```
agent.py ──→ loop, commands, config, providers, tools, ui, llm
commands.py ──→ session, memory_manager, skill_manager, config, providers, ui, usage, logger
loop.py ──→ llm, tools, ui, context, memory_manager, vision, logger, config
```

禁止：`loop.py` import `commands.py`；`llm.py` import `agent.py`。

## 迁移约束

- `run_interactive` 现有命令一条不能丢（plan7 已删的中文意图除外）。
- `agent_loop` 签名保持或仅加默认参数，`run_single` / `run_interactive` 调用处同步。
- 模块级全局（`CONTEXT_LIMIT`、`SHOW_REASONING`、`tools.WORK_DIR`）仍由入口在 load 之后赋值。
- `chat_3.8.py` 不接入主路径。
- 注释：模块头写职责与依赖方向；公共函数补参数/返回/副作用。风格对齐现有中文 docstring。不写叙事废话。

## 开发任务

- [x] 抽出 `loop.py`，`agent.py` 改为 import
- [x] 抽出 `commands.py`：一张表 dispatch；未知 `/xxx` 给一句「未知命令，/help」
- [x] `run_interactive` 不再含大段 if-else
- [x] 补模块头注释
- [x] 手测：`/help` `/memory` `/sessions` `/config` `/probe` `/ls skills` `/quit` 与拆前一致
- [x] Ctrl-C 中止本轮仍有效

## 成功标准

同样一组斜杠命令行为不变。`grep "def run_interactive"` 内无「记住」「/sessions」长分支。无新依赖。
