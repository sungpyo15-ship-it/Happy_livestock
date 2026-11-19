@echo off
set "SMX_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = Get-Content -LiteralPath '%~f0' -Encoding UTF8 | Select-Object -Skip 6 | Out-String; Invoke-Expression $s"
pause
exit /b
REM ===== PowerShell =====
Set-Location $env:SMX_DIR
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
function OK($m){ Write-Host "  [정상] $m" -ForegroundColor Green }
function NG($m){ Write-Host "  [문제] $m" -ForegroundColor Red; $script:bad++ }
function TIP($m){ Write-Host "         -> $m" -ForegroundColor Yellow }
$script:bad = 0
Write-Host ""
Write-Host "========== 아이폰 연결 점검 ==========" -ForegroundColor Cyan
Write-Host ""

# 1. 앱 파일
Write-Host "[1] 앱 파일"
if (Test-Path "reports\app\index.html") { OK "앱 파일 있음" } else { NG "앱 파일 없음"; TIP "2_RUN_REPORT.bat 를 먼저 실행하세요" }

# 2. PC 앱 서버
Write-Host "[2] PC 앱 서버 (127.0.0.1:8765)"
function TestLocal { try { (Invoke-WebRequest "http://127.0.0.1:8765/" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop).StatusCode -eq 200 } catch { $false } }
if (TestLocal) { OK "실행 중" } else {
  Write-Host "  꺼져 있어서 켭니다..."
  Start-ScheduledTask -TaskName smxmon_app_server -ErrorAction SilentlyContinue; Start-Sleep 5
  if (-not (TestLocal)) {
    $py = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
    if (-not $py) { $py = (Get-Command pyw -ErrorAction SilentlyContinue).Source }
    if ($py) { Start-Process $py -ArgumentList "-m smxmon serve" -WorkingDirectory (Get-Location) -WindowStyle Hidden; Start-Sleep 5 }
  }
  if (TestLocal) { OK "서버를 켰습니다" } else { NG "서버를 켤 수 없음"; TIP "6_APP_SERVER.bat 를 실행해서 나오는 화면을 캡처해 보내주세요" }
}

# 3. PC Tailscale
Write-Host "[3] PC Tailscale"
$ts = (Get-Command tailscale -ErrorAction SilentlyContinue).Source
if (-not $ts -and (Test-Path "C:\Program Files\Tailscale\tailscale.exe")) { $ts = "C:\Program Files\Tailscale\tailscale.exe" }
if (-not $ts) { NG "Tailscale 이 설치되어 있지 않음"; TIP "tailscale.com/download 에서 Windows 용 설치 후 로그인"; exit }
[Console]::OutputEncoding = [Text.Encoding]::UTF8   # 한글 이름이 깨져 JSON 해석이 실패하는 문제 방지
$raw = (& $ts status --json 2>$null) -join "`n"
function Pick($pat) { if ($raw -match $pat) { $Matches[1] } else { "" } }
$state = Pick '"BackendState":\s*"([^"]*)"'
$acct  = Pick '"LoginName":\s*"([^"]*)"'
$name  = (Pick '"DNSName":\s*"([^"]*)"').TrimEnd(".")      # 첫 DNSName = 이 PC
$mdns  = Pick '"MagicDNSEnabled":\s*(true|false)'
if ($state -eq "Running") { OK "연결됨 (계정: $acct)" } else { NG "연결 안 됨 (상태: $state)"; TIP "작업표시줄의 Tailscale 아이콘 -> 로그인/Connect"; exit }
if ($mdns -eq "true") { OK "MagicDNS 켜짐" } elseif ($mdns -eq "false") {
  NG "MagicDNS 꺼짐 -> 아이폰이 주소를 찾지 못함"
  TIP "https://login.tailscale.com/admin/dns 에서 [Enable MagicDNS] 와 [Enable HTTPS] 를 누른 뒤 이 파일을 다시 실행"
}
if (-not $name) { NG "PC 주소(이름)를 읽지 못함"; TIP "PowerShell 에서 'tailscale status' 결과를 캡처해 보내주세요" }

# 4. 아이폰 (기기 목록 텍스트에서 iOS 기기 확인)
Write-Host "[4] 아이폰"
$ios = @((& $ts status 2>$null) | Where-Object { $_ -match "\sios\s" })
if ($ios.Count -eq 0) { NG "이 계정($acct)에 아이폰이 없음"; TIP "아이폰 Tailscale 앱에서 PC 와 같은 계정($acct, 구글)으로 로그인" }
foreach ($line in $ios) {
  $hn = ($line -split "\s+")[1]
  if ($line -match "offline") { NG "아이폰($hn) 꺼짐"; TIP "아이폰 Tailscale 앱을 열고 맨 위 스위치를 켜서 Connected 로" } else { OK "아이폰 연결됨: $hn" }
}

# 5. 아이폰용 주소(HTTPS)
Write-Host "[5] 아이폰용 주소"
$sv = (& $ts serve status 2>&1) | Out-String
if ($sv -notmatch "8765") {
  Write-Host "  주소를 설정합니다..."
  (& $ts serve --bg 8765 2>&1) | ForEach-Object { Write-Host "  $_" }
  $sv = (& $ts serve status 2>&1) | Out-String
}
if ($sv -match "8765") { OK "설정됨" } else { NG "설정 실패"; TIP "위에 https://login.tailscale.com/... 링크가 보이면 열어서 HTTPS 를 허용한 뒤 다시 실행" }
Write-Host "  PC 에서 주소 열기 시험 중 (처음엔 인증서 발급으로 최대 1분)..."
try {
  $r = Invoke-WebRequest "https://$name/" -UseBasicParsing -TimeoutSec 90 -ErrorAction Stop
  if ($r.StatusCode -eq 200) { OK "PC 에서 https://$name 정상" }
} catch {
  NG "PC 에서도 열리지 않음: $($_.Exception.Message)"
  TIP "https://login.tailscale.com/admin/dns 에서 [Enable HTTPS] 확인 후 다시 실행"
}

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host " 아이폰 Safari 주소창에 아래를 정확히 입력:"
Write-Host "    https://$name" -ForegroundColor Cyan
Write-Host ""
if ($script:bad -eq 0) {
  Write-Host " PC 쪽은 모두 정상입니다. 아이폰에서도 안 열리면:" -ForegroundColor Green
  Write-Host "  1) 아이폰 Tailscale 앱 -> 설정(톱니) -> 'Use Tailscale DNS' 켜기"
  Write-Host "  2) 아이폰 설정 -> Wi-Fi/셀룰러에서 다른 VPN·iCloud 비공개 릴레이 끄기"
  Write-Host "  3) Safari 에서 주소 앞 https:// 까지 입력했는지 확인"
} else {
  Write-Host " 빨간 [문제] 항목을 해결한 뒤 이 파일을 다시 실행하세요." -ForegroundColor Yellow
}
Write-Host ""
