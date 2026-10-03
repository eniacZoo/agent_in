# Changelog

## 3.1 — 2026-10-03

上下文改为只追加，长任务按进展停，并补上离线建站所需的工具和依赖。

- 截断或 JSON 不完整的工具调用不执行；会话记录 `finish_reason` 与缓存命中
- 按 provider 分预算：Qwen 128K / 32K / 200 轮，DeepSeek 196K / 16K / 300 轮；压缩改为结构化摘要
- 新工具：`python`、`job`、`todo_write`、`ask_user`；`shell` 超时转后台；`edit_file` 默认唯一匹配；`write_file` 可 append
- 任务文件隔离在 `temp/tasks/<session_id>/`；`/continue` 交接后续做，`/clean` 清理过期临时文件
- vendor 增加 FastAPI 栈（cp311）和免构建的 Vue 3 / ECharts；技能覆盖拆分入库、数据管理系统脚手架、Edge 验收
- shell 拒绝结束本进程、父进程，或按名字结束全部 python
- 同批验收：3.11 解释器解析与 30 轮刹车（plan24）、产出以文件变化为准（plan25）、确认空回车再问（plan26）

## 3.0 — 2026-09-13

会话树与当前流式块差分渲染。

- 消息带 `id` / `parent`，session 记 `leaf_id`；模型只看到 root → 当前叶
- `/tree` 看树，`/fork [id]` 分叉；`/history` 只显示当前路径
- 旧线性 `sessions/*.json` 按数组串成单链，可直接打开
- thinking / ai 未完成块按行差分重绘（stdlib ANSI，不抢屏）
- 第一期已含：`/config` 与 `/provider` 同源、office2、连接失败一句文案

## 2.0 — 2026-09-09

第 13 次增量之后的首个功能完整版本。

- 5 个核心工具：`read_file` / `write_file` / `edit_file` / `shell` / `view_image`（后续增量补上 `glob` / `grep`）
- SAFE_MODE、危险命令审批、`temp/` 可删沙箱
- 办公流程 md（pptx/xlsx/docx/pdf/网页）+ vendor 离线包
- 会话、记忆、能力探测、provider 切换
- 密钥只放本机 `agent_config.json`，不进版本库

2026-09-10 整理：开发期 `_test_*.py` 归入 `tests/`；`plan*.md`、目录结构与本 Changelog 归入 `docs/`。`README.md` 留在根目录。
