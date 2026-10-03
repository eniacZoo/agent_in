@echo off
cd /d "%~dp0"
if not exist frontend\vendor\vue.global.prod.js (
  echo 缺少 frontend\vendor\vue.global.prod.js
  echo 请从 agent_in\vendor\web\ 复制 vue.global.prod.js 和 echarts.min.js 到 frontend\vendor\
  exit /b 1
)
if "%AGENT_VENDOR%"=="" (
  echo 请先设置 AGENT_VENDOR 为 agent_in\vendor 的绝对路径
  exit /b 1
)
set PYTHONPATH=%AGENT_VENDOR%;%PYTHONPATH%
py -3.11 -c "import fastapi, uvicorn" 1>nul 2>nul
if errorlevel 1 (
  echo 当前 Python 加载不了 vendor 里的 fastapi。请用 py -3.11，并确认 AGENT_VENDOR 指向 agent_in\vendor
  exit /b 1
)
start "data-admin" py -3.11 -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
echo 等待 http://127.0.0.1:8000/
ping -n 3 127.0.0.1 >nul
start "" http://127.0.0.1:8000/
