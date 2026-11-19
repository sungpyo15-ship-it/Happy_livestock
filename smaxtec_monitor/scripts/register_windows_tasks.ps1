# Windows 작업 스케줄러 등록 (PowerShell 에서 실행: powershell -ExecutionPolicy Bypass -File scripts\register_windows_tasks.ps1)
# - 수집: 매일 06:10 부터 2시간 간격 (분을 정각에서 비켜 서버 혼잡 회피)
# - 리포트: 매일 06:50
$root = Split-Path -Parent $PSScriptRoot
$collect = New-ScheduledTaskAction -Execute "$root\scripts\run_collect.bat" -WorkingDirectory $root
$report  = New-ScheduledTaskAction -Execute "$root\scripts\run_daily_report.bat" -WorkingDirectory $root
$t1 = New-ScheduledTaskTrigger -Once -At "06:10" -RepetitionInterval (New-TimeSpan -Hours 2)
$t2 = New-ScheduledTaskTrigger -Daily -At "06:50"
$opt = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 50)
Register-ScheduledTask -TaskName "smxmon_collect" -Action $collect -Trigger $t1 -Settings $opt -Force
Register-ScheduledTask -TaskName "smxmon_daily_report" -Action $report -Trigger $t2 -Settings $opt -Force
Write-Host "등록 완료. 작업 스케줄러에서 smxmon_* 확인"
