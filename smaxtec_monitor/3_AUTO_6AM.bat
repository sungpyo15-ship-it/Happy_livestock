@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 매일 06:00 자동 실행을 등록합니다...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\register_windows_tasks.ps1"
echo.
echo 등록이 끝났습니다. PC 를 끄지 말고 절전 상태로 두면 매일 06:00 에 리포트가 만들어집니다.
pause
