# PowerPoint（.pptx）

人读、人改。模型用 `read_file` 打开本文件，再用现有 5 个工具做完。不要调用不存在的 skill 工具。长脚本 `timeout` 用 120。

## 何时加载

`.pptx` 读幻灯片、改要点、生成演示文稿。

## 读

确认文件存在。vendor 的 python-pptx 抽每页标题/正文。摘要写入 `temp/task_notes.md`（页数、标题列表）。不要把每页全文打进对话。看图时列出图片再 `view_image`。

## 改

写一份 `temp/*.py` 改或生成。不要把 pptx 当 zip 解 XML。产物到用户指定路径；过程文件进 temp/。禁止 pip。

## 校验

幻灯片标题/要点能对上要求。没有 pip、没有解压到 TEMP。

## 坑

桌面路径展开 `%USERPROFILE%\Desktop`。不要把中文逗号当成文件名。工具表仍是 5 个。
