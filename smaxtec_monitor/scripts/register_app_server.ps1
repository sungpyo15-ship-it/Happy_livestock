# 아이폰 앱용 PC 내부 서버(127.0.0.1:8765)를 로그온 시 자동 실행 (창 없이 백그라운드)
$root = Split-Path -Parent $PSScriptRoot
$py = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command pyw -ErrorAction SilentlyContinue).Source }
if (-not $py) { Write-Host "pythonw 를 찾지 못했습니다. 6_APP_SERVER.bat 를 직접 실행하세요."; exit 1 }
$act = New-ScheduledTaskAction -Execute $py -Argument "-m smxmon serve" -WorkingDirectory $root
$trg = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$opt = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
       -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "smxmon_app_server" -Action $act -Trigger $trg -Settings $opt -Force | Out-Null
Start-ScheduledTask -TaskName "smxmon_app_server"
Write-Host "앱 서버 등록·시작 완료 (작업 이름: smxmon_app_server, 주소: http://127.0.0.1:8765)"
