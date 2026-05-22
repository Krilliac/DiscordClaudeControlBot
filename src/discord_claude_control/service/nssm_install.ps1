# Installs discord-claude-control as a Windows service via NSSM.
# Run from an elevated PowerShell.
#
# Usage:
#   .\nssm_install.ps1
#   .\nssm_install.ps1 -NssmPath C:\Tools\nssm.exe -PythonExe C:\repo\.venv\Scripts\python.exe

[CmdletBinding()]
param(
    [string]$ServiceName = "discord-claude-control",
    [string]$NssmPath    = "C:\Tools\nssm.exe",
    [string]$RepoPath    = (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSCommandPath)))),
    [string]$PythonExe   = "",
    [string]$LogDir      = ""
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $NssmPath)) {
    Write-Error "nssm.exe not found at $NssmPath. Download from https://nssm.cc/ and pass -NssmPath."
    exit 1
}

if (-not $PythonExe) {
    $venvPython = Join-Path $RepoPath ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        $PythonExe = $venvPython
    } else {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $cmd) {
            Write-Error "No python found. Pass -PythonExe explicitly."
            exit 1
        }
        $PythonExe = $cmd.Source
    }
}

if (-not $LogDir) {
    $LogDir = Join-Path $RepoPath "logs"
}
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

Write-Host "Installing service '$ServiceName'"
Write-Host "  Python : $PythonExe"
Write-Host "  Repo   : $RepoPath"
Write-Host "  Logs   : $LogDir"
Write-Host ""

# Remove any prior install so re-running this script is safe.
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Existing service found; removing first."
    & $NssmPath stop   $ServiceName confirm | Out-Null
    & $NssmPath remove $ServiceName confirm | Out-Null
}

& $NssmPath install $ServiceName $PythonExe "-m" "discord_claude_control"
& $NssmPath set $ServiceName AppDirectory $RepoPath
& $NssmPath set $ServiceName AppStdout (Join-Path $LogDir "stdout.log")
& $NssmPath set $ServiceName AppStderr (Join-Path $LogDir "stderr.log")
& $NssmPath set $ServiceName AppStdoutCreationDisposition 4
& $NssmPath set $ServiceName AppStderrCreationDisposition 4
& $NssmPath set $ServiceName AppRotateFiles 1
& $NssmPath set $ServiceName AppRotateBytes 10485760
& $NssmPath set $ServiceName Start SERVICE_AUTO_START
& $NssmPath set $ServiceName Description "Discord-controlled personal PC agent (Claude Agent SDK)"

Write-Host ""
Write-Host "Service registered."
Write-Host ""
Write-Host "Next steps (REQUIRED for input/screenshot tools):"
Write-Host "  1. Run:    & '$NssmPath' edit $ServiceName"
Write-Host "  2. On the 'Log on' tab pick 'This account' and enter:"
Write-Host "         Account:  .\$($env:USERNAME)"
Write-Host "         Password: <your Windows password>"
Write-Host "     This makes the service run inside your logon session so it can"
Write-Host "     interact with the visible desktop. LocalSystem cannot."
Write-Host "  3. Start:  & '$NssmPath' start $ServiceName"
Write-Host "  4. Status: & '$NssmPath' status $ServiceName"
Write-Host ""
Write-Host "Logs:"
Write-Host "  stdout -> $(Join-Path $LogDir 'stdout.log')"
Write-Host "  stderr -> $(Join-Path $LogDir 'stderr.log')"
