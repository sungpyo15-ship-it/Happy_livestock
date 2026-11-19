@echo off
REM Windows 작업 스케줄러용: 매일 아침 1회 (예: 06:50)
cd /d "%~dp0.."
if exist .venv\Scripts\python.exe (set PY=.venv\Scripts\python.exe) else (set PY=python)
%PY% -m smxmon report --email
