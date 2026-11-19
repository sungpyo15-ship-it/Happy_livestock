# Windows 작업 스케줄러 등록
#   실행: PowerShell 에서  powershell -ExecutionPolicy Bypass -File scripts\register_windows_tasks.ps1
# - smxmon_daily_report : 매일 06:00  수집 → 리포트·방문요약·모바일 대시보드 (→ 공유폴더/메일)
# - smxmon_collect      : 08:10 부터 2시간 간격 수집 (낮 동안 긴급 집단신호 메일, --notify)
# - 절전 상태면 깨워서 실행(WakeToRun). 꺼져 있으면 다음에 켜질 때 바로 실행(StartWhenAvailable)
$root = Split-Path -Parent $PSScriptRoot
$report  = New-ScheduledTaskAction -Execute "$root\scripts\run_daily_report.bat" -WorkingDirectory $root
$collect = New-ScheduledTaskAction -Execute "$root\scripts\run_collect.bat" -WorkingDirectory $root
$tReport  = New-ScheduledTaskTrigger -Daily -At "06:00"
$tCollect = New-ScheduledTaskTrigger -Once -At "08:10" -RepetitionInterval (New-TimeSpan -Hours 2)
$opt = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -DontStopIfGoingOnBatteries `
       -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 50)
Register-ScheduledTask -TaskName "smxmon_daily_report" -Action $report -Trigger $tReport -Settings $opt -Force
Register-ScheduledTask -TaskName "smxmon_collect" -Action $collect -Trigger $tCollect -Settings $opt -Force
Write-Host "등록 완료: 작업 스케줄러 > 작업 스케줄러 라이브러리 > smxmon_* 확인"
Write-Host "지금 시험 실행: Start-ScheduledTask -TaskName smxmon_daily_report"
