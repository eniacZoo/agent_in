# 读 PDF（.pdf）

人读、人改。模型用 `read_file` 打开本文件，再用现有 5 个工具做完。不要调用不存在的 skill 工具。

## 输入

- `.pdf` 路径

## 步骤

1. 确认文件存在。
2. `shell` 用 vendor 里的 pypdf 抽文本：

```
python -c "from pypdf import PdfReader; r=PdfReader(r'路径.pdf'); print('pages', len(r.pages))
[print('--- page',i+1,'---'); print(p.extract_text() or '') for i,p in enumerate(r.pages)]"
```

3. 扫描件/无文字：对关键页导出图再 `view_image`，不要假装抽到了字。
4. 收据/合同：列出编号、日期、金额、当事方；缺的就写「PDF 里没有」。

## 坑

- **禁止** pip/conda/npm install。
- 不要为读 PDF 去装 pdfplumber/poppler。pypdf 不够再说。
- 大文件先读前几页，不要一次把全部二进制喂进上下文。

## 如何验收

- 页数和可见文字能对上文件。
- 工具表仍是 5 个。
