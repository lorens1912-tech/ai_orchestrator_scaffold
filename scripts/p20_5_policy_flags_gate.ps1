param(
  [string]$BaseUrl = "http://127.0.0.1:8000"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Set-Location C:\AI\ai_orchestrator_scaffold

$py = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "py" }

# 1) Unit tests
& $py -m pytest -q .\tests\test_p20_5_policy_resolver.py .\tests\test_p20_5_targeted_adjust.py
if ($LASTEXITCODE -ne 0) { throw "PYTEST_FAIL_P20_5" }

# 2) OpenAPI check
$openapi = Invoke-RestMethod -Method Get -Uri "$BaseUrl/openapi.json" -TimeoutSec 20
$paths = $openapi.paths.PSObject.Properties.Name
if ($paths -notcontains "/policy/adjust/targeted") {
  throw "OPENAPI_MISSING_/policy/adjust/targeted"
}

# 3) API check: mode override priority
$body1 = @{
  preset = "ORCH_STANDARD"
  mode = "WRITE"
  current_policy = @{
    quality_min = 0.78
    max_retries = 2
    critic_weight = 1.00
    writer_temperature = 0.55
  }
  feedback = @{
    reject_rate = 0.55
    retry_rate = 0.60
    accept_rate = 0.20
    observed_quality = 0.61
    user_satisfaction = 0.30
  }
  flags = @{
    enabled_global = $true
    overrides_global = @{ quality_min = 0.79 }
    overrides_by_preset = @{ ORCH_STANDARD = @{ max_retries = 3 } }
    overrides_by_mode = @{ WRITE = @{ max_retries = 4; critic_weight = 1.25 } }
  }
} | ConvertTo-Json -Depth 50

$r1 = Invoke-RestMethod -Method Post -Uri "$BaseUrl/policy/adjust/targeted" -ContentType "application/json" -Body $body1 -TimeoutSec 20
if ($r1.status -ne "ok") { throw "API_STATUS_NOT_OK_1" }
if ($r1.telemetry.policy_source -ne "mode") { throw "SOURCE_NOT_MODE" }
if ([int]$r1.adjusted_policy.max_retries -lt 4) { throw "MODE_OVERRIDE_NOT_APPLIED" }

# 4) API check: disabled by mode -> skip
$body2 = @{
  preset = "ORCH_STANDARD"
  mode = "WRITE"
  current_policy = @{
    quality_min = 0.78
    max_retries = 2
    critic_weight = 1.00
    writer_temperature = 0.55
  }
  feedback = @{
    reject_rate = 0.55
    retry_rate = 0.60
    accept_rate = 0.20
    observed_quality = 0.61
    user_satisfaction = 0.30
  }
  flags = @{
    enabled_global = $true
    enabled_by_mode = @{ WRITE = $false }
    overrides_by_mode = @{ WRITE = @{ quality_min = 0.88 } }
  }
} | ConvertTo-Json -Depth 50

$r2 = Invoke-RestMethod -Method Post -Uri "$BaseUrl/policy/adjust/targeted" -ContentType "application/json" -Body $body2 -TimeoutSec 20
if ($r2.status -ne "ok") { throw "API_STATUS_NOT_OK_2" }
if ($r2.audit.band -ne "skip") { throw "SKIP_EXPECTED" }
if ($r2.audit.reason -ne "disabled_by_flags") { throw "SKIP_REASON_EXPECTED" }

Write-Host "P20_5_GATE_PASS: True"
Write-Host "P20_5_CHECK_1_SOURCE: $($r1.telemetry.policy_source)"
Write-Host "P20_5_CHECK_2_BAND: $($r2.audit.band)"
