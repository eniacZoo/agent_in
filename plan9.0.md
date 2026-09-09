# agent_in v9.0 — 可选成长层（默认可以不做）

> 前置：plan7 + plan8。
> 按 PI：成长在核心外面。模型默认工具表仍是 5 个。
> **本阶段默认跳过。** 有明确办公流程再做。

## 原则

- 流程写成 markdown（人读、人改），模型用已有 `read_file` 打开，**不注册新 function**。
- 真要跑第三方库：脚本放 `skills/<name>/`，依赖放 `vendor/`，人用 `/use <name>` 调。
- 不做学习环、不做 skill_manage、不做 SOUL.md、不做 Skills Hub。

## 若做：markdown 流程

```
skills/
  周报转docx.md      # 步骤 + 坑 + 如何验收
  合并excel.md
```

system prompt **不**注入这些文件。用户说「按周报流程做」时，模型 `read_file` 该 md，再调 write/edit/shell。

可选：斜杠 `/read-skill 周报转docx` 只是 `read_file` 的糖，不算新工具（CLI 命令，不进 TOOLS）。

## 若做：vendor 脚本 skill

沿用现有 `skill.json` + entry。`/use` 已有。`skill_scanner` 首次执行仍走确认。

体积闸：

| 包 | 预估 | 决策 |
|----|------|------|
| sqlite3 | 0（stdlib） | 需要会话检索再考虑；不做 FTS 平台 |
| python-docx / openpyxl / python-pptx / pypdf | 数 MB | 先称重，合计进 15MB 再打进 `vendor/` |
| pandas | 大 | 不做 |
| Playwright / browser-use | 远超 15MB | **必须问过再装**；默认可选「不控浏览器」或「只连本机已装 Chrome 的最小 CDP」 |

`PYTHONPATH` 含 `vendor/`。办公机已有的 soffice/WPS 用 shell 调，不打包。

材料：[plan_office_2.0.md](plan_office_2.0.md) 只引用，不按它的 Linux 已装包假设执行。

## 开发任务（仅当决定做时）

- [x] 称 `vendor` wheel 体积，超标停下
- [x] 1 个示范 markdown 流程 + 1 个 `/use` 脚本 skill（例如 xlsx 读表）
- [x] 确认 `TOOLS` 仍无 `skill_*`
- [x] README 写清：核心 5 工具；技能是文件；`/use` 是人触发

## 成功标准

核心体积与行为与 v8 一致。多出来的只在 `skills/` 与 `vendor/`。没有自动写 skill。
