# agent_in v15.0 — 启动不断连 + 心跳

> 前置：plan14 playbook。隔离网 DNS 失败不应挡住 `/model` 切换。

## 改什么

- `run_interactive`：连通失败只记日志，进入 REPL。`run_single` 仍退出。
- `heartbeat.py`：DNS → TCP → HTTP，记 `heartbeat_ok` / `heartbeat_fail`（含 stage/errno）。
- 流式：`llm_ttfb_ms`；超过 60s 无 chunk 记 `stream_stall`。
- `/status` + banner 显示连接状态与上次心跳。

## 成功标准

指向不可解析 host 时 `py agent.py` 仍出 `>`。`/status` 能看到 DISCONNECTED 与心跳 stage=dns。
