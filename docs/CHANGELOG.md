# Changelog

## 2.0 — 2026-09-09

第 13 次增量之后的首个功能完整版本。

- 5 个核心工具：`read_file` / `write_file` / `edit_file` / `shell` / `view_image`（后续增量补上 `glob` / `grep`）
- SAFE_MODE、危险命令审批、`temp/` 可删沙箱
- 办公流程 md（pptx/xlsx/docx/pdf/网页）+ vendor 离线包
- 会话、记忆、能力探测、provider 切换
- 密钥只放本机 `agent_config.json`，不进版本库

2026-09-10 整理：开发期 `_test_*.py` 归入 `tests/`；`plan*.md`、目录结构与本 Changelog 归入 `docs/`。`README.md` 留在根目录。
