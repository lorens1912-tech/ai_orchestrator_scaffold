param(
  [Parameter(Mandatory=$true)]
  [string]$SnapshotPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Set-Location C:\AI\ai_orchestrator_scaffold

if ([string]::IsNullOrWhiteSpace($SnapshotPath)) {
  throw "AUDYT_SNAPSHOT_REQUIRED: SnapshotPath is empty."
}
if (-not (Test-Path $SnapshotPath)) {
  throw "AUDYT_SNAPSHOT_REQUIRED: File not found -> $SnapshotPath"
}

$raw = Get-Content $SnapshotPath -Raw
if ($raw -notmatch "PROJECT:\s*AgentAI PRO") {
  throw "AUDYT_SNAPSHOT_INVALID: missing PROJECT: AgentAI PRO"
}
if ($raw -notmatch "STATUS:\s*(VERIFIED|BLOCKED)") {
  throw "AUDYT_SNAPSHOT_INVALID: missing STATUS"
}

Write-Host "AUDYT_SNAPSHOT_OK: $SnapshotPath"
