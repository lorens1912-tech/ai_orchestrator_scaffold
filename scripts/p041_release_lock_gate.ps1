Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Set-Location C:\AI\ai_orchestrator_scaffold

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$tempRoot = Join-Path $env:TEMP "agentai_release_lock"
New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
$report = Join-Path $tempRoot "P041_RELEASE_LOCK_$ts.md"
$dirtyReport = Join-Path $tempRoot "P041_RELEASE_LOCK_DIRTY_$ts.txt"

$runtimeTracked = @(
  "books/test_book/draft/latest.txt"
)

$runtimeDirs = @(
  "runs",
  ".pytest_cache",
  "app/__pycache__",
  "tests/__pycache__",
  "reports/dirty_diag",
  "books/book_runtime_test"
)

function Normalize-Path([string]$p) {
  return $p.Replace('\','/').Trim()
}

function Get-DirtyPaths {
  $lines = @(git status --porcelain=v1 2>$null)
  if ($LASTEXITCODE -ne 0) { throw "P041_GIT_STATUS_FAILED" }

  $out = New-Object System.Collections.Generic.List[string]
  foreach ($l in $lines) {
    if ([string]::IsNullOrWhiteSpace($l)) { continue }
    if ($l.Length -ge 4) {
      $path = Normalize-Path $l.Substring(3)
      if (-not [string]::IsNullOrWhiteSpace($path)) {
        [void]$out.Add($path)
      }
    }
  }

  return @($out | Sort-Object -Unique)
}

function Sanitize-KnownRuntime {
  foreach ($f in $runtimeTracked) {
    git restore --staged --worktree -- $f 2>$null
  }
  foreach ($d in $runtimeDirs) {
    if (Test-Path $d) { Remove-Item $d -Recurse -Force -ErrorAction Stop }
  }
}

function Assert-CleanOrFail([string]$phase) {
  $dirty = @(Get-DirtyPaths)
  $dirty = @($dirty | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })

  if ($dirty.Count -gt 0) {
    $dirty | Set-Content $dirtyReport -Encoding UTF8
    throw "P041_${phase}_WORKTREE_NOT_CLEAN"
  }
}

# PRECHECK
Sanitize-KnownRuntime
Assert-CleanOrFail "PRECHECK"

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

# POSTCHECK
Sanitize-KnownRuntime
Assert-CleanOrFail "POSTCHECK"

$branch = git rev-parse --abbrev-ref HEAD
$head = git rev-parse --short HEAD
try {
  $health = Invoke-RestMethod http://127.0.0.1:8001/health -TimeoutSec 10 | ConvertTo-Json -Depth 8
} catch {
  $health = "UNAVAILABLE"
}

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
