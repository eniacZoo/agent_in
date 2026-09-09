# 读 Word（.docx）

人读、人改。模型用 `read_file` 打开本文件，再用现有 5 个工具做完。不要调用不存在的 skill 工具。

## 输入

- `.docx` 路径

## 步骤

1. 确认文件存在。
2. `shell` 用 vendor 里的 python-docx 抽段落：

```
python -c "from docx import Document; d=Document(r'路径.docx');
[print(p.text) for p in d.paragraphs if p.text.strip()]"
```

3. 有表格再遍历 `d.tables`。需要图再 `view_image`。
4. 按标题层级简述，不要编造没抽到的段落。

## 坑

- **禁止** pip/conda/npm install。包在 `vendor/`（python-docx + lxml，CPython 3.11 win_amd64）。
- **不要** 当 zip 手改 XML，除非用户明确要修底层。
- `.doc` 老格式 python-docx 打不开：说明要用 WPS/Word 另存为 `.docx`。

## 如何验收

- 正文要点能对上文件。
- 工具表仍是 5 个。
