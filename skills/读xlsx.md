# 读 Excel（.xlsx）

人读、人改。模型用 `read_file` 打开本文件，再用现有 5 个工具做完。不要调用不存在的 skill 工具。

## 输入

- `.xlsx` 路径

## 步骤

1. 确认文件存在。
2. `shell` 用 vendor 里的 openpyxl 读第一张表（前几十行即可）：

```
python -c "from openpyxl import load_workbook; wb=load_workbook(r'路径.xlsx', read_only=True, data_only=True); ws=wb.active
[print('\\t'.join('' if c is None else str(c) for c in row)) for i,row in enumerate(ws.iter_rows(values_only=True)) if i<40]"
```

3. 人也可以 `/use read_xlsx 路径.xlsx`（脚本 skill，不进工具表）。模型不要等用户敲 `/use`。
4. 总结列含义、异常空行、明显合计错误。不要编造没读到的数字。

## 坑

- **禁止** pip/conda/npm install。
- 不要用 pandas，除非用户明确要求且 import 已成功。
- 公式格用 `data_only=True` 可能是 None（没在 Excel 里算过）。

## 如何验收

- 表头和样例行能对上文件。
- 工具表仍是 5 个。
