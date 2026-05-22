<#
.SYNOPSIS
    Report the current state of the discord-claude-control scheduled task,
    its liveness heartbeat, any active power requests, and matching python
    processes, so you can verify the bot is running and awake-holding
    correctly.

.PARAMETER TaskName
    Leaf task name. Defaults to 'bot'. Lives under '\discord-claude-control\'.

.PARAMETER StaleHeartbeatSeconds
    Threshold above which the heartbeat file is considered stale (the
    event loop is likely wedged). Defaults to 120 seconds; the bot writes
    every 30s so anything > 2 minutes is suspicious.
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'bot',
    [int]$StaleHeartbeatSeconds = 120
)

$taskFolder = '\discord-claude-control'

Write-Host "--- Scheduled task ---"
$task = Get-ScheduledTask -TaskPath "$taskFolder\" -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    $info = $task | Get-ScheduledTaskInfo
    [PSCustomObject]@{
        TaskName       = $task.TaskName
        TaskPath       = $task.TaskPath
        State          = $task.State
        LastRunTime    = $info.LastRunTime
        LastTaskResult = '0x{0:X} ({0})' -f $info.LastTaskResult
        NextRunTime    = $info.NextRunTime
        UserId         = $task.Principal.UserId
        LogonType      = $task.Principal.LogonType
    } | Format-List
} else {
    Write-Host "Task '$taskFolder\$TaskName' is not registered."
    Write-Host "Install with: src\discord_claude_control\service\task_install.ps1"
}

$repo = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSCommandPath)))

Write-Host ""
Write-Host "--- Liveness heartbeat ---"
$heartbeat = Join-Path $repo "logs\heartbeat"
if (Test-Path $heartbeat) {
    $lastWrite = (Get-Item $heartbeat).LastWriteTime
    $age = (Get-Date) - $lastWrite
    $ageSecs = [int]$age.TotalSeconds
    if ($ageSecs -lt $StaleHeartbeatSeconds) {
        Write-Host ("  fresh (age={0}s, written {1})" -f $ageSecs, $lastWrite)
    } else {
        Write-Warning ("  STALE (age={0}s, threshold={1}s) -- event loop may be wedged" -f $ageSecs, $StaleHeartbeatSeconds)
    }
} else {
    Write-Host "  no heartbeat file yet at logs\heartbeat"
    Write-Host "  (normal if the bot just started, or if it predates the heartbeat feature)"
}

Write-Host ""
Write-Host "--- Crash marker ---"
$crashMarker = Join-Path $repo "logs\crash-marker.json"
$crashMarkerLast = Join-Path $repo "logs\crash-marker.json.last"
if (Test-Path $crashMarker) {
    Write-Warning "  unhandled crash detected -- inspect logs\crash-marker.json"
    Get-Content $crashMarker
} elseif (Test-Path $crashMarkerLast) {
    Write-Host ("  no live crash; last recorded crash in logs\crash-marker.json.last ({0})" -f (Get-Item $crashMarkerLast).LastWriteTime)
} else {
    Write-Host "  none"
}

Write-Host ""
Write-Host "--- Running python processes for this repo ---"
Get-CimInstance -ClassName Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*discord_claude_control*" -or $_.ExecutablePath -like "$repo*" } |
    Select-Object ProcessId, SessionId, ExecutablePath, CommandLine |
    Format-List

Write-Host ""
Write-Host "--- powercfg /requests (requires elevation) ---"
try {
    powercfg /requests 2>&1
} catch {
    Write-Host "(powercfg failed; run this script from an elevated PowerShell to see power requests)"
}
