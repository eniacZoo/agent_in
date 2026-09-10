# agent_in v20.0 — 轮次与成本治理

> 基线：session 208e614f，148 次 API，累计 prompt 3.81M，单次峰值 86.5K。
> 产物落盘后仍有约 58 次工具调用。`sessions/208e614f.json` 仅 2 条 tool 消息。

## 阶段 1 实际改动

- `llm.py`：`_sanitize_messages` 剥掉 `reasoning_content` 等本地字段后再发 API。
- `context.py`：`trim_tool_call_args` 瘦身旧 `write_file`/`edit_file` 大参数；`apply()` 按 `budget` 加压，删死变量 `percent`。
- `config.py` + `agent_config.example.json`：`compact_at_tokens=40000`。
- `loop.py`：`context.apply` 传 `LAST_PROMPT_TOKENS`（上次单次 prompt），不再传累计 `total_prompt`。

## 阶段 2 实际改动

- `loop.py`：`TURN_LEDGER` / `ledger_add` / `ledger_clear` / `ledger_block`；write/edit/shell 成功后记账；台账拼进 system prompt。
- `agent.py`：`[已中止]` 改为明确说明，不把中止标记当模型回复写入历史。
- `commands.py`：`/new` 调用 `ledger_clear()`。

## 阶段 3 实际改动

- `_OFFICE_OVERLAY` 改名 `_OFFICE_DISCIPLINE`，解绑 `office` provider，加入「立即停止」。
- `tools.shell_name()`；system prompt 与 shell 工具描述写明 PowerShell 用 `;` 不用 `&&`。
- `_test_plan19.py` 跟随常量改名。

## 阶段 4 实际改动

- 每轮状态行：`round k/N | last | cum | 产出 n`。
- 重复 `(tool, args_hash)` 告警；连续 5 轮无产出告警、12 轮停止并输出台账。
- 日志补 `tool_call_sig` / `args_hash`。

## 阶段 5 实际改动

- `agent.py`：`/paste` 多行模式；Windows `_drain_pasted_lines`；`max_tool_iterations` 兜底 80。
- `ui.turn_footer`：增加 `rounds`/`peak`。
- `loop.py`：`LAST_PEAK_PROMPT`；`while tool_iterations < MAX`；达到上限用 `>=`。

## 全局验收（用户本机）

用 `/paste` 一次性贴完整扩表提示词，核对：

- 主回合 LLM ≤ 25
- 累计 prompt ≤ 500K
- 峰值 ≤ 45K
- 产物落盘后额外工具调用 ≤ 5
- 无 `&&` / `cd /d` 报错
- 每轮有 `round k/80 | last … | cum … | 产出 n`
- 产物仍通过 74 街道 / 146 网点 / 合并 / SUM / 底色红字

