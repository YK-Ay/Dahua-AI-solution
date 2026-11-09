@echo off
REM 一键初始化 venv 依赖并跑采集器（W1）
cd /d %~dp0
if not exist venv (
    python -m venv venv
)
call venv\Scripts\activate.bat
pip install -r requirements.txt
python -m scraper.collect %*
pause
