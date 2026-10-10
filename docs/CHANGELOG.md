# Changelog

## 3.3 — 2026-10-10

vendor 原生扩展换成 CPython 3.14，任务脚本不再中途删掉，页面验收改为固定工具。

- lxml、greenlet、numpy、pandas、Pillow、pydantic-core 换成同版本 `cp314-win_amd64`。`python` 工具和 shell 用启动本进程的解释器，不再查找 `py -3.11`
- 导入失败时只报告当前解释器，并写明不要搜索其他 python
- temp 里成功写下的 `.py` `.ps1` `.bat` `.cmd` 计入产出。笔记和 json 不计。Qwen 档硬上限仍是 200
- `python` 工具的脚本留到本轮结束、且没有未完成待办时，才由 `clean_task` 清理
- 新工具 `preview_page`：用系统 Edge 在 1440×900 和 390×844 截图。打不开就停，不要自己写 Playwright

## 3.2 — 2026-10-07

技能子进程不再按系统 GBK 解码，后台任务转入时会等一小段首包。

- 脚本技能收集字节，按 utf-8 → gb18030 → locale 解码；环境与 shell 对齐，带 `PYTHONIOENCODING=utf-8` 和 `TASK_TEMP`
- 自动转入后台且日志仍空时，最多再等约 1 秒。仍空则说明日志可能还没写入，并提示 `wait_sec`
- `job action=output` 未传 `wait_sec` 时仍立即返回
- `skills/网页验收.md`：Edge 启动失败或页面打不开，报告原因并停，不改 Playwright / vendor / asyncio

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
