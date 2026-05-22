<#
.SYNOPSIS
    Report the current state of the discord-claude-control scheduled task
    plus any active power requests, so you can verify the bot is running
    and awake-holding correctly.

.PARAMETER TaskName
    Leaf task name. Defaults to 'bot'. Lives under '\discord-claude-control\'.
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'bot'
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

Write-Host ""
Write-Host "--- Running python processes for this repo ---"
$repo = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSCommandPath)))
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
