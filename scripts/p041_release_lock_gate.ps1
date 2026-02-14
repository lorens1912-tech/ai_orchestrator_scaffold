Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Set-Location C:\AI\ai_orchestrator_scaffold

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$reportDir = ".\reports\release_lock"
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
$report = "$reportDir\P041_RELEASE_LOCK_$ts.md"
$dirtyReport = "$reportDir\P041_RELEASE_LOCK_DIRTY_$ts.txt"

python -m py_compile .\app\main.py
python -m py_compile .\app\quality_contract.py

python -m pytest -q `
  .\tests\test_013_quality_contract.py::TestQualityContract013::test_quality_contract_min `
  .\tests\test_014_quality_uses_write_output.py::TestQualityUsesWriteOutput014::test_quality_is_not_reject_on_short_input `
  .\tests\test_011_quality_gate_v2.py `
  .\tests\test_040_bible_api_roundtrip.py `
  .\tests\test_070_team_layer.py `
  .\tests\test_080_resume_reuses_run_id.py `
  .\tests\test_082_resume_missing_run_folder_creates_new.py `
  .\tests\test_083_resume_empty_latest_file_scans_runs.py `
  -x

if ($LASTEXITCODE -ne 0) { throw "P041_BASELINE_TESTS_FAILED" }

.\scripts\p14_continuous_release_guard.ps1
if ($LASTEXITCODE -ne 0) { throw "P041_P14_GUARD_FAILED" }

$dirty = (git status --porcelain | Out-String).Trim()
if (-not [string]::IsNullOrWhiteSpace($dirty)) {
  git status --porcelain | Set-Content $dirtyReport -Encoding UTF8
  throw "P041_WORKTREE_NOT_CLEAN"
}

$branch = git rev-parse --abbrev-ref HEAD
$head = git rev-parse --short HEAD
$health = Invoke-RestMethod http://127.0.0.1:8001/health -TimeoutSec 10 | ConvertTo-Json -Depth 8

$lines = @(
  "# P041 RELEASE LOCK REPORT"
  "TIME: $(Get-Date -Format o)"
  "BRANCH: $branch"
  "HEAD: $head"
  "TESTS: QUALITY(011/013/014/040)+TEAM_RESUME(070/080/082/083)=PASS"
  "GUARD: P14_GUARD_PASS"
  "WORKTREE: CLEAN"
  "HEALTH: $health"
  "STATUS: GREEN"
)
Set-Content -Path $report -Value ($lines -join "`n") -Encoding UTF8

Write-Host "P041_RELEASE_LOCK_PASS: $report"
