@echo off
cd /d "%~dp0"
if exist "reports\smaxtec_app.html" (
  start "" "reports\smaxtec_app.html"
) else (
  echo 아직 리포트가 없습니다. 2_RUN_REPORT.bat 를 먼저 실행하세요.
  pause
)
