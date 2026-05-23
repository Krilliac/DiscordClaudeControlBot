<#
.SYNOPSIS
    Register the discord-claude-control watchdog as a Windows Task
    Scheduler entry that runs at the current user's logon.

.DESCRIPTION
    The watchdog is a second, independent process from the bot. It reads
    logs/heartbeat every minute and, when the heartbeat is stale, POSTs
    an alert to a Discord incoming webhook (separate credential from the
    bot's gateway token) and invokes task_restart.ps1.

    Why a separate Task Scheduler entry?
      - Independent failure domain: the bot can die without taking the
        watchdog with it.
      - Independent identity: alerts go to a Discord webhook URL, not via
        the bot's token, so a token reset (which has happened) does NOT
        silence the alerts.

    Requires:
      - ALERT_WEBHOOK_URL set in the user's environment or in .env (the
        watchdog process reads it at startup).
      - The bot is registered via task_install.ps1.

.PARAMETER TaskName
    Leaf name for the watchdog task. Defaults to 'watchdog'. Lives at
    '\discord-claude-control\$TaskName'.

.PARAMETER RepoPath
    Repo root. Defaults to four levels above this script.

.PARAMETER PythonExe
    Python interpreter. Defaults to '$RepoPath\.venv\Scripts\python.exe'.

.PARAMETER StaleSeconds
    Alert if heartbeat exceeds this age. Defaults to 180s.

.PARAMETER CheckInterval
    Seconds between heartbeat polls. Defaults to 60s.

.PARAMETER AlertCooldown
    Don't re-alert during a continuing outage for this long. Defaults to 600s.

.PARAMETER NoStart
    Register but do not start immediately.

.EXAMPLE
    .\task_install_watchdog.ps1
    Register the watchdog and start it now.
#>
[CmdletBinding()]
param(
    [string]$TaskName       = 'watchdog',
    [string]$RepoPath       = '',
    [string]$PythonExe      = '',
    [string]$LogDir         = '',
    [int]   $StaleSeconds   = 180,
    [int]   $CheckInterval  = 60,
    [int]   $AlertCooldown  = 600,
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'

if (-not $RepoPath) {
    $RepoPath = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSCommandPath)))
}
if (-not (Test-Path $RepoPath)) { throw "RepoPath not found: $RepoPath" }
$RepoPath = (Resolve-Path $RepoPath).Path

if (-not $PythonExe) {
    $PythonExe = Join-Path $RepoPath '.venv\Scripts\python.exe'
}
if (-not (Test-Path $PythonExe)) {
    throw "Python not found at $PythonExe. Create the venv first or pass -PythonExe."
}

if (-not $LogDir) {
    $LogDir = Join-Path $RepoPath 'logs'
}
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$taskFolder = '\discord-claude-control'
$taskPath   = "$taskFolder\$TaskName"
$userId     = "$env:USERDOMAIN\$env:USERNAME"

Write-Host "Registering watchdog scheduled task:"
Write-Host "  Path           : $taskPath"
Write-Host "  User           : $userId"
Write-Host "  Python         : $PythonExe"
Write-Host "  RepoPath       : $RepoPath"
Write-Host "  LogDir         : $LogDir"
Write-Host "  StaleSeconds   : $StaleSeconds"
Write-Host "  CheckInterval  : $CheckInterval"
Write-Host "  AlertCooldown  : $AlertCooldown"
Write-Host ""

# Warn (but don't refuse) if no webhook is configured. The watchdog itself
# exits with code 2 if ALERT_WEBHOOK_URL is unset, so the operator gets a
# clear signal in logs\watchdog-stderr.log on first run.
$envFile = Join-Path $RepoPath '.env'
$webhookConfigured = $false
if ($env:ALERT_WEBHOOK_URL) { $webhookConfigured = $true }
if (-not $webhookConfigured -and (Test-Path $envFile)) {
    $webhookConfigured = (Select-String -Path $envFile -Pattern '^ALERT_WEBHOOK_URL\s*=' -Quiet)
}
if (-not $webhookConfigured) {
    Write-Warning "ALERT_WEBHOOK_URL is not set in environment or .env."
    Write-Warning "The watchdog will exit immediately on start until you add it."
}

$stdoutLog = Join-Path $LogDir 'watchdog-stdout.log'
$stderrLog = Join-Path $LogDir 'watchdog-stderr.log'

# Build the args once so the cmd.exe quoting is readable.
$pyArgs = @(
    "-m discord_claude_control.watchdog"
    "--heartbeat-path `"$($LogDir)\heartbeat`""
    "--stale-seconds $StaleSeconds"
    "--check-interval $CheckInterval"
    "--alert-cooldown $AlertCooldown"
) -join ' '

$cmdArgs = "/d /c `"`"$PythonExe`" $pyArgs >> `"$stdoutLog`" 2>> `"$stderrLog`"`""
$action  = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\cmd.exe" -Argument $cmdArgs -WorkingDirectory $RepoPath
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

$existing = Get-ScheduledTask -TaskPath "$taskFolder\" -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Existing watchdog task found; removing first..."
    $orphans = @(
        Get-CimInstance -ClassName Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like '*discord_claude_control.watchdog*' }
    )
    foreach ($p in $orphans) {
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            Write-Host ("  killed pre-existing watchdog PID {0}" -f $p.ProcessId)
        } catch {}
    }
    if ($orphans.Count -gt 0) { Start-Sleep -Seconds 2 }
    Unregister-ScheduledTask -TaskPath "$taskFolder\" -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskPath $taskFolder `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'Watchdog for discord-claude-control. Reads logs/heartbeat and posts alerts to ALERT_WEBHOOK_URL when the bot wedges; runs task_restart.ps1 to recover.' | Out-Null

Write-Host "Watchdog task registered."

if (-not $NoStart) {
    Write-Host "Starting watchdog now..."
    Start-ScheduledTask -TaskPath "$taskFolder\" -TaskName $TaskName
    Start-Sleep -Seconds 2
}

Write-Host ""
Write-Host "=== Task state ==="
Get-ScheduledTask -TaskPath "$taskFolder\" -TaskName $TaskName |
    Get-ScheduledTaskInfo |
    Format-List TaskName, TaskPath, LastRunTime, LastTaskResult, NextRunTime, NumberOfMissedRuns

Write-Host ""
Write-Host "Done. The watchdog starts at every user logon."
Write-Host "Logs: $stdoutLog and $stderrLog"
