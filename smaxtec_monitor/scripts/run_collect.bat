@echo off
chcp 65001 >nul
REM 2시간 간격 수집 (실행 기록: logs\task_collect.log)
cd /d "%~dp0.."
if not exist logs mkdir logs
set PYTHONUTF8=1
set PY=python
python --version >nul 2>nul || set PY=py
if exist .venv\Scripts\python.exe set PY=.venv\Scripts\python.exe
echo ===== %date% %time% collect start >> logs\task_collect.log
%PY% -m smxmon collect --notify >> logs\task_collect.log 2>&1
echo ===== %date% %time% collect end (exit %errorlevel%) >> logs\task_collect.log
