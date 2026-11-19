@echo off
REM 매일 06:00: 최신 수집 → 일일 리포트·방문요약·모바일 대시보드 생성 → (설정 시) 공유폴더 복사·메일
cd /d "%~dp0.."
set PY=python
where python >nul 2>nul || set PY=py
if exist .venv\Scripts\python.exe set PY=.venv\Scripts\python.exe
%PY% -m smxmon collect
%PY% -m smxmon report --email
