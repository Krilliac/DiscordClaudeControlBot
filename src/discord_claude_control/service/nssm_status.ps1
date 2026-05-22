# Reports the current state of the discord-claude-control service plus any
# active power requests so you can verify the bot is awake-holding correctly.

[CmdletBinding()]
param(
    [string]$ServiceName = "discord-claude-control",
    [string]$NssmPath    = "C:\Tools\nssm.exe"
)

if (Test-Path $NssmPath) {
    Write-Host "--- NSSM status ---"
    & $NssmPath status $ServiceName
}

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc) {
    Write-Host ""
    Write-Host "--- Service ---"
    $svc | Format-List Name, Status, StartType, ServiceType
} else {
    Write-Host "Service '$ServiceName' is not installed."
}

Write-Host ""
Write-Host "--- powercfg /requests ---"
powercfg /requests
