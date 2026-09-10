# 周报转 Word

人读、人改。先 `read_file skills/docx.md`，再用现有 5 个工具做完。不要调用不存在的 skill 工具。

## 输入

- 本周工作记录：markdown / 记事本 / 聊天粘贴
- 默认工作目录下的文件；相对路径即可

## 步骤

1. `read_file` 读原始记录。没有文件就请用户贴内容，不要编造业绩。
2. 整理成固定四段，先 `write_file` 写成 `周报.md`：
   - 本周完成
   - 风险与阻塞
   - 下周计划
   - 需要协调
3. 转 Word（有什么用什么，不要假设 Linux 已装包）：
   - 本机有 `soffice`：`shell` 执行 `soffice --headless --convert-to docx 周报.md`
   - 本机有 `pandoc`：`pandoc 周报.md -o 周报.docx`
   - 都没有：项目 `vendor/` 里有 python-docx。写一段短脚本生成 `.docx`，运行时把 `PYTHONPATH` 指到 `vendor/`（lxml 是 CPython 3.11 win_amd64）
   - 再没有：保留 `周报.md`，告诉用户用 WPS / Word 打开另存为 `.docx`
4. 不要删除原始记录。不要对 `C:\Windows` 或工作目录外写文件。

## 坑

- Windows 控制台可能是 GBK：读盘已按 utf-8 → gb18030 兜底，不要再手动转码一遍。
- 用户没说「覆盖」时，已存在的 `周报.docx` 会被确认拦截，改用带日期的文件名。
- 不要用 pandas / Playwright。不要用 shell 做删除。

## 如何验收

- 打开结果：有标题、有上述四段、没有空白套话。
- 原始记录还在。
- 工具表仍是 5 个：read_file / write_file / edit_file / shell / view_image。
