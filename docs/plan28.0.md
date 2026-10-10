# agent_in v28.0 / 产品 3.2 — 技能输出、后台首包、验收失败即停

> 前置：plan27（产品 3.1）。产品版本升为 **3.2**。`ui.APP_VERSION`、README、CHANGELOG 已同步。
> 评测只提供症状。主证据：`E:\DeepSeekHarnessProjectss\agent_in_eval\results\run_20261006_111432_1347445`（全量跑时测试集记 2.2.0，分诊后为 2.3.0）。88 例：62 通过 / 17 失败 / 9 xfail / 2 XPASS。模型 `qwen/qwen3.8-27b`。web_skill 原始 2/8。
> 完成标准是下面三件事的行为，不是把测试集门禁刷绿。

## 评测结论

全量 17 条失败里，约一半在测试集 2.3.0 复跑后转绿，是断言或执行器写窄了：F-07、S-01、S-04、O-05、O-09、X-01、X-02、X-05、X-06。W-06 的进程树已经清掉，失败落在退出码和调用上限。O-01 是回答里的 Markdown 加粗让正则落空。

仍值得改的运行时缺陷只有两处，不依赖用例措辞也成立：

- X-04 / G29：脚本技能在中文 Windows 上按 GBK 读子进程输出，读线程抛 `UnicodeDecodeError`，技能本身已执行成功。
- R-10、W-04 / G27：shell 转入后台的那一刻 `jobs/<id>.log` 仍是 0 字节，返回文案要求立刻 `job action=output`，模型读到「(没有新输出)」。

X-07 是一次 live：模型放下 `skills/网页验收.md` 里的现成片段，转去改 Playwright，430 秒被测试集杀掉，截图和 `view_image` 都是 0。一次跑偏不构成新增技能的理由。O-10 与已通过的 X-08 是同一套 FastAPI 脚手架，差在模型自选了子目录。

## 要做

1. **技能子进程与 shell 同一条解码链**  
   `skill_manager.py` 约 175 行 `subprocess.run(..., text=True)` 用系统编码。中文 Windows 上是 GBK，输出里的非 GBK 字节让 `subprocess._readerthread` 抛 `UnicodeDecodeError`，Traceback 进 stderr。  
   `tools._decode_bytes`（`tools.py` 约 1626 行）已是 utf-8 → gb18030 → locale → replace。技能子进程改为收集字节，再调用这条链。环境与 `tools._shell_env()` 对齐：`PYTHONIOENCODING=utf-8`，并带上 `TASK_TEMP`。现在技能路径只补了 `PYTHONPATH` 和 vendor 的 Python（`skill_manager.py` 约 163–170 行）。

2. **转入后台时等一小段首包**  
   `_format_running`（`tools.py` 约 1322 行）在还没有输出时仍写「用 job 工具 action=output 查看新输出」。模型照做，`_exec_job` 在未传 `wait_sec` 时立刻读日志，空则返回「(没有新输出)」（约 1445–1449 行）。起本地服务再看日志，是网页任务的第一步；空日志会被当成服务没起来，然后自己改启动方式。  
   自动转入后台（`background=true`，或前台超过 `AUTO_BG_SEC`）时，若日志仍空，最多再等约 1 秒拿首包，然后再格式化返回。仍为空时说明日志可能还没写入，并提示 `job` 的 `wait_sec`。显式 `action=output` 且未传 `wait_sec` 时保持立即返回。

3. **网页验收：失败一次就停**  
   只改 `skills/网页验收.md`，不新增运行时。在现有步骤后补一条：Edge 启动失败，或页面打不开，报告原因并停。不要改 Playwright、vendor、asyncio，也不要为了找 Edge 另写补丁脚本。现有片段、1440 与 390、`channel="msedge"`、「本机没有 Edge」保持不变。  
   停机刹车这次不动。`loop.py` 里，写出用户侧文件会把无产出计数清零（约 840–848、891 行）；读了非 `temp/` 的项目文件也会把空转计数清零（约 827–828、886 行）。模型不停读技能、不停往任务目录写补丁时，`STALL_STOP_ROUNDS = 30` 到不了。若再出现一次同类跑偏，再决定要不要改刹车。

## 不改

- 已复跑转绿的文本断言，以及 W-06 的退出码噪音、O-01 的加粗正则。产品行为保持。
- 安全缺口 G1–G6（SEC-05、SEC-06、SEC-10、SEC-13、SEC-14、SEC-15）。记账中的 xfail，另案处理。
- F-09、F-10：全量跑里审批被拒，执行器与产品尚未分开。先复跑再决定。
- C-12、S-07 的 XPASS：测试集标记为待人工定性。
- 不新增 `web_accept` 脚本技能。不改 `skills/数据管理系统.md` 的目录约定，不为 O-10 增加复制工具。X-08 已按该约定交付成功。
- 不增加联网抓取工具，不把 Chromium 打进 vendor。
- 会话残缺 tool 批次复活（G26 / S-02）和顶层数组会话（G8 / S-05）留到 3.2 之后。
- 不以复跑整套测试集、或把门禁刷到 web ≥85% 之类的阈值，作为 3.2 完成的定义。

## 验收

实现时用单测锁住解码和后台首包，不依赖实模型。网页技能只检查「失败一次就停」是否写进 `skills/网页验收.md`。

- [x] 脚本技能输出含非 GBK 字节时，返回正文，stderr 无 `UnicodeDecodeError`
- [x] 技能子进程环境含 `PYTHONIOENCODING=utf-8` 与 `TASK_TEMP`
- [x] 自动转入后台且日志仍空时，最多再等约 1 秒；仍空则提示 `wait_sec`，不把空日志说成没有输出
- [x] `job action=output` 未传 `wait_sec` 时立即返回
- [x] `skills/网页验收.md` 写明：Edge 或打开页面失败一次就报告并停，不改 Playwright / vendor / asyncio
- [x] 上述改动落地后，再把 `ui.APP_VERSION`、README、CHANGELOG 升到 3.2
