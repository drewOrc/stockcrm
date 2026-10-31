# Daily maintenance + agent morning brief. Scheduled by install_schedule.ps1; safe to run by hand.
#   powershell -ExecutionPolicy Bypass -File scripts\morning.ps1 [-SkipAgent]
param([switch]$SkipAgent)

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$PSDefaultParameterValues["Tee-Object:Encoding"] = "UTF8"
$PSDefaultParameterValues["Out-File:Encoding"] = "UTF8"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"
$day = Get-Date -Format "yyyy-MM-dd"
$reports = Join-Path $root "data\reports"
New-Item -ItemType Directory -Force $reports | Out-Null
$log = Join-Path $reports "morning-$day.log"

"=== $(Get-Date -Format s) maintain all" | Tee-Object -FilePath $log -Append
python -X utf8 scripts\maintain.py all 2>&1 | Tee-Object -FilePath $log -Append
$maintExit = $LASTEXITCODE

if ($SkipAgent) { "agent skipped" | Tee-Object -FilePath $log -Append; exit $maintExit }

# Claude Code CLI: STOCKCRM_CLAUDE env var, else PATH, else the copy bundled with the Claude desktop app (newest version).
$claude = $env:STOCKCRM_CLAUDE
if (-not $claude) { $cmd = Get-Command claude -ErrorAction SilentlyContinue; if ($cmd) { $claude = $cmd.Source } }
if (-not $claude) {
  $bundled = Get-ChildItem "$env:APPDATA\Claude\claude-code\*\*\claude.exe" -ErrorAction SilentlyContinue |
    Sort-Object { [version](($_.Directory.Parent.Name -replace '[^\d\.]', '')) } -Descending | Select-Object -First 1
  if ($bundled) { $claude = $bundled.FullName }
}
if (-not $claude) {
  "claude CLI not found; set STOCKCRM_CLAUDE or install Claude Code. Maintenance ran, agent brief skipped." | Tee-Object -FilePath $log -Append
  python -X utf8 -c "import sys; sys.path.insert(0, '.'); from stockcrm import db, core; core.log_maintenance(db.connect(), 'morning', 'skipped', 'claude CLI not found')"
  exit $maintExit
}

"=== $(Get-Date -Format s) agent morning brief via $claude" | Tee-Object -FilePath $log -Append
$prompt = Get-Content (Join-Path $root "scripts\morning_prompt.md") -Raw -Encoding UTF8
$brief = Join-Path $reports "morning-$day.md"
# Only the stockcrm MCP tools and web lookup are allowed; no file edits, no shell.
& $claude -p $prompt --output-format text --allowedTools "mcp__stockcrm__*,WebSearch,WebFetch" 2>>$log | Out-File -FilePath $brief -Encoding UTF8
$agentExit = $LASTEXITCODE
$briefText = if (Test-Path $brief) { Get-Content $brief -Raw -Encoding UTF8 } else { "" }
$status = "ok"; $note = "brief written to data/reports/morning-$day.md (exit $agentExit)"
if ($briefText -match "Not logged in") {
  $status = "error"; $note = "claude CLI is not logged in: open a terminal, run claude, then /login once (or set ANTHROPIC_API_KEY)"
} elseif ($agentExit -ne 0 -or $briefText.Trim().Length -lt 20) {
  $status = "error"; $note = "agent exit $agentExit, brief empty; see data/reports/morning-$day.log"
}
python -X utf8 -c "import sys; sys.path.insert(0, '.'); from stockcrm import db, core; core.log_maintenance(db.connect(), 'morning', '$status', '''$note''')"
"=== done, agent exit $agentExit, brief: $brief" | Tee-Object -FilePath $log -Append
exit ([int]($maintExit -ne 0 -or $agentExit -ne 0))
