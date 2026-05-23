<#
.SYNOPSIS
    Stop and unregister the discord-claude-control scheduled task.

.DESCRIPTION
    Does NOT require elevation: tasks registered under the current user
    can be removed by the current user.

.PARAMETER TaskName
    Leaf task name. Defaults to 'bot'. Lives under '\discord-claude-control\'.
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'bot'
)

$ErrorActionPreference = 'Stop'

$taskFolder = '\discord-claude-control'

$task = Get-ScheduledTask -TaskPath "$taskFolder\" -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "Task '$taskFolder\$TaskName' is not registered; nothing to do."
    exit 0
}

Write-Host "Stopping task '$taskFolder\$TaskName' (if running)..."
try { Stop-ScheduledTask -TaskPath "$taskFolder\" -TaskName $TaskName -ErrorAction SilentlyContinue } catch {}

# Stop-ScheduledTask does not kill child python.exe that have orphaned
# from their cmd.exe wrapper. Sweep them explicitly so uninstall really
# leaves no running bot behind.
$orphans = @(
    Get-CimInstance -ClassName Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*discord_claude_control*' }
)
foreach ($p in $orphans) {
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
        Write-Host ("  killed orphan PID {0}" -f $p.ProcessId)
    } catch {}
}

Write-Host "Unregistering task..."
Unregister-ScheduledTask -TaskPath "$taskFolder\" -TaskName $TaskName -Confirm:$false

# Try to remove the empty folder. The Task Scheduler API doesn't expose a
# clean cmdlet for this; use the COM scheduler object.
try {
    $sched = New-Object -ComObject 'Schedule.Service'
    $sched.Connect()
    $root = $sched.GetFolder('\')
    $folderName = $taskFolder.TrimStart('\')
    $folder = $root.GetFolder($folderName)
    $remaining = $folder.GetTasks(0)
    if ($remaining.Count -eq 0) {
        $root.DeleteFolder($folderName, 0)
        Write-Host "Removed empty folder '$taskFolder'."
    }
} catch {
    # Folder may not exist or may still contain other tasks. Either is fine.
}

Write-Host "Done."
