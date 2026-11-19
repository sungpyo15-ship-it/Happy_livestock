@echo off
REM Windows 작업 스케줄러용: 2시간마다 실행 (README 참고)
cd /d "%~dp0.."
if exist .venv\Scripts\python.exe (set PY=.venv\Scripts\python.exe) else (set PY=python)
%PY% -m smxmon collect --notify
