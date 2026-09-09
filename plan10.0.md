# agent_in v10.0 — 交互可视化（仍是终端）

> 前置：plan7 + plan8。plan9 可未做。
> 对齐 PI：「终端还是终端」，不抢屏、不 alt-buffer、不引入 rich / prompt_toolkit / curses。
> stdlib + 现有 ANSI。Windows 11 控制台 UTF-8（`ui._enable_ansi` 已有）。

## 明确不做

完整 TUI、差分渲染框架、主题包、JSON/RPC 模式（以后若要，另开 plan）。

## 改什么

1. **工具行**  
   现在只打结果摘要。改为固定一行：工具名、关键路径或命令截断、审批结果（放行 / 已确认 / 已拒绝 / 阻断）。危险命令在审批前后都能看见命令本身。

2. **审批 vs spinner**  
   `ui.confirm` / `text_input` 已暂停 spinner。验收：强确认键入 token 时动画不得覆写输入。若仍打架，只修暂停/恢复，不加新库。

3. **`/status`**  
   现有 token 累计保留。补：当前 SAFE_MODE、work_dir、本轮工具轮次、上下文占用百分比（已有 70%/90% 预警，收到 `/status` 里）。

4. **banner**  
   去掉过期的写死「ctx: 196K, text」；用 `capability` / `CONTEXT_LIMIT` 真实值。命令提示与 `/help` 不重复长列表，一行即可。

5. **流式**  
   保持现有 `StreamDisplay`。不重做思考区布局。

## 开发任务

- [x] 工具执行一行状态（含审批）
- [x] 手测：覆盖确认 + spinner 同时出现，输入不被冲掉
- [x] `/status` 含 SAFE_MODE 与上下文占用
- [x] banner 用真实 context 上限
- [x] 无新依赖

## 成功标准

Windows Terminal / 默认 conhost 下：中文、警告符号不乱码；审批可键入；会话结束后 `/status` 数字与 footer 一致。项目体积几乎不涨。
