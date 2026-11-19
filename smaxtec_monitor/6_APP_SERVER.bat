@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM 아이폰 앱용 PC 내부 서버 (창을 닫으면 서버도 꺼짐. 자동 실행은 7_IPHONE_SETUP.bat 이 등록)
set PY=python
python --version >nul 2>nul || set PY=py
%PY% -m smxmon serve
