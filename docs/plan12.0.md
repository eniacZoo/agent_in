# agent_in v12.0 — vendor 真能用、禁止 pip、补办公流程 md

> 前置：plan9（vendor 打包）+ plan11。
> 根因：PYTHONPATH 只给 `/use` 脚本；模型不知道有 python-pptx；`pip install` 不拦。20 轮不动。不新增 TOOLS。

## 明确不做

第 6 个工具、`skill_*`、新的 `/use` 脚本、把 20 改大、改 Cursor 计划文件。

## 结论

1. 读 pptx 空转是 `import pptx` 失败 → pip（无网）→ 手解 XML，不是 20 轮太少。
2. vendor 里有包，但交互 `shell` 看不到。
3. plan9 只有周报 md 和人用的 `read_xlsx`，没有「读 pptx」流程，system prompt 也不列文件名。

## 改什么

1. **shell** 把 `vendor/` 插到 `PYTHONPATH`，并设 `PYTHONIOENCODING=utf-8`。
2. **规则** `pkg_install`：pip/conda/npm install 在 SAFE_MODE 下阻断；文案指向 vendor。
3. **流程 md**：`读pptx.md` / `读xlsx.md` / `读docx.md` / `读pdf.md`；系统提示一行索引，不注入全文。

## 开发任务

- [x] `plan12.0.md` 落盘
- [x] shell 带 vendor PYTHONPATH
- [x] 阻断 pip/conda/npm install
- [x] 四个办公 md + 系统提示索引
- [x] `_test_plan12.py`

## 成功标准

`python -c "import pptx"` 经 `_exec_shell` 成功。`pip install` 被拦。读桌面 pptx 数轮内出分析，不再解 XML 打满 20。
