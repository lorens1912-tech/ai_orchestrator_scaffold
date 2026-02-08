param(
  [switch]$Full
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

Write-Host "P14: Continuous Release Guard"

$dirty = git status --porcelain
if ($dirty) {
  Write-Host "BLOCKED_DIRTY_TREE:"
  $dirty | ForEach-Object { Write-Host $_ }
  throw "Working tree must be clean before release/push."
}

if ($Full) {
  # tryb P15+ (pełny) — bez fastpath
  Remove-Item Env:PYTEST_FASTPATH -ErrorAction SilentlyContinue
  Remove-Item Env:AGENT_TEST_MODE -ErrorAction SilentlyContinue
  Remove-Item Env:WRITE_MODEL_FORCE -ErrorAction SilentlyContinue

  $cmd = "python -m pytest -q -x --maxfail=1"
}
else {
  # tryb P14 — TYLKO strict pack 002-007, fastpath ON
  $env:PYTEST_FASTPATH = "1"
  $env:AGENT_TEST_MODE = "0"
  Remove-Item Env:WRITE_MODEL_FORCE -ErrorAction SilentlyContinue

  $tests = @(
    "tests/test_002_config_contract.py"
    "tests/test_003_write_step.py"
    "tests/test_004_critic_step.py"
    "tests/test_005_edit_step.py"
    "tests/test_006_pipeline_smoke.py"
    "tests/test_007_artifact_schema.py"
  )

  $cmd = "python -m pytest -q -x --maxfail=1 " + ($tests -join " ")
}

Write-Host "RUNNING:" $cmd
Invoke-Expression $cmd
if ($LASTEXITCODE -ne 0) { throw "pytest failed." }

Write-Host "P14_GUARD_PASS"
exit 0
