param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Set-Location (Resolve-Path "$PSScriptRoot\..").Path

function Restore-TestArtifacts {
    if (Test-Path "books/test_book/draft/latest.txt") {
        git restore --staged --worktree -- "books/test_book/draft/latest.txt" 2>$null
    }
    if (Test-Path "runs") {
        Get-ChildItem "runs" -Directory -Filter "test_run_*" -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "P042_PRE_CLEAN_START"
Restore-TestArtifacts
Write-Host "P042_PRE_CLEAN_DONE"

Write-Host "P042_COMPILE_START"
python -m py_compile .\app\main.py
python -m py_compile .\app\orchestrator_stub.py
if (Test-Path ".\app\quality_contract.py") {
    python -m py_compile .\app\quality_contract.py
}
Write-Host "P042_COMPILE_DONE"

Write-Host "P042_TEST_START"
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
if ($LASTEXITCODE -ne 0) { throw "BASELINE_GATE_FAILED" }
Write-Host "P042_TEST_DONE"

Write-Host "P042_GUARD_START"
if (Test-Path ".\scripts\p14_continuous_release_guard.ps1") {
    .\scripts\p14_continuous_release_guard.ps1
}
elseif (Test-Path ".\scripts\p14_release_guard.ps1") {
    .\scripts\p14_release_guard.ps1
}
else {
    throw "P14_GUARD_SCRIPT_MISSING"
}
if ($LASTEXITCODE -ne 0) { throw "P14_GUARD_FAILED" }
Write-Host "P042_GUARD_DONE"

Write-Host "P042_POST_CLEAN_START"
Restore-TestArtifacts
Write-Host "P042_POST_CLEAN_DONE"

$dirty = git status --porcelain
if ($dirty) {
    Write-Host "WORKTREE_DIRTY_AFTER_GATE"
    $dirty
    throw "WORKTREE_DIRTY_AFTER_GATE"
}

Write-Host "P042_GATE_PASS"
