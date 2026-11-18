<#
.SYNOPSIS
    Register (or remove) the daily LLA database backup scheduled task.

.DESCRIPTION
    The task is intentionally dumb: it only calls scripts/backup_database.py,
    which refuses to write inside the repository, verifies every copy with
    PRAGMA integrity_check and keeps a sha256 index.

    Run once from an elevated shell:
        pwsh -File scripts/register_backup_task.ps1
        pwsh -File scripts/register_backup_task.ps1 -RunNow
        pwsh -File scripts/register_backup_task.ps1 -Unregister

    Note: the default task runs under the current user, so it fires while that
    user is logged on. Use the Windows Task Scheduler GUI if it must also run
    while logged off.
#>
[CmdletBinding()]
param(
    [string]$Time = "12:00",
    [string]$TaskName = "LLA Database Backup",
    [switch]$RunNow,
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"

if ($Unregister) {
    schtasks /Delete /TN "$TaskName" /F
    exit 0
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repo ".venv\Scripts\python.exe"
$script = Join-Path $repo "scripts\backup_database.py"

foreach ($required in @($python, $script)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Missing required file: $required" }
}

$action = '"' + $python + '" "' + $script + '"'
schtasks /Create /SC DAILY /ST $Time /TN "$TaskName" /TR $action /F /RL HIGHEST

if ($RunNow) {
    schtasks /Run /TN "$TaskName"
    Start-Sleep -Seconds 5
}

schtasks /Query /TN "$TaskName" /V /FO LIST |
    Select-String -Pattern 'TaskName|Status|Next Run Time|Task To Run|Scheduled Task State|Run As User'