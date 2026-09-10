# agent_in v11.0 — 修图片任务空转

> 前置：plan10。
> 根因：Windows shell 按 GBK 读 stdout 崩溃后返回空结果；大图被 10MB 硬拒；工具行「放行」把失败藏起来。20 轮上限够用，不作为主要手段。

## 明确不做

TUI / rich / 新工具（没有 `read_exif`）。不把默认 20 轮改大。不改 Cursor 计划文件。

## 结论（已发生的问题）

1. **轮次空转**：中文逗号被当成文件名 → 原图 >10MB → `shell text=True` 默认 GBK，reader 线程 `UnicodeDecodeError`，LLM 看到空输出后反复换命令。
2. **多模态没吃到图**：失败时不写入 `PENDING_IMAGES`；UI 只显示「放行」。缩成功的那一次又被后续 EXIF/shell 吃光轮次。
3. **像 loop**：打满 20 的警告只写进 `final_text` 不打印；「请继续」是新一轮，计数归零。

## 改什么

1. **shell**  
   `capture_output` 按字节读，utf-8 → gb18030 → replace。线程不再崩。

2. **view_image**  
   超 10MB 或长边过大时用 `vendor/PIL` 缩到长边约 2048、JPEG 压进上限。Pillow 不可用才报过大。路径含 `，` 且文件不存在时提示「目录 + 文件名」。

3. **工具行**  
   结果以 `Error:` 开头时标记 `[x]`，并带错误首行。

4. **打满轮次**  
   `ui.print_warning` 打到终端。默认仍 20。

## 开发任务

- [x] `plan11.0.md` 落盘
- [x] shell 按字节解码
- [x] view_image 自动缩图 + 逗号提示
- [x] 工具行区分放行与失败
- [x] 打满轮次可见 + `_test_plan11.py`

## 成功标准

同一张大 JPG：1–3 轮出描述；终端不再刷 `UnicodeDecodeError`；失败时看到 `[x]` 而不是只有「放行」。
