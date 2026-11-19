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
$j = (& $ts status --json) | ConvertFrom-Json
$acct = $j.User."$($j.Self.UserID)".LoginName
if ($j.BackendState -eq "Running") { OK "연결됨 (계정: $acct)" } else { NG "연결 안 됨 (상태: $($j.BackendState))"; TIP "작업표시줄의 Tailscale 아이콘 -> 로그인/Connect"; exit }
$name = "$($j.Self.DNSName)".TrimEnd(".")
if ($j.CurrentTailnet.MagicDNSEnabled) { OK "MagicDNS 켜짐" } else {
  NG "MagicDNS 꺼짐 -> 아이폰이 주소를 찾지 못함"
  TIP "https://login.tailscale.com/admin/dns 에서 [Enable MagicDNS] 와 [Enable HTTPS] 를 누른 뒤 이 파일을 다시 실행"
}

# 4. 아이폰
Write-Host "[4] 아이폰"
$ios = @($j.Peer.PSObject.Properties.Value | Where-Object { $_.OS -eq "iOS" })
if ($ios.Count -eq 0) { NG "이 계정($acct)에 아이폰이 없음"; TIP "아이폰 Tailscale 앱에서 로그아웃 후 PC 와 같은 계정($acct)으로 로그인" }
foreach ($p in $ios) {
  if ($p.Online) { OK "아이폰 연결됨: $($p.HostName)" } else { NG "아이폰($($p.HostName)) 꺼짐"; TIP "아이폰 Tailscale 앱을 열고 맨 위 스위치를 켜서 Connected 로" }
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
