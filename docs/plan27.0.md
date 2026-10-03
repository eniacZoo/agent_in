# agent_in v27.0 / 产品 3.1 — harness：上下文、任务状态、工具与离线建站

> 前置：plan23（会话树、差分渲染）。产品版本升为 **3.1**。
> 同模型对照（DeepSeek 4.1 flash，147 家网点诊断表）：PI 约 26 分钟做完；agent_in 会话 `bc657821` 153 次调用、重读 45 次，并在 80 轮停下。差距在 harness，不在模型。
> 80 轮上限是症状。每次请求只留约 20K token，更早的历史被压成工具名，前缀缓存也跟着失效。

## 做了什么

1. **截断不再执行（P0）**  
   解析 `finish_reason`。DeepSeek `prompt_cache_hit_tokens`、vLLM `prompt_tokens_details.cached_tokens` 记入会话遥测，`/status` 可看。  
   `finish_reason=length` 或工具参数 JSON 不完整：不执行，要求拆成骨架加 `edit_file`，或 `write_file` 的 `mode=append`。

2. **上下文只追加（P1）**  
   未到压缩阈值时历史只追加，不再每轮滑窗。压缩用结构化摘要（目标、约束、进度、文件清单、下一步），最近约 24K token 保留原文。旧的 write/edit 正文改成 `{"_omitted": true, "chars", "path"}`。  
   预算按 provider：Qwen / office* 为 128K 窗口、`max_tokens` 32K、硬上限 200 轮、`keep_turn_reasoning` 开、压缩比 0.70；DeepSeek 为 196K、16K、300 轮、思考链默认不回传、压缩比 0.75。  
   文件里的旧默认值（`max_tool_iterations` 80、`keep_recent_tokens` 20000、`max_tokens` 8192）不覆盖这套 profile。

3. **按进展停机（P2）**  
   `todo_write` 按 id 合并，写入任务目录 `todo.json`。`ask_user` 为选择题，推荐项放第一。计划可落 `plan.md`。  
   每 50 轮只打印进度。同一调用同一结果 3 次、连续 5 次工具错误、或 30 轮既无产物也无 todo 进展，则停并写交接摘要。`/continue` 把原文交给模型，不当成未知命令吞掉。无进展刹车仍是 `STALL_STOP_ROUNDS = 30`。

4. **工具（P3）**  
   `python`：代码进任务临时目录，`PYTHONPATH` 带 vendor，成功删除脚本，失败保留。  
   `shell`：`background=true` 或前台超过 15 秒转入后台，不杀进程，返回 job id。`job` 可 `output` / `kill` / `list`。结果里注明还有几个后台任务。`/quit` 和进程退出时结束这些任务。超长输出留首尾，全文在任务 `out/`。  
   `edit_file` 默认唯一匹配，`replace_all` 才全换；`edits[]` 一次改多处并返回短 diff。`write_file` 支持 `mode=append`。

5. **临时目录（P4）**  
   探查和任务文件放在 `temp/tasks/<session_id>/`（系统提示里的 `task_temp`）。真正做完且没有未完成 todo 时，清掉残留，保留 `plan.md`、`todo.json`、`task_notes.md`、`.active`。  
   `/clean`、`/clean list`、`/clean now`。孤儿目录 7 天 TTL。探针输出不要写进交付目录。

6. **离线建站与验收（P5）**  
   vendor 增加 FastAPI 0.142.2、uvicorn 0.54.0、Starlette 1.7.0、pydantic 2.13.5（pydantic-core 为 cp311 win_amd64）及依赖。`vendor/web/` 放 Vue 3.5.22 与 ECharts 5.6.0，不经 npm。字体用本机字体栈。  
   新流程：`skills/数据拆分入库.md`、`skills/数据管理系统.md`（脚手架 `skills/templates/webapp/`）、`skills/网页验收.md`（Playwright，`channel="msedge"`，1440 与 390）。`skills/网页.md` 去掉「无 Chromium」。  
   系统提示要求：完成要有工具证据；先验证假设再改；校验不能改成恒真。

7. **验收（P6）**  
   `tests/_test_plan27.py` 覆盖截断拒绝、只追加窗口、结构化压缩、profile 预算、重复停机、`/continue`、python 清理、后台任务、append / 批量编辑、TTL。  
   `tests/regression/score_session.py` 与 `tests/regression/net147.md` 记录 147 网点的对照方法和基线（`bc657821`：153 次调用、重读 45、撞过 80 轮）。这次没有重跑实模型，避免覆盖本机项目。

8. **重启服务不再带走主程序**  
   shell 若会 `Stop-Process` / `taskkill` / `kill` 本进程或其父链，或按名字结束全部 `python` / `py`，直接返回错误，不启动。  
   停掉 netstat 里正在 LISTENING 的那个其他 PID 仍然允许。后台任务自己的 `taskkill /T` 只作用于外壳子进程。

## 不改

登录、额外沙箱、把 147 网点实跑写进这次提交。配置默认值仍让旧测试读到 80 轮和 20000 token；运行时以 profile 为准。

## 验收

- [x] 截断的工具参数不执行
- [x] 窗口只追加，压缩摘要含文件清单
- [x] Qwen / DeepSeek 预算分开；旧默认不盖掉 profile
- [x] 重复、连续错误、30 轮无进展会停；`/continue` 进入模型
- [x] `python` 成功删脚本、失败保留；慢 shell 转后台后可 `job` kill
- [x] `write_file` append、`edits[]`、非唯一匹配报错
- [x] TTL 清理不碰受保护的当前任务
- [x] 点名结束本进程 PID 被拒绝；结束其他 PID 仍可执行
- [ ] DeepSeek / Qwen 各跑一遍 147 网点（步骤见 `tests/regression/net147.md`）
