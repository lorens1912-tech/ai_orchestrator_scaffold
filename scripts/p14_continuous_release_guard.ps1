Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location C:\AI\ai_orchestrator_scaffold
Write-Host "P14: Continuous Release Guard"

function Get-NormalizedPathFromPorcelainLine {
  param([Parameter(Mandatory=$true)][string]$Line)

  $raw = if ($Line.Length -ge 4) { $Line.Substring(3).Trim() } else { $Line.Trim() }

  # rename pattern: old -> new
  if ($raw -match " -> ") {
    $raw = ($raw -split " -> ")[-1].Trim()
  }

  return ($raw -replace "\\","/")
}

# 1) Dirty tree check (z wykluczeniami lokalnych artefaktów)
$rawStatus = @(git status --porcelain)
$dirty = New-Object System.Collections.Generic.List[string]

foreach ($ln in $rawStatus) {
  if ([string]::IsNullOrWhiteSpace($ln)) { continue }

  $p = Get-NormalizedPathFromPorcelainLine -Line $ln

  # wykluczenia lokalne
  if ($p -like "reports/*") { continue }
  if ($p -like "runs/_tmp/*") { continue }
  if ($p -eq "runs/_tmp") { continue }

  [void]$dirty.Add($ln)
}

if ($dirty.Count -gt 0) {
  Write-Host "BLOCKED_DIRTY_TREE:"
  $dirty | ForEach-Object { Write-Host $_ }
  throw "Working tree must be clean before release/push (excluding local reports/handoff)."
}

# 2) Strict fastpath tests 002..007
$tests = @(
  "tests/test_002_config_contract.py",
  "tests/test_003_write_step.py",
  "tests/test_004_critic_step.py",
  "tests/test_005_edit_step.py",
  "tests/test_006_pipeline_smoke.py",
  "tests/test_007_artifact_schema.py"
)

Write-Host "MODE: STRICT_002_007_FASTPATH"
Write-Host ("RUNNING: python -m pytest -q -x --maxfail=1 " + ($tests -join " "))

& python -m pytest -q -x --maxfail=1 @tests
if ($LASTEXITCODE -ne 0) {
  throw "P14_GUARD_FAIL: strict smoke tests failed."
}

Write-Host "P14_GUARD_PASS"
