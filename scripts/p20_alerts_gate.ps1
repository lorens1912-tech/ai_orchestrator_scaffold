param(
  [string]$ThresholdsPath = ".\config\quality_alert_thresholds.json"
)

$ErrorActionPreference = "Stop"
Write-Host "P20.1: ALERTS GATE"

.\scripts\p20_strict_telemetry_gate.ps1
if ($LASTEXITCODE -ne 0) { throw "P20.1_GATE_FAIL: strict telemetry gate failed." }

python .\scripts\p20_evaluate_quality_alerts.py --thresholds $ThresholdsPath
if ($LASTEXITCODE -ne 0) { throw "P20.1_GATE_FAIL: alert policy evaluator failed." }

Write-Host "P20.1_ALERTS_GATE_PASS"
