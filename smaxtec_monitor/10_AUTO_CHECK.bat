@echo off
set "SMX_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = Get-Content -LiteralPath '%~f0' -Encoding UTF8 | Select-Object -Skip 6 | Out-String; Invoke-Expression $s"
pause
exit /b
REM ===== PowerShell =====
Set-Location $env:SMX_DIR
[Console]::OutputEncoding = [Text.Encoding]::UTF8
function OK($m){ Write-Host "  [정상] $m" -ForegroundColor Green }
function NG($m){ Write-Host "  [문제] $m" -ForegroundColor Red }
function TIP($m){ Write-Host "         -> $m" -ForegroundColor Yellow }
function Info($m){ Write-Host "         $m" -ForegroundColor Gray }
$root = (Get-Location).Path.TrimEnd("\")
$codes = @{ 0 = "성공"; 267011 = "아직 한 번도 실행 안 됨"; 267009 = "실행 중"; 267014 = "사용자가 중지";
            2147942402 = "실행 파일 없음(폴더가 옮겨짐)"; 2147946720 = "조건 불충족(로그온/전원)"; 1 = "프로그램 오류(로그 확인)";
            2 = "수집 실패(로그 확인)"; 3221225786 = "창을 닫아 중단됨" }
function Code($c){ if ($codes.ContainsKey([long]$c)) { $codes[[long]$c] } else { "오류 코드 $c (로그 확인)" } }

Write-Host ""
Write-Host "========== 06시 자동 실행 점검 ==========" -ForegroundColor Cyan
Write-Host "  현재 폴더: $root"
Write-Host ""

function CheckTasks {
  $need = $false
  foreach ($name in "smxmon_daily_report", "smxmon_collect", "smxmon_app_server") {
    $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $t) {
      if ($name -eq "smxmon_app_server") { Info "$name : 미등록 (아이폰 앱을 안 쓰면 정상)"; continue }
      NG "$name : 등록 안 됨"; $need = $true; continue
    }
    $a = $t.Actions[0]
    $where = if ($a.WorkingDirectory) { $a.WorkingDirectory } else { Split-Path (Split-Path $a.Execute) }
    $where = "$where".TrimEnd("\")
    $i = Get-ScheduledTaskInfo -TaskName $name
    $last = if ($i.LastRunTime -and $i.LastRunTime.Year -gt 2000) { $i.LastRunTime.ToString("MM/dd HH:mm") } else { "없음" }
    $next = if ($i.NextRunTime) { $i.NextRunTime.ToString("MM/dd HH:mm") } else { "-" }
    if ($where -ne $root) { NG "$name : 다른 폴더를 가리킴 ($where)"; $need = $true }
    elseif ($t.State -eq "Disabled") { NG "$name : 사용 안 함 상태"; $need = $true }
    else { OK "$name : 등록됨" }
    Info "마지막 실행 $last → $(Code $i.LastTaskResult) · 다음 실행 $next"
  }
  return $need
}

Write-Host "[1] 자동 실행 등록 상태"
$need = CheckTasks
if ($need) {
  Write-Host ""
  Write-Host "  → 현재 폴더 기준으로 다시 등록합니다..." -ForegroundColor Yellow
  & powershell -NoProfile -ExecutionPolicy Bypass -File "$root\scripts\register_windows_tasks.ps1" | Out-Null
  Write-Host "  [다시 확인]"
  $null = CheckTasks
}

Write-Host ""
Write-Host "[2] 최근 결과"
$rep = Get-ChildItem "reports\smaxtec_daily_*.xlsx" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($rep) {
  $age = (Get-Date) - $rep.LastWriteTime
  if ($age.TotalHours -lt 26) { OK "마지막 리포트: $($rep.LastWriteTime.ToString('MM/dd HH:mm')) ($([int]$age.TotalHours)시간 전)" }
  else { NG "마지막 리포트가 오래됨: $($rep.LastWriteTime.ToString('MM/dd HH:mm')) ($([int]$age.TotalDays)일 전)" }
} else { NG "리포트 없음"; TIP "2_RUN_REPORT.bat 을 먼저 실행" }
if (Test-Path "logs\task_daily.log") {
  Info "자동 실행 기록(logs\task_daily.log) 마지막 줄:"
  Get-Content "logs\task_daily.log" -Encoding UTF8 -Tail 6 | ForEach-Object { Write-Host "           $_" -ForegroundColor DarkGray }
}
if (Test-Path "logs\smxmon.log") {
  $err = Get-Content "logs\smxmon.log" -Encoding UTF8 -Tail 300 | Select-String -Pattern "ERROR|수집 중단|로그인 실패|권한 오류" | Select-Object -Last 3
  if ($err) { NG "최근 오류 기록:"; $err | ForEach-Object { Write-Host "           $($_.Line)" -ForegroundColor DarkYellow } }
  else { OK "최근 오류 기록 없음" }
}

Write-Host ""
Write-Host "[3] 전원 (노트북 절전 중 06:00 에 깨어나는지)"
$bat = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue
if ($bat) {
  if ($bat.BatteryStatus -eq 2) { OK "충전기 연결됨" } else { NG "배터리 사용 중 → 밤새 충전기를 꽂아 두세요" }
}
$q = (powercfg /query SCHEME_CURRENT SUB_SLEEP RTCWAKE) -join "`n"
$vals = [regex]::Matches($q, "0x0000000(\d)") | ForEach-Object { [int]$_.Groups[1].Value }
if ($vals.Count -ge 2) {
  $ac = $vals[$vals.Count - 2]; $dc = $vals[$vals.Count - 1]
  $txt = @{0 = "사용 안 함"; 1 = "사용"; 2 = "중요한 것만"}
  if ($ac -eq 1) { OK "절전 해제 타이머(충전 중): 사용" }
  else {
    NG "절전 해제 타이머(충전 중): $($txt[$ac]) → 06:00 에 절전에서 깨어나지 못함"
    $ans = Read-Host "         충전 중일 때 절전 해제 타이머를 '사용'으로 바꿀까요? (Y/N)"
    if ($ans -match "^[Yy]") {
      powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1 | Out-Null
      powercfg /setactive SCHEME_CURRENT | Out-Null
      OK "변경했습니다 (충전 중 절전 해제 허용)"
    }
  }
  Info "배터리 사용 중: $($txt[$dc]) (배터리로는 깨우지 않는 것이 기본 — 충전기 연결 권장)"
} else { Info "절전 해제 타이머 설정을 읽지 못함 (제어판 > 전원 옵션 > 고급 설정 > 절전 > 절전 해제 타이머 허용)" }
Info "노트북 덮개를 닫으면 '최대 절전'이나 종료로 바뀌는 설정이면 깨어나지 못할 수 있습니다. PC 를 완전히 끄면 실행되지 않습니다."

Write-Host ""
$ans = Read-Host "[4] 지금 06시 작업을 시험 실행할까요? 수집·리포트까지 몇 분 걸립니다 (Y/N)"
if ($ans -match "^[Yy]") {
  Start-ScheduledTask -TaskName "smxmon_daily_report"
  Write-Host "  실행 중" -NoNewline
  $t0 = Get-Date
  do { Start-Sleep 10; Write-Host "." -NoNewline; $st = (Get-ScheduledTask -TaskName "smxmon_daily_report").State }
  while ($st -eq "Running" -and ((Get-Date) - $t0).TotalMinutes -lt 30)
  Write-Host ""
  $i = Get-ScheduledTaskInfo -TaskName "smxmon_daily_report"
  if ($i.LastTaskResult -eq 0) { OK "시험 실행 성공 → 앱/리포트가 방금 시각으로 바뀌었는지 확인하세요" }
  else { NG "시험 실행 결과: $(Code $i.LastTaskResult)"; TIP "이 창과 logs\task_daily.log 내용을 캡처해 보내주세요" }
  if (Test-Path "logs\task_daily.log") { Get-Content "logs\task_daily.log" -Encoding UTF8 -Tail 8 | ForEach-Object { Write-Host "           $_" -ForegroundColor DarkGray } }
}
Write-Host ""
