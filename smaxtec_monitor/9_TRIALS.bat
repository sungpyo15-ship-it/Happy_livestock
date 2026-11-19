@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=python
python --version >nul 2>nul || set PY=py
REM 사료·첨가제 투입 기록 파일 열기 (없으면 양식 생성). 저장 후 2_RUN_REPORT.bat 실행 → 앱 '투입비교' 탭
%PY% -m smxmon trials-file
