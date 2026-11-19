@echo off
chcp 65001 >nul
REM 매일 06:00: 최신 수집 -> 리포트·방문요약·투입비교·앱 생성 -> (설정 시) 공유폴더 복사·메일
REM 실행 기록: logs\task_daily.log
cd /d "%~dp0.."
if not exist logs mkdir logs
set PYTHONUTF8=1
set PY=python
python --version >nul 2>nul || set PY=py
if exist .venv\Scripts\python.exe set PY=.venv\Scripts\python.exe
echo ===== %date% %time% daily start (PY=%PY%, dir=%cd%) >> logs\task_daily.log
%PY% -m smxmon collect >> logs\task_daily.log 2>&1
echo ----- collect exit %errorlevel% >> logs\task_daily.log
%PY% -m smxmon report --email >> logs\task_daily.log 2>&1
echo ===== %date% %time% daily end (report exit %errorlevel%) >> logs\task_daily.log
