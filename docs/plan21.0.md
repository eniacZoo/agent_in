# agent_in v21.0 — 会话记性、运行时护栏、窗口压缩、只读搜索

> 方向：向 Pi 靠拢（CLI、精简、精准），**不重做、不完整复制 Pi**。
> 场景：办公几件套（xlsx / docx / pptx / pdf / 网页）交互与自动化。
> 前置：plan20.0 已落地（回合内止血、台账、纪律、刹车、`/paste`）。
> 本文件是实施规格。先评审，通过后再按阶段改代码。

## 决策（先写死，避免做到一半膨胀）

1. **改现有仓库，不重写，不迁 TypeScript Pi。**
2. **不搬 Pi 产品壳**：`/tree` `/fork` `/clone`、JSONL 会话树 UI、消息队列、编辑器、扩展系统、MCP、多 agent。
3. **交互层本轮不动**（`/paste` 已够用）。
4. **台账保留。** 它是 system 里的速查表，不是会话的替代品。
5. **Excel/Word 不升级成第 6 个 function。** 办公改表仍是 md + 脚本。但 **`read_file` 对办公格式必须给出有界摘要**——这是现有工具做对事，不是新 function。
6. **坑封进运行时，不写进 skill 百科。** 禁止靠加长 `skills/xlsx.md` 去补 `max_column`、theme、16383 列这类个案。同一类失败只在工具层挡一次。
7. 一次只做一个阶段。新单测写进 `_test_plan21.py`。每阶段跑该文件 + `py -m unittest discover -p "_test_plan*.py"`。已知无关失败：`_test_plan9` GBK、`_test_plan11` PIL。不要修这两个。
8. 不要改 `vendor/`、不要改 `E:\CursorProjects\优化`、**不要改 `skills/*.md` 正文**（流程仍由模型 `read_file` 那些 md；护栏在 `tools.py` / `loop.py`）。system prompt 最多加一句「xlsx 用 read_file 看结构，不要按 max_column 扫全表」。
9. **12 轮刹车只在本回合已经开始改文件之后生效。** 不靠把 12 改成 24，不按任务类型分支。探查的硬上限仍是 80。

## 问题基线

### A. 跨回合失忆（plan20 之后仍在）

会话 `208e614f`：产物落盘后模型仍当没做过。

- `loop.agent_loop` 里 `full_messages = [system] + messages` 是新列表。工具消息只长在这份拷贝上。
- `agent.py` 回合结束后只往 `state.messages` 追加最终 assistant 文本。工具历史丢了。
- `session.save` 与内存中的 `state.messages` 不是同一份。
- `context.apply` 原地改工作拷贝，瘦身若写回 session，原文也没了。
- `session._validate_messages` 可能把完整 tool 批次在 `/resume` 时截掉。

### B. 便宜模型靠自造脚本探表，成功率被拖垮（2026-09-10 美化任务）

会话 `0dc1eb9f`：扩表已完成，用户要求表头对齐 / 去空白列 / 行配色。卡在

`[>] shell( python ...\temp\b9_probe.py )`

实测 `74条街道测算`：`max_row=182`，**`max_col=16383`**（样式把画布撑满），合并 1244 个。`b9_probe.py` 对 `range(1, ws.max_column+1)` 再套合并三重循环，等于扫整张 Excel 画布。shell 无回显，界面像死机；用户 Ctrl-C。同一回合写了 `b3`–`b9` 十来个探针，**始终没写出改表脚本**。

原因不是「只会改提示词」：

- `.xlsx` 走现在的 `read_file`（当文本）等于废，模型只能发明 Python。
- 发明时最标准的 API 就是 `ws.max_column`。
- 写 `temp/bN.py` 仍算「产出」，plan20 的 12 轮无产出刹车不清零。
- 超时/卡死对模型不可见，它不会自己改成「只扫 41 列」。

别的 agent 少写这种脚本，是因为它们往往不必用 `max_column` 过日子。本 plan 要把这条路在 **工具里堵死**，而不是每踩一次就加长 skill。

办公任务仍是直线：探表 → 写脚本 → 校验 → 停。需要记性 + 探表别自造扫画布 + 空转要停。

### C. 12 轮无产出刹车误伤探查（2026-09-10，会话 `9a11b87f`）

用户要在 PowerShell 里配 Pi 的 DeepSeek。任务本质是 **读配置 / 搜 npm 文档 / 再写 `~/.pi`**，前十几轮本来就不该有「用户侧文件」。

实测两段：

1. 回合 #1：12 轮全是 `shell` + `read_file`（列 `~/.pi`、读 `models.md`、grep 包）。第 5 轮告警「可能在原地打转」，第 12 轮 **直接停**，产出 0。模型还没写配置。
2. 用户说「还没设置完，请继续」。回合 #2 又探了 11 轮，第 12 轮 `write_file ~/.pi/agent/models.json` 被 SAFE_MODE 出界阻断；返回值不是 `Error` 开头，`loop.py` 仍把 `_writes_this_turn` **加了 1**（状态行「产出 1」）。随后改走 `temp/*.ps1` + `shell` 才写完，第 15 轮成功。

根因不是「12 这个数字太大或太小」：

- 刹车把「本轮还没 `write_file`」当成「原地打转」。办公改表需要这个假设；**探查 / 配环境 / 只读诊断不需要。**
- plan21 原先 2.2「temp 不算产出」只会让美化探针更快停，**会让本任务更早停**（连 temp 脚本都不算清零）。
- 被阻断的写出界仍算产出：既谎报进度，又可能让真探针循环靠一次失败写续命。

SAFE_MODE 拦 `write_file` 出界是对的，不必为本任务关掉。缺的是：探查阶段不要 12 停；失败/阻断写不要算产出。

---

## 阶段 1：会话回写完整轨迹

目标：同一会话里，下一回合能看见本回合的 `tool_calls` 与 `role=tool`。`/resume` 同样如此。

### 1.1 单一事实来源

- `state.messages` = 本会话完整轨迹（不含 system）。
- `session.save` 存的就是 `state.messages`，不再另拼一份 reply。
- `agent_loop` 返回的第五项去掉 system 后，就是下一轮应采用的轨迹。

循环中途的 `session.save` 改为存 **未裁剪的轨迹**。

### 1.2 窗口与轨迹分开

在 `agent_loop` 内：

- `transcript`：只追加，不跑 `context.apply`。
- `full_messages`：发给 API 的工作拷贝；`apply()` 只改这份。

每轮 LLM 前从 transcript 派生窗口。工具结果同时 append 到 transcript 与窗口。

最终无 tool_calls 的 assistant 文本写入 transcript（现在 break 时没写入）。`[已中止]` 仍按 plan20 写说明句进 transcript，不要写字面 `[已中止]`。调用方不再 `saved.append(reply)`。

### 1.3 修正 `session._validate_messages`

- 完整的 `assistant(tool_calls)` + 配对齐全的 `role=tool` 视为合法末尾。
- 仅残缺（有 tool_calls 无结果）时截到上一个完整边界。

### 1.4 `agent.py`

`run_interactive` / `run_single`：`state.messages = [m for m in full_messages if m.get("role") != "system"]`，然后 `session.save(..., state.messages)`。中止说明已在 transcript 则不要重复追加。

### 阶段 1 验收

`Phase1SessionTests`：

- `test_transcript_keeps_tools`
- `test_validate_keeps_complete_tool_batch` / `test_validate_truncates_orphan_tool_call`
- `test_no_duplicate_final_reply`

手工：`write_file temp/x.txt` → 下一句「你刚写了什么」应直接答路径。

---

## 阶段 2：运行时护栏（办公可读 + 探针不算产出 + 慢脚本可见）

目标：DeepSeek 这类模型 **不必靠更强 AI 验尸**，也不必每遇一坑就加长 skill。堵住「自造脚本扫 `max_column`」、「连写 temp 探针永不刹车」、以及「只读探查被 12 轮误停」。

### 2.1 `read_file`：办公格式给有界摘要

`tools._exec_read_file`：按后缀分流。**禁止**对 `.xlsx` 当 utf-8 文本读（二进制垃圾会逼模型写探针）。

**xlsx（必做）**

用 vendor 的 openpyxl（`tools.py` 已能通过 PYTHONPATH / 自行 `sys.path` 指到 `vendor/`，与 shell 一致）。摘要必须有硬上限：

- **禁止** `for c in range(1, ws.max_column+1)`。列上界 =  occuped cells 或硬顶 `OFFICE_MAX_COLS = 80`（取实际占用与 80 的较小值）。占用列从 `ws._cells` 的 key 取 max col，不要用 `ws.max_column`。
- 输出大约：sheet 名列表；当前 sheet（默认第一个或文件名能对上的）的 `used_rows` / `used_cols`（有内容的，不是 16383）；第 1–8 行有值的表头（坐标=值，截断）；合并个数（可只报数量 + 前 30 个 range）；「不要用 max_column 扫全表，本摘要列已封顶」。
- 整段摘要 ≤ 8000 字符。多 sheet 每个 sheet 最多 15 行。
- 打不开或缺 openpyxl：返回明确 Error，仍不要当文本 dump。

**docx / pptx / pdf（本阶段能 50 行内做完就做，否则只留 xlsx）**

同样有界：段落/幻灯片标题/页数前几段，上限 8000 字符。做不完不要堵阶段 2。

`read_file` 的 schema description 补一句：xlsx/docx 等返回结构摘要，不是原始字节。

### 2.2 「产出」只认用户侧成功写；12 停只在改文件之后

抽 `_is_product_write(path) -> bool`：路径在 `WORK_DIR` 内、且 **不在** `{work_dir}/temp/` 下。

`_writes_this_turn += 1` 仅当 **同时** 满足：

- 工具是 `write_file` / `edit_file`
- 结果真正成功（`ok`：不以 `Error` / `操作被拒绝` 开头；`tools.execute` 阻断文案是 `操作被拒绝（blocked）：…`，现在不算 Error，必须改判定）
- `_is_product_write(path)` 为真

被阻断、失败、写 `temp/`，一律不加。状态行「产出 n」同一口径。temp 仍可 `ledger_add`，不当刹车产出。

**何时累加 `rounds_since_write`、何时 12 停：**

本回合设 `_mutated = False`。一旦出现过（不论成败）：

- `write_file` / `edit_file`，或
- `shell` 的 command 像在跑/写 `{work_dir}/temp/` 下的脚本（`python ...\temp\*.py` 或 `powershell ...\temp\*.ps1` 即可，不必完美）

则 `_mutated = True`。

| 状态 | 5 轮 | 12 轮 |
|------|------|--------|
| 尚未 `_mutated`（只 `read_file` / `view_image` / 探查向 `shell`，以及将来的 glob/grep） | **不告警「打转」** | **不停**。硬上限仍是 `max_tool_iterations`（80） |
| 已 `_mutated`，且这 12 轮没有一次产品写 | 告警（文案可改为「已开始改文件但还没有用户侧产物」） | **停止** + 台账（美化探针走这条） |
| 产品写成功 | `rounds_since_write = 0` | — |

会话 `9a11b87f` 回合 #1 从未 `_mutated` → 不应停。会话 `0dc1eb9f` 一写 `temp/b3.py` 就进入变异态 → 12 轮无产品写仍停。

不要按任务关键词（「excel」/「配置」）分支。不要把 12 改成 24 来绕过探查——那只是让探针多空转 12 轮。

### 2.3 shell：可见超时 + 挡一类蠢循环

- 跑 Python 时环境加 `PYTHONUNBUFFERED=1`（现有 `_shell_env`）。
- 超时返回必须含秒数（已有则保持），并建议「缩小扫描范围 / 用 read_file 看 xlsx」。
- **执行前**若 command 像 `python ...\foo.py`：读该文件（失败则跳过扫描），若源码匹配  
  `range\s*\([^)]*max_column` 或 `iter_rows\s*\(\s*\)` 且无 `max_col`  
  → **不启动进程**，返回 Error，写明：列循环须有上界（≤80 或最后有内容的列），xlsx 结构用 `read_file`。  
  这是工具错误，不是 skill 课文。

PowerShell `&&`：本阶段若顺手（检测 command 含 `&&` 且当前是 powershell）返回 Error 并提示用 `;`，可以做；不要为此新开阶段。

### 阶段 2 验收

`Phase2HarnessTests`：

- `test_read_xlsx_bounded`：造一个小 xlsx（openpyxl 写 2 列），`read_file` 摘要含 sheet 名且 **不含** 16383；再给一个把 `max_column` 撑大的夹具若太重可 skip，至少断言实现源码无 `range(1, ws.max_column`。
- `test_read_xlsx_not_raw_zip`：读 xlsx 结果不以 `PK` 开头。
- `test_temp_write_not_counted`：`_is_product_write`：`temp/` 下 False，工作区根 xlsx True。
- `test_blocked_write_not_counted`：返回以 `操作被拒绝` 开头时，不得当作 `ok` 去加产出（可测 `_is_tool_ok(result)`）。
- `test_stall_skips_inspect_only`：源码断言 12 停依赖 `_mutated`（或抽出的 `should_stall_stop(mutated, rounds_since_write)`：`False, 12` → 不停；`True, 12` → 停）。
- `test_stall_after_temp_mutate`：已变异 + 12 轮无产品写 → 停。
- `test_reject_max_column_loop`：临时 py 含 `range(1, ws.max_column+1)`，`tools.execute("shell", {"command": "python that.py"})` 返回 Error 且进程未把「sentinel 文件」写出来。
- `test_pythonunbuffered_in_env`：`_shell_env()` 含 `PYTHONUNBUFFERED=1`。

---

## 阶段 3：压缩按预算切，原文留在会话里

目标：API 窗口可以瘦；`sessions/*.json` 仍是完整轨迹。

### 3.1 磁盘 vs 窗口

- 磁盘 / `state.messages` = 阶段 1 的 `transcript`。不对它滑动窗口、不用摘要替换原文。
- API 窗口每轮从 transcript 派生；plan20 的 trim / over_budget 只打窗口。
- 单条 tool 存盘最多 `TRANSCRIPT_TOOL_MAX_CHARS = 20000`。`write_file` 参数 content 存盘同样上限或只留 path+长度。

### 3.2 按保留最近 token 切摘要

- 配置 `keep_recent_tokens: 20000`（`_DEFAULTS` / `_WHITELIST` / example json）。
- 从最新往回累加 `_estimate_tokens`，切点落在 user / 无 tool_calls 的 assistant / 完整 tool 批次。
- 摘要只进窗口（`name=context_summary`），**save 前滤掉**，不删 transcript。
- `SUMMARY_MAX_CHARS` 提到 1500。
- `sliding_window` 只作用于窗口。

可选 `/compact`：强制下一轮窗口摘要；时间紧可只留 `context.window_from_transcript()`。

### 阶段 3 验收

`Phase3CompactTests`：`test_window_trim_does_not_mutate_transcript`、`test_cut_on_boundary`、`test_keep_recent_tokens_config`、`test_summary_stays_out_of_session_file`。

---

## 阶段 4：只读搜索（glob + grep）

目标：找办公文件和 `temp/*.py` 不必靠 PowerShell。工具表 5 → **7**。plan7「钉死 5 个」的明示例外：只读、不出工作目录、不执行。

### 4.1 `glob`

`pattern` 必填，`path` 可选默认 work_dir。只解析到 `WORK_DIR`；最多 200 条文件；忽略 `vendor/`、`.git`、`__pycache__`、`node_modules`。

### 4.2 `grep`

`pattern` 默认当正则，非法则当字面量；`glob` 可选默认 `*.{py,md,txt,json,csv}`。每文件 20 命中、全局 100 行。stdlib，不调外部 rg。

### 4.3 接线

`BASE_TOOLS` + `execute`；`tool_guard` 与 `read_file` 同级。system prompt 工具列表加上 glob、grep。更新 `_test_plan9/12/19` 里钉死 5 工具的断言为 7 个名字（跟随改动，不是修 GBK）。

### 阶段 4 验收

`Phase4SearchTests`：workdir 内 glob 命中 xlsx；出界 Error；grep 命中 `openpyxl`；工具名顺序  
`read_file, write_file, edit_file, shell, view_image, glob, grep`。

---

## 明确不做

- 会话树、分叉、时间旅行 UI  
- 中途插话 / 消息队列 / 多行编辑器  
- 把办公格式做成独立 function（read_file 摘要不是）  
- 每遇一坑加长 `skills/*.md`  
- Chromium / pip / 改 skill 流程正文  
- 工具并行、去掉台账、LangChain/LangGraph、多 provider 目录、prompt cache  

## 建议实施顺序

| 阶段 | 挡哪类失败 | 不修的后果 |
|------|------------|------------|
| 1 会话回写 | 换一句就失忆、重新探表 | 压缩/护栏都白做 |
| 2 运行时护栏 | `max_column` 扫画布、探针当产出、探查任务被 12 轮误停、阻断写谎报产出 | 办公空转或配置类任务半路掐死 |
| 3 窗口/原文 | session 被 trim 污染 | resume 看到残本 |
| 4 glob/grep | 用 shell 找文件 | 有方言税，但不再是失忆/扫画布级 |

先 1 再 2。2 单独也能立刻改善「美化卡在 b9」。3、4 可随后。

## 办公验收（用户本机，不必找更强 AI）

自己看四件事：

1. `read_file` 一个 xlsx：立刻得到结构摘要，**第一个探查 shell 不应再扫全表**。  
2. 只读探查（列目录、读 md、grep 包）**不要**在 12 轮停；一旦开始写 `temp/` 或改用户文件，再 12 轮无用户侧产物才停。  
3. 出界 `write_file` 被拒时，状态行「产出」不得 +1。 
4. 同一会话、不要 `/new`：扩表完成后再问「产物在哪」——应直接答路径，不重写扩表脚本。

美化三项（表头 / 空列 / 配色）若再跑：允许 1 份改表脚本，不允许再出现对 `max_column` 的全表 `range`。plan20 的 token/轮次门槛不放宽。
