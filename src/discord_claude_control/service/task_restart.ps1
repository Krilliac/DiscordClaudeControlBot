<#
.SYNOPSIS
    Restart the discord-claude-control scheduled task safely, killing
    any orphan python.exe processes first.

.DESCRIPTION
    Stop-ScheduledTask only signals the task's immediate action. If the
    task's child python.exe has been orphaned from its cmd.exe wrapper
    (e.g. wrapper exited early, or python re-parented to services.exe),
    Stop-ScheduledTask does not kill it. Start-ScheduledTask then
    launches a SECOND python, which loses the Discord gateway-slot
    race against the orphan and exits with code 1. The user sees the
    bot stay "online" on the OLD code while the new launch is silently
    discarded.

    This helper is the supported way to restart the bot after a
    self-update or config change:

      1. Find every python.exe whose CommandLine references
         discord_claude_control and Stop-Process it. This covers both
         the venv launcher and the actual interpreter, regardless of
         parent process.
      2. Wait briefly for Discord to register the disconnection and
         release the gateway slot.
      3. Stop-ScheduledTask (covers the rare case where cmd.exe is
         still alive without a python child).
      4. Start-ScheduledTask.
      5. Poll logs/heartbeat for up to -WaitSeconds to confirm the
         new bot reached run_heartbeat_loop.

    Returns exit code 0 on success (fresh heartbeat observed), 1 if
    the new bot never wrote a heartbeat within the wait window.

.PARAMETER TaskName
.PARAMETER WaitSeconds
    How long to wait for the new bot's heartbeat. Defaults to 60s
    (heartbeat cadence is 30s; allow time for python startup and
    Discord connect).

.EXAMPLE
    .\task_restart.ps1
    Kill old bot processes, restart task, wait for heartbeat.
#>
[CmdletBinding()]
param(
    [string]$TaskName    = 'bot',
    [int]$WaitSeconds    = 60
)

$ErrorActionPreference = 'Continue'

$taskFolder = '\discord-claude-control'
$repo = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSCommandPath)))
$heartbeat = Join-Path $repo 'logs\heartbeat'

function Get-BotPythonProcs {
    Get-CimInstance -ClassName Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*discord_claude_control*' }
}

Write-Host "--- Killing orphan bot processes ---"
$procs = @(Get-BotPythonProcs)
if ($procs.Count -eq 0) {
    Write-Host "  none found"
} else {
    foreach ($p in $procs) {
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            Write-Host ("  killed PID {0} (session={1})" -f $p.ProcessId, $p.SessionId)
        } catch {
            Write-Warning ("  failed to kill PID {0}: {1}" -f $p.ProcessId, $_)
        }
    }
    # Give Discord time to register the disconnection so the new bot
    # does not lose the gateway-slot race.
    Start-Sleep -Seconds 3
}

Write-Host ""
Write-Host "--- Stopping task (covers the case where cmd.exe is still alive) ---"
try {
    Stop-ScheduledTask -TaskPath "$taskFolder\" -TaskName $TaskName -ErrorAction Stop
    Write-Host "  Stop-ScheduledTask issued"
} catch {
    Write-Host ("  Stop-ScheduledTask: {0}" -f $_.Exception.Message)
}

# Confirm we're really not running anymore before starting fresh.
for ($i = 0; $i -lt 10; $i++) {
    Start-Sleep -Milliseconds 500
    $t = Get-ScheduledTask -TaskPath "$taskFolder\" -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($t -and $t.State -ne 'Running') { break }
}

# Pre-emptively delete the old heartbeat file so we can tell when the
# NEW bot writes a fresh one (otherwise an old file would look "fresh"
# from the wrapper's perspective until 30s of staleness accumulated).
if (Test-Path $heartbeat) {
    Remove-Item $heartbeat -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "--- Starting task ---"
try {
    Start-ScheduledTask -TaskPath "$taskFolder\" -TaskName $TaskName -ErrorAction Stop
    Write-Host "  Start-ScheduledTask issued"
} catch {
    Write-Error "  Start-ScheduledTask failed: $_"
    exit 1
}

Write-Host ""
Write-Host ("--- Waiting up to {0}s for heartbeat ---" -f $WaitSeconds)
$deadline = (Get-Date).AddSeconds($WaitSeconds)
$ok = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    if (Test-Path $heartbeat) {
        $age = ((Get-Date) - (Get-Item $heartbeat).LastWriteTime).TotalSeconds
        if ($age -lt 60) {
            Write-Host ("  fresh heartbeat (age={0:N0}s)" -f $age)
            $ok = $true
            break
        }
    }
}

if (-not $ok) {
    Write-Warning "No fresh heartbeat after $WaitSeconds s -- check logs\stderr.log for startup errors:"
    $stderr = Join-Path $repo 'logs\stderr.log'
    if (Test-Path $stderr) {
        Get-Content $stderr -Tail 15 | ForEach-Object { Write-Host "    $_" }
    }
    exit 1
}

Write-Host ""
Write-Host "--- Final state ---"
$task = Get-ScheduledTask -TaskPath "$taskFolder\" -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    $info = $task | Get-ScheduledTaskInfo
    [PSCustomObject]@{
        State          = $task.State
        LastRunTime    = $info.LastRunTime
        LastTaskResult = '0x{0:X}' -f $info.LastTaskResult
    } | Format-List
}

$bot = @(Get-BotPythonProcs)
if ($bot.Count -gt 0) {
    Write-Host "Bot processes:"
    $bot | Select-Object ProcessId, SessionId, ExecutablePath | Format-Table -AutoSize
}

Write-Host "Done."
exit 0
