$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path "$PSScriptRoot\..")

Write-Host "P20: STRICT TELEMETRY GATE"

python -m pytest -q .\tests\test_p20_quality_taxonomy.py
if ($LASTEXITCODE -ne 0) {
  throw "P20_GATE_FAIL: taxonomy tests failed."
}

.\scripts\p19_strict_release_gate.ps1
if ($LASTEXITCODE -ne 0) {
  throw "P20_GATE_FAIL: P19 strict release gate failed."
}

python .\scripts\p20_build_quality_telemetry.py --max-runs 200 --out-dir .\reports\handoff\telemetry
if ($LASTEXITCODE -ne 0) {
  throw "P20_GATE_FAIL: telemetry build failed."
}

Write-Host "P20_STRICT_TELEMETRY_GATE_PASS"
