@echo off
REM 启动 Web 界面（完全离线可用；端口 8080，8000 被本机代理占用）
REM 断网环境建议在 .env 里加一行 APP_OFFLINE=1，跳过所有联网探测。
cd /d %~dp0
setlocal

set PY=python
if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python314\python.exe"

%PY% -c "import fastapi, uvicorn, docx, PIL" >nul 2>&1
if errorlevel 1 (
  echo [错误] 当前 Python 缺少依赖：fastapi / uvicorn / python-docx / Pillow
  echo 请先安装依赖，例如：
  echo   %PY% -m pip install -r requirements.txt
  echo 提示：本项目已装好依赖的解释器通常是 Python 3.14
  pause
  exit /b 1
)

echo 正在启动 http://127.0.0.1:8080 ...
echo 离线可用：工作台 / 知识库 / 智能问答（本地检索摘要） / 竞品对比 / Agent 工作台（规则解析） / Word 导出
echo 需要联网的只有：一键同步、在线大模型生成、官网来源链接
%PY% -m uvicorn app.api:app --host 127.0.0.1 --port 8080
pause
