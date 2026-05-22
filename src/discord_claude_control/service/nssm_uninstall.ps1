# Removes the discord-claude-control NSSM service. Run from elevated PowerShell.

[CmdletBinding()]
param(
    [string]$ServiceName = "discord-claude-control",
    [string]$NssmPath    = "C:\Tools\nssm.exe"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $NssmPath)) {
    Write-Error "nssm.exe not found at $NssmPath. Pass -NssmPath."
    exit 1
}

$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "Service '$ServiceName' is not installed; nothing to do."
    exit 0
}

Write-Host "Stopping and removing service '$ServiceName'"
& $NssmPath stop   $ServiceName confirm | Out-Null
& $NssmPath remove $ServiceName confirm | Out-Null
Write-Host "Done."
