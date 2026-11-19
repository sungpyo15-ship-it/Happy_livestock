@echo off
REM Windows 작업 스케줄러용: 2시간마다 실행 (README 참고)
cd /d "%~dp0.."
set PY=python
where python >nul 2>nul || set PY=py
if exist .venv\Scripts\python.exe set PY=.venv\Scripts\python.exe
%PY% -m smxmon collect --notify
