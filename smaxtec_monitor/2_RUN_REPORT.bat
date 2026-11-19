@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=python
python --version >nul 2>nul || set PY=py
echo 최신 데이터 수집 중... (처음에는 5분 정도 걸립니다)
%PY% -m smxmon collect
echo.
echo 리포트 만드는 중...
%PY% -m smxmon report
echo.
echo 완료. reports 폴더를 엽니다.
start "" "%~dp0reports"
pause
