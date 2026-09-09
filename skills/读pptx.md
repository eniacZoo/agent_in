# 读 PowerPoint（.pptx）

人读、人改。模型用 `read_file` 打开本文件，再用现有 5 个工具做完。不要调用不存在的 skill 工具。

## 输入

- `.pptx` 路径（桌面请展开 `%USERPROFILE%\Desktop`）
- 不要把中文逗号 `，` 当成文件名的一部分

## 步骤

1. 确认文件存在（`Test-Path` 或 `os.path.isfile`）。
2. `shell` 抽文本。`PYTHONPATH` 已含项目 `vendor/`，直接：

```
python -c "from pptx import Presentation; p=Presentation(r'路径.pptx');
[print('--- slide',i+1,'---'); print('\\n'.join(s.text.strip() for s in sl.shapes if getattr(s,'text',None))) for i,sl in enumerate(p.slides)]"
```

3. 需要看图时：列出幻灯片里的图片，再 `view_image`（超 10MB 会自动缩）。
4. 用 1–2 段话总结结构、要点、缺页/乱码。不要编造没抽到的字。

## 坑

- **禁止** `pip` / `conda` / `npm install`。离线，包已在 `vendor/`（python-pptx）。
- **不要** 把 pptx 当 zip 解 XML；那是空转。
- 不要 `Remove-Item -Recurse`。工作目录外的只读可以，不要往桌面乱写。

## 如何验收

- 幻灯片标题/正文能对上文件。
- 没有 pip、没有解压到 TEMP。
- 工具表仍是 5 个。
