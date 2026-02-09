Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "P20.3: RETRY OUTCOME TELEMETRY FEEDBACK LOOP GATE"

python -m pytest -q .\tests\test_p20_3_retry_feedback_loop.py
if ($LASTEXITCODE -ne 0) { throw "P20_3_GATE_FAIL: unit tests failed" }

.\scripts\p20_2_auto_retry_gate.ps1
if ($LASTEXITCODE -ne 0) { throw "P20_3_GATE_FAIL: P20.2 gate failed" }

python .\scripts\p20_3_build_retry_feedback.py
if ($LASTEXITCODE -ne 0) { throw "P20_3_GATE_FAIL: feedback builder failed" }

$fb = Get-ChildItem .\reports\handoff\telemetry\P20_3_RETRY_FEEDBACK_*.json -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
if (-not $fb) { throw "P20_3_GATE_FAIL: missing P20_3_RETRY_FEEDBACK_*.json" }

$j = Get-Content $fb.FullName -Raw | ConvertFrom-Json -AsHashtable

$policy = ""
if ($j.ContainsKey("policy")) { $policy = [string]$j["policy"] }

$events = 0
if ($j.ContainsKey("events_count")) { $events = [int]$j["events_count"] }

$ok = $false
if ($j.ContainsKey("ok")) { $ok = [bool]$j["ok"] }

if ([string]::IsNullOrWhiteSpace($policy) -or $policy -eq "UNKNOWN") {
  throw "P20_3_GATE_FAIL: invalid policy in feedback JSON"
}
if ($events -le 0) {
  throw "P20_3_GATE_FAIL: events_count<=0 in feedback JSON"
}
if (-not $ok) {
  throw "P20_3_GATE_FAIL: feedback JSON reports ok=false"
}

Write-Host ("P20_3_FEEDBACK_OK: policy={0}; events={1}" -f $policy, $events)
Write-Host ("FEEDBACK_FILE: {0}" -f $fb.FullName)
Write-Host "P20_3_FEEDBACK_GATE_PASS"
