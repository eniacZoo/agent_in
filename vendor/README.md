# vendor/

脚本 skill 运行时会把本目录加进 `PYTHONPATH`。核心 5 个工具不从这里 import。

当前 vendor 原生扩展是 CPython **3.14** win_amd64（2026-10-10 起，同版本轮子替换了原来的 cp311）。请用 Python 3.14 启动 `agent.py`。换别的版本要重装 numpy / pandas / lxml / greenlet / Pillow / pydantic-core。

## 已打入（实测 2026-09-09）

源码约 0.7MB + vendor **~220MB**。办公套件齐：xlsx / docx / pptx / pdf + pandas + Playwright 驱动。数据管理脚手架另带 FastAPI 与免构建前端。

| 包 | 版本 | 约体积 |
|----|------|--------|
| playwright | 1.62.0 | 104MB（含驱动，**不含**浏览器） |
| pandas + numpy | 3.0.5 / 2.4.6 | 73MB |
| Pillow | 12.3.0 | 14MB（pptx 依赖） |
| python-docx + lxml | 1.2.0 / 6.1.3 | ~10MB |
| fastapi + uvicorn + starlette + pydantic | 0.142.2 / 0.54.0 / 1.7.0 / 2.13.5 | ~5MB（pydantic-core 为 cp314 win_amd64） |
| pypdf | 6.18.0 | ~2MB |
| python-pptx | 1.0.2 | ~1.2MB |
| openpyxl + et_xmlfile | 3.1.5 | ~1MB |
| XlsxWriter | 3.2.9 | ~0.8MB |

`vendor/web/` 是前端单文件，不进 PYTHONPATH：

| 文件 | 版本 | 约体积 |
|------|------|--------|
| vue.global.prod.js | Vue 3.5.22 | 156KB |
| echarts.min.js | ECharts 5.6.0 | 1.0MB |

脚手架 `skills/templates/webapp/` 启动时把这两份复制到交付项目的 `frontend/vendor/`。字体用系统字体栈，不走 CDN。

## 未打

Playwright 浏览器本体。验收自己做的页面时用工具 `preview_page`（系统 Edge，1440 与 390）。不要默认执行 `playwright install chromium`，也不要自己写 Playwright。

本机已有的 WPS / Word / `soffice` / Edge 用 `shell` 调即可。
