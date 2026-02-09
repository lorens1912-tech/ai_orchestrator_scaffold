param(
  [Parameter(Mandatory=$true)]
  [string]$SnapshotPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Set-Location C:\AI\ai_orchestrator_scaffold

powershell -ExecutionPolicy Bypass -File .\scripts\require_audyt_snapshot.ps1 -SnapshotPath $SnapshotPath

$guardOut = & powershell -ExecutionPolicy Bypass -File .\scripts\p14_continuous_release_guard.ps1 2>&1 | Out-String
$guardOut | Write-Host

if ($guardOut -notmatch "(?m)\bPASS\b") {
  throw "P14_GUARD_FAIL_AFTER_SNAPSHOT_CHECK"
}

Write-Host "P14_PLUS_SNAPSHOT_PASS: True"
