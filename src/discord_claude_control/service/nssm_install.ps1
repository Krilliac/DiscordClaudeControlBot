<#
.SYNOPSIS
    Register discord-claude-control as a Windows service via NSSM.

.DESCRIPTION
    ============================================================
     WARNING: Session 0 isolation breaks desktop tools.
    ============================================================
    Windows services always run in Session 0, a non-interactive
    desktop with no visible surface. The screenshot tool's BitBlt
    call (and any GDI/USER call) fails there, even when the
    service runs under your user account. The mouse, keyboard,
    and screenshot tools will NOT work from this service.

    For the full tool set (screenshot, click, type_text, etc.)
    use task_install.ps1 instead -- Task Scheduler can launch
    the bot inside your interactive session.

    Use this NSSM script ONLY when you intend to disable the
    desktop-interactive tools in config.toml (leaving things
    like run_powershell, read_file, write_file, list_dir,
    kill_process), AND you need true reboot-without-login
    survival (which Task Scheduler at-logon does not provide
    unless Windows auto-logon is configured).

    What this script does:
      1. Self-elevates via UAC if not already elevated.
      2. Auto-detects nssm.exe (winget install path / C:\Tools /
         PATH) or accepts -NssmPath.
      3. Stops + removes any prior install so re-runs are safe.
      4. Installs the service to run the repo's venv python.
      5. Prompts for your Windows password to configure log-on
         as your user account (Read-Host -AsSecureString; never
         echoed; passed only to nssm.exe).
      6. Starts the service and reports status.

.PARAMETER ServiceName
.PARAMETER NssmPath
.PARAMETER RepoPath
.PARAMETER PythonExe
.PARAMETER LogDir
.PARAMETER ServiceAccount
.PARAMETER NoServiceStart

.EXAMPLE
    .\nssm_install.ps1
    Run with all defaults; self-elevates and prompts for password.

.EXAMPLE
    .\nssm_install.ps1 -NssmPath C:\Tools\nssm.exe -NoServiceStart
    Use a specific NSSM, install but don't start the service.
#>
[CmdletBinding()]
param(
    [string]$ServiceName    = 'discord-claude-control',
    [string]$NssmPath       = '',
    [string]$RepoPath       = '',
    [string]$PythonExe      = '',
    [string]$LogDir         = '',
    [string]$ServiceAccount = '',
    [switch]$NoServiceStart
)

$ErrorActionPreference = 'Stop'

# Self-elevate if not already.
$identity = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Elevation required. A UAC prompt will appear."
    $argList = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$PSCommandPath)
    foreach ($k in $PSBoundParameters.Keys) {
        $v = $PSBoundParameters[$k]
        if ($v -is [switch]) {
            if ($v.IsPresent) { $argList += "-$k" }
        } else {
            $argList += @("-$k", "$v")
        }
    }
    Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList $argList
    exit
}

function Find-Nssm {
    if ($script:NssmPath -and (Test-Path $script:NssmPath)) { return $script:NssmPath }
    $candidates = @(
        'C:\Tools\nssm.exe',
        'C:\Program Files\nssm\nssm.exe',
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links\nssm.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    $wingetSearch = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path $wingetSearch) {
        $found = Get-ChildItem $wingetSearch -Recurse -Filter 'nssm.exe' -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -like '*win64*' } |
            Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    $cmd = Get-Command nssm.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

# Defaults.
if (-not $RepoPath) {
    $RepoPath = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSCommandPath)))
}
$RepoPath = (Resolve-Path $RepoPath).Path

if (-not $PythonExe) {
    $venvPython = Join-Path $RepoPath '.venv\Scripts\python.exe'
    if (Test-Path $venvPython) { $PythonExe = $venvPython }
    else {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $cmd) { throw "No python found. Pass -PythonExe explicitly." }
        $PythonExe = $cmd.Source
    }
}
if (-not (Test-Path $PythonExe)) { throw "Python not found at $PythonExe" }

if (-not $LogDir) { $LogDir = Join-Path $RepoPath 'logs' }
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

if (-not $NssmPath) {
    $NssmPath = Find-Nssm
    if (-not $NssmPath) {
        throw "nssm.exe not found. Install with: winget install --id NSSM.NSSM --source winget`nOr download from https://nssm.cc/ and pass -NssmPath."
    }
}

if (-not $ServiceAccount) { $ServiceAccount = ".\$env:USERNAME" }

Write-Host "Installing service '$ServiceName'"
Write-Host "  NSSM   : $NssmPath"
Write-Host "  Python : $PythonExe"
Write-Host "  Repo   : $RepoPath"
Write-Host "  LogDir : $LogDir"
Write-Host "  Account: $ServiceAccount"
Write-Host ""

# Idempotent re-install.
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Existing service found; removing first."
    & $NssmPath stop   $ServiceName confirm 2>&1 | Out-Null
    & $NssmPath remove $ServiceName confirm 2>&1 | Out-Null
}

& $NssmPath install $ServiceName $PythonExe '-m' 'discord_claude_control'
& $NssmPath set $ServiceName AppDirectory $RepoPath
& $NssmPath set $ServiceName AppStdout (Join-Path $LogDir 'stdout.log')
& $NssmPath set $ServiceName AppStderr (Join-Path $LogDir 'stderr.log')
& $NssmPath set $ServiceName AppStdoutCreationDisposition 4
& $NssmPath set $ServiceName AppStderrCreationDisposition 4
& $NssmPath set $ServiceName AppRotateFiles 1
& $NssmPath set $ServiceName AppRotateBytes 10485760
& $NssmPath set $ServiceName Start SERVICE_AUTO_START
& $NssmPath set $ServiceName Description 'Discord-controlled personal PC agent (Claude Agent SDK). HEADLESS mode -- desktop tools (screenshot, mouse, keyboard) WILL NOT work; use task_install.ps1 for those.'

# Log-on configuration. Without this nssm sets ObjectName=LocalSystem, which
# (a) still fails BitBlt because of Session 0 isolation, but (b) also blocks
# access to user-scoped resources (HKCU, %USERPROFILE%). Run as the user.
if ($ServiceAccount -notmatch '^(LOCALSYSTEM|LOCALSERVICE|NETWORKSERVICE)$') {
    Write-Host ""
    Write-Host "===================================================================="
    Write-Host "  Enter the Windows password for account $ServiceAccount"
    Write-Host "  (the password you use to sign into Windows on this PC)"
    Write-Host "  Sent only to nssm.exe locally; not transmitted, not logged."
    Write-Host "===================================================================="
    $sec  = Read-Host -AsSecureString -Prompt "Password for $ServiceAccount"
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try {
        $plain = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        & $NssmPath set $ServiceName ObjectName $ServiceAccount $plain
    } finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        $plain = $null
        [System.GC]::Collect()
    }
}

if (-not $NoServiceStart) {
    Write-Host ""
    Write-Host "Starting service..."
    & $NssmPath start $ServiceName
}

Write-Host ""
Write-Host "=== Status ==="
& $NssmPath status $ServiceName
Get-Service -Name $ServiceName | Format-Table Name, Status, StartType -AutoSize

Write-Host ""
Write-Host "Logs:"
Write-Host "  stdout -> $(Join-Path $LogDir 'stdout.log')"
Write-Host "  stderr -> $(Join-Path $LogDir 'stderr.log')"
Write-Host ""
Write-Host "REMINDER: in Session 0 the screenshot/mouse/keyboard tools fail."
Write-Host "Disable them in config.toml's [tools] enabled list, or switch to"
Write-Host "task_install.ps1 to run the bot inside your interactive session."
Write-Host ""
Write-Host "Press Enter to close this window..."
[void](Read-Host)
