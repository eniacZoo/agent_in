# agent_in v29.0 / 产品 3.3 — 3.14 轮子、脚本产出、页面预览

> 前置：plan28（产品 3.2）。产品版本升为 **3.3**。`ui.APP_VERSION`、README、CHANGELOG、目录结构、`vendor/README.md` 已同步。
> 办公机默认 `py` 是 Python 3.14。vendor 原来的原生扩展是 cp311，docx 和 Playwright 会在导入时失败，模型接着去全盘找解释器。

## 要做

1. **换成 Python 3.14 的轮子，不再回退 3.11**  
   lxml 6.1.3、greenlet 3.5.5、numpy 2.4.6、pandas 3.0.5、Pillow 12.3.0、pydantic-core 2.46.5 换成同版本 `cp314-win_amd64`。Playwright 1.62.0 本身是纯 Python，不换包。不保留 cp311。  
   `python` 工具和 shell 用启动本进程的解释器。删掉 `py -3.11` 查找，以及启动时切回 3.11。导入失败时只报告当前解释器，并写明不要搜索其他 python。

2. **temp 里的任务脚本算产出**  
   `write_file` / `edit_file` 成功写下 temp 里的 `.py` `.ps1` `.bat` `.cmd`，以及 `python` 工具留下的脚本，计入界面上的「产出」，并清掉空转计数。笔记、json、日志、截图不计。用户侧文件仍按原来的规则计。Qwen / `office*` 硬上限仍是 200。

3. **脚本留到这次任务结束**  
   `python` 工具不再在成功后立刻删除 `run/*.py`。结果里给出路径，要求改这个文件。本轮模型不再调工具、且没有未完成待办时，仍由 `clean_task` 清理。

4. **preview_page**  
   固定脚本 `web_preview.py`，用当前解释器打开系统 Edge，视口 1440×900 和 390×844，截图写到任务临时目录。有视觉能力时把图送进下一轮；没有时附上正文摘要。Edge 不存在或页面打不开：报告原因并停。不要自己写 Playwright。`skills/网页验收.md` 改为只调这个工具。

## 不改

- 两个 agent、不同工作目录：temp 按 `{工作目录}/temp/tasks/<session_id>/` 分开，工具状态在进程内。不改共享的 memory、日志、能力缓存。
- sub-agent 本轮不做。串行大约 2–4 人日，不增加模型连接。并行还会碰到本地 27B 一次只服务一条的限制，第二条可能排队或返回 503/429。
- 旧的 `docs/plan*.md` 不回头改。

## 验收

- [x] vendor 里不再有 cp311 的 `.pyd`；`python` 工具使用 `sys.executable`，不再调用 `py -3.11`
- [x] 成功的 `python` 调用留下 `run/*.py`；`clean_task` 才会清掉
- [x] temp 里的 `.py` 计为产出；`.json` 不计。Qwen 档硬上限仍是 200
- [x] `preview_page` 启动 `web_preview.py`；连接失败的文案要求停止，且不包含「改 Playwright」
- [x] README、目录结构、vendor/README、CHANGELOG 与 `ui.APP_VERSION` 都是 3.3
