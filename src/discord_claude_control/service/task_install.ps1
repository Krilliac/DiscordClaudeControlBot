<#
.SYNOPSIS
    Register discord-claude-control as a Windows Task Scheduler entry that
    runs at the current user's logon.

.DESCRIPTION
    The bot uses screenshot, mouse, keyboard, and process tools that
    require access to the visible interactive desktop. Windows services
    always run in Session 0, which has no desktop -- BitBlt and the
    other GDI/USER calls fail there even when the service runs under
    the user's account.

    Task Scheduler, with a "Run only when user is logged on" task
    triggered "At log on", runs the process inside the user's
    interactive session (Session 1+) where the desktop is real. That
    is the supported Windows pattern for unattended desktop-aware
    automation.

    What this script does:
      1. Builds a ScheduledTaskAction that runs the repo's venv python
         against the discord_claude_control package, with WorkingDirectory
         set to the repo root.
      2. Builds a ScheduledTaskTrigger fired at the current user's logon.
      3. Registers (or replaces) the task under the path
         '\discord-claude-control\bot'.
      4. Starts it immediately so you don't have to log out and back in.

    No password is required and elevation is NOT required to register
    a task that runs as the current user. (If a prior NSSM-based service
    exists, see nssm_uninstall.ps1 to remove it first; that does need
    elevation.)

.PARAMETER TaskName
    The leaf task name. Defaults to 'bot'. The task lives under
    '\discord-claude-control\$TaskName'.

.PARAMETER RepoPath
    Path to the repo root. Defaults to the script's grandparent's
    grandparent (i.e. assumes this file lives at
    src/discord_claude_control/service/task_install.ps1).

.PARAMETER PythonExe
    Python interpreter. Defaults to '$RepoPath\.venv\Scripts\python.exe'.

.PARAMETER NoStart
    If set, register the task but do not start it immediately. It will
    still start at next logon.

.EXAMPLE
    .\task_install.ps1
    Register with defaults and start the bot now.

.EXAMPLE
    .\task_install.ps1 -NoStart
    Register but defer starting until next logon.
#>
[CmdletBinding()]
param(
    [string]$TaskName  = 'bot',
    [string]$RepoPath  = '',
    [string]$PythonExe = '',
    [string]$LogDir    = '',
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'

# Resolve paths.
if (-not $RepoPath) {
    $RepoPath = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSCommandPath)))
}
if (-not (Test-Path $RepoPath)) { throw "RepoPath not found: $RepoPath" }
$RepoPath = (Resolve-Path $RepoPath).Path

if (-not $PythonExe) {
    $PythonExe = Join-Path $RepoPath '.venv\Scripts\python.exe'
}
if (-not (Test-Path $PythonExe)) {
    throw "Python not found at $PythonExe. Create the venv first (python -m venv .venv) or pass -PythonExe."
}

if (-not $LogDir) {
    $LogDir = Join-Path $RepoPath 'logs'
}
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$taskFolder = '\discord-claude-control'
$taskPath   = "$taskFolder\$TaskName"
$userId     = "$env:USERDOMAIN\$env:USERNAME"

Write-Host "Registering scheduled task:"
Write-Host "  Path     : $taskPath"
Write-Host "  User     : $userId"
Write-Host "  Python   : $PythonExe"
Write-Host "  RepoPath : $RepoPath"
Write-Host "  LogDir   : $LogDir"
Write-Host ""

# Wrap the python invocation in cmd.exe so we can redirect stdout/stderr to
# log files. ScheduledTaskAction by itself does not support I/O redirection.
$stdoutLog = Join-Path $LogDir 'stdout.log'
$stderrLog = Join-Path $LogDir 'stderr.log'
$cmdArgs   = "/d /c `"`"$PythonExe`" -m discord_claude_control >> `"$stdoutLog`" 2>> `"$stderrLog`"`""
$action    = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\cmd.exe" -Argument $cmdArgs -WorkingDirectory $RepoPath
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User $userId
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

# Remove any prior registration so re-running this script is safe.
$existing = Get-ScheduledTask -TaskPath "$taskFolder\" -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Existing task found; removing first..."
    # Kill orphan bot processes before unregister -- Stop-ScheduledTask
    # does not reach python.exe that have detached from their cmd.exe
    # wrapper. If they survive, the new bot we are about to start
    # collides on the Discord gateway slot.
    $orphans = @(
        Get-CimInstance -ClassName Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like '*discord_claude_control*' }
    )
    foreach ($p in $orphans) {
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            Write-Host ("  killed pre-existing PID {0}" -f $p.ProcessId)
        } catch {}
    }
    if ($orphans.Count -gt 0) { Start-Sleep -Seconds 3 }
    Unregister-ScheduledTask -TaskPath "$taskFolder\" -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskPath $taskFolder `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'Discord-controlled personal PC agent (Claude Agent SDK). Runs in the user interactive session so screenshot/input tools work.' | Out-Null

Write-Host "Task registered."

if (-not $NoStart) {
    Write-Host "Starting task now..."
    Start-ScheduledTask -TaskPath "$taskFolder\" -TaskName $TaskName
    Start-Sleep -Seconds 3
}

Write-Host ""
Write-Host "=== Task state ==="
Get-ScheduledTask -TaskPath "$taskFolder\" -TaskName $TaskName |
    Get-ScheduledTaskInfo |
    Format-List TaskName, TaskPath, LastRunTime, LastTaskResult, NextRunTime, NumberOfMissedRuns

Write-Host ""
Write-Host "Done. The bot now starts at every user logon."
Write-Host "Manage with: Get-ScheduledTask, Start-ScheduledTask, Stop-ScheduledTask,"
Write-Host "  Unregister-ScheduledTask (or run task_uninstall.ps1)."
