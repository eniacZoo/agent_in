# vendor/

脚本 skill 运行时会把本目录加进 `PYTHONPATH`。核心 5 个工具不从这里 import。

当前解释器：CPython **3.11** win_amd64。换 3.13 要重装 numpy / pandas / lxml / greenlet / Pillow 等原生 wheel。

## 已打入（实测 2026-09-09）

源码约 0.7MB + vendor **~208MB**。办公套件齐：xlsx / docx / pptx / pdf + pandas + Playwright 驱动。

| 包 | 版本 | 约体积 |
|----|------|--------|
| playwright | 1.62.0 | 104MB（含驱动，**不含**浏览器） |
| pandas + numpy | 3.0.5 / 2.4.6 | 73MB |
| Pillow | 12.3.0 | 14MB（pptx 依赖） |
| python-docx + lxml | 1.2.0 / 6.1.3 | ~10MB |
| pypdf | 6.18.0 | ~2MB |
| python-pptx | 1.0.2 | ~1.2MB |
| openpyxl + et_xmlfile | 3.1.5 | ~1MB |
| XlsxWriter | 3.2.9 | ~0.8MB |

## 未打

Playwright 浏览器本体。需要时：`PYTHONPATH=vendor python -m playwright install chromium`（进用户缓存，不进本目录）。

本机已有的 WPS / Word / `soffice` / Chrome 用 `shell` 调即可。
