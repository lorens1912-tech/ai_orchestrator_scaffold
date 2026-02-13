$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path "$PSScriptRoot\..")

Write-Host "P19: STRICT RELEASE GATE"

$tests = @(
  ".\tests\test_p16_quality_contract.py",
  ".\tests\test_p17_pipeline_quality_contract.py",
  ".\tests\test_p18_preset_matrix_quality_contract.py",
  ".\tests\test_p19_quality_direct_contract.py"
)

python -m pytest -q $tests
if ($LASTEXITCODE -ne 0) {
  throw "P19_GATE_FAIL: regression P16/P17/P18/P19 failed."
}

try {
  .\scripts\p14_continuous_release_guard.ps1
} catch {
  throw ("P19_GATE_FAIL: P14 guard failed: " + $_.Exception.Message)
}

Write-Host "P19_STRICT_RELEASE_GATE_PASS"
