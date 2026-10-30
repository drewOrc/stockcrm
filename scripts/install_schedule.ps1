# Register (or refresh) the Windows scheduled tasks for Stock CRM maintenance. Current user, no admin needed.
#   powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1 [-MorningAt "08:30"] [-EveningAt "19:30"] [-Remove]
param([string]$MorningAt = "08:30", [string]$EveningAt = "19:30", [switch]$Remove)

$root = Split-Path -Parent $PSScriptRoot
$ps = (Get-Command powershell.exe).Source
$names = @("StockCRM Morning", "StockCRM Evening Fetch")

if ($Remove) {
  foreach ($n in $names) { Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue }
  "removed"; exit 0
}

# Morning: backup, fetch official data, quality + health report, then the agent brief.
$morning = New-ScheduledTaskAction -Execute $ps -WorkingDirectory $root `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$root\scripts\morning.ps1`""
# Evening: official data only (monthly revenue lands after the close on days 1-10; quarterly after 5/15, 8/14, 11/14, 3/31).
$evening = New-ScheduledTaskAction -Execute $ps -WorkingDirectory $root `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$root\scripts\morning.ps1`" -SkipAgent"

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 40) `
  -MultipleInstances IgnoreNew -RunOnlyIfNetworkAvailable

Register-ScheduledTask -TaskName $names[0] -Action $morning -Settings $settings -Force `
  -Trigger (New-ScheduledTaskTrigger -Daily -At $MorningAt) `
  -Description "Stock CRM: backup, fetch TWSE/TPEx data, quality report, agent morning brief" | Out-Null
Register-ScheduledTask -TaskName $names[1] -Action $evening -Settings $settings -Force `
  -Trigger (New-ScheduledTaskTrigger -Daily -At $EveningAt) `
  -Description "Stock CRM: fetch TWSE/TPEx data after the close" | Out-Null

Get-ScheduledTask -TaskName $names | Select-Object TaskName, State | Format-Table -AutoSize
"Morning $MorningAt, evening $EveningAt. Logs and briefs: $root\data\reports"
