# agent_in 3.3

极简 CLI Agent。12 个工具：`read_file`、`write_file`、`edit_file`、`shell`、`python`、`preview_page`、`job`、`todo_write`、`ask_user`、`view_image`、`glob`、`grep`。

当前产品版本 **3.3**（vendor 原生扩展为 CPython 3.14；temp 里的任务脚本算产出，并留到任务结束再清理；自己做的页面用 `preview_page` 打开 Edge 截图）。启动横幅、`python agent.py -V`、`/status` 显示同一版本号。

技能是文件，不是新 function。模型默认看不见 `/use`。

```text
python agent.py
python agent.py "列出当前目录文件"
python agent.py -V
```

首次使用：复制 `agent_config.example.json` 为 `agent_config.json`，填入 API Key。本机已有的配置文件不会进 git。

目录分层见 [`docs/目录结构.md`](docs/目录结构.md)。版本记录见 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)。

## 技能

| 类型 | 位置 | 谁触发 |
|------|------|--------|
| markdown 流程 | `skills/*.md` | 人对 `/read-skill`，或模型 `read_file` |
| 脚本 skill | `skills/<name>/skill.json` + entry | 人对 `/use <name>`；首次执行会确认 |

示范：

- `/read-skill 周报转docx` — 打印流程
- `/use read_xlsx 表.xlsx` — 读第一张表前若干行（优先 vendor 里的 openpyxl）

`/ls skills` 列出两类。不要对模型注册 `skill_*`。

`vendor/` 办公套件：openpyxl、python-docx、python-pptx、pypdf、pandas、Playwright，以及 FastAPI / uvicorn / pydantic（原生扩展为 cp314）。前端单文件在 `vendor/web/`（Vue 3、ECharts），不经 npm。脚本 skill 运行时自动加 `PYTHONPATH`。Playwright **不含** Chromium；验收页面用 `preview_page`（系统 Edge）。详见 `vendor/README.md`。

## 文档与测试

| 位置 | 内容 |
|------|------|
| `README.md`（根目录） | 本文件，打开仓库就能读 |
| `docs/` | 各版 `plan*.md`、办公专项、目录结构、Changelog、开发笔记 |
| `tests/` | 开发期验收脚本 `_test_plan*.py` 等，不是对外测试套件 |

跑某一阶段的验收（在仓库根目录）：

```text
python tests/_test_plan27.py
python -m unittest discover -s tests -p "_test_plan*.py"
```
