@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ================================================
echo   smaXtec 모니터 설치 (이 창은 닫지 말고 기다리세요)
echo ================================================
echo.
set PY=
python --version >nul 2>nul && set PY=python
if not defined PY ( py --version >nul 2>nul && set PY=py )
if not defined PY (
  echo [오류] Python 이 설치되어 있지 않습니다.
  echo  1. 지금 열리는 사이트에서 Python 을 내려받아 설치하세요.
  echo  2. 설치 첫 화면 아래 "Add python.exe to PATH" 를 꼭 체크하세요.
  echo  3. 설치가 끝나면 이 파일을 다시 더블클릭하세요.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)
%PY% --version
echo.
echo [1/3] 필요한 프로그램 설치 중... (1~3분)
%PY% -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
  echo [오류] 설치 실패. 이 창 내용을 캡처해서 보내주세요.
  pause
  exit /b 1
)
echo      완료
echo.
if not exist .env (
  copy .env.example .env >nul
  echo [2/3] 메모장이 열립니다.
  echo      SMX_USERNAME= 뒤에 아이디, SMX_PASSWORD= 뒤에 비밀번호를 입력하고
  echo      저장^(Ctrl+S^) 후 메모장을 닫으세요.
  notepad .env
) else (
  echo [2/3] 설정 파일^(.env^) 이 이미 있습니다. 수정하려면 4_SETTINGS.bat 실행
)
echo.
echo [3/3] smaXtec 접속 확인 중...
%PY% -m smxmon api-test --limit 1
echo.
echo ================================================
echo  위에 "로그인 성공" 이 보이면 설치 완료입니다.
echo  다음: 2_RUN_REPORT.bat 더블클릭
echo ================================================
pause
