$ErrorActionPreference = "Stop"
Write-Host "P20.2: AUTO RETRY GATE"

if (!(Test-Path ".\scripts\p20_2_tune_auto_retry_policy.py")) { throw "MISSING: scripts/p20_2_tune_auto_retry_policy.py" }
if (!(Test-Path ".\tests\test_p20_2_auto_retry_policy.py")) { throw "MISSING: tests/test_p20_2_auto_retry_policy.py" }
if (!(Test-Path ".\scripts\p20_alerts_gate.ps1")) { throw "MISSING: scripts/p20_alerts_gate.ps1" }

# telemetry + policy snapshot
.\scripts\p20_alerts_gate.ps1
if ($LASTEXITCODE -ne 0) { throw "P20_2_GATE_FAIL: p20_alerts_gate.ps1 failed" }

# tuning retry policy
python .\scripts\p20_2_tune_auto_retry_policy.py
if ($LASTEXITCODE -ne 0) { throw "P20_2_GATE_FAIL: p20_2_tune_auto_retry_policy.py failed" }

# testy P20.2
python -m pytest -q .\tests\test_p20_2_auto_retry_policy.py
if ($LASTEXITCODE -ne 0) { throw "P20_2_GATE_FAIL: tests/test_p20_2_auto_retry_policy.py failed" }

Write-Host "P20_2_AUTO_RETRY_GATE_PASS"
