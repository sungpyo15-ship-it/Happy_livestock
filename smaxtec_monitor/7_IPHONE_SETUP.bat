@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ================================================
echo   아이폰 앱 설정 (Tailscale)
echo ================================================
echo.
set TS=tailscale
where tailscale >nul 2>nul || set TS="C:\Program Files\Tailscale\tailscale.exe"
%TS% version >nul 2>nul
if errorlevel 1 (
  echo [1] Tailscale 이 설치되어 있지 않습니다. 지금 열리는 사이트에서 Windows 용을 설치하고
  echo     구글/마이크로소프트/애플 계정으로 로그인한 뒤, 이 파일을 다시 실행하세요.
  start https://tailscale.com/download/windows
  pause
  exit /b 1
)
echo [1] Tailscale 확인 완료
echo.
echo [2] PC 앱 서버를 자동 실행으로 등록합니다 (PC 켜면 자동 시작)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\register_app_server.ps1"
echo.
echo [3] 아이폰에서 접속할 주소를 만듭니다 (처음이면 HTTPS 사용 허용 링크가 나올 수 있음 - 링크를 열어 허용)
%TS% serve --bg 8765
echo.
echo ================================================
echo  위에 나온 https://....ts.net 주소를 아이폰에서 여세요.
echo   1) 아이폰 App Store 에서 Tailscale 설치, PC 와 같은 계정으로 로그인, 연결 켜기
echo   2) Safari 로 위 주소 열기
echo   3) 아래 공유 버튼 → "홈 화면에 추가"
echo  주소를 다시 보려면:  tailscale serve status
echo ================================================
pause
