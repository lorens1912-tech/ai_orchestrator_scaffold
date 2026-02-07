param(
  [switch]$Full
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ROOT = "C:\AI\ai_orchestrator_scaffold"
Set-Location $ROOT

# --- helpers ---
function Write-Line($s) { Write-Host $s }

function Http-Json([string]$url, [int]$timeoutSec = 5) {
  try {
    return Invoke-RestMethod $url -TimeoutSec $timeoutSec
  } catch {
    return $null
  }
}

# --- proof output ---
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$proof = Join-Path $ROOT ("reports\P0_GATE_PROOF_" + $stamp + ".txt")
New-Item -ItemType Directory -Force -Path (Join-Path $ROOT "reports") | Out-Null

# --- 1) Runtime health check ---
$health = Http-Json "http://127.0.0.1:8000/health" 3
if ($null -eq $health -or -not $health.ok) {
  @(
    "P0_GATE_PROOF"
    "time=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    "status=FAIL"
    "reason=HEALTH_DOWN"
    "hint=Start server in 🔵: python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
  ) | Out-File $proof -Encoding UTF8

  throw "P0_GATE_FAIL: /health is down. PROOF=$proof"
}

# --- 2) Config validate ---
$validate = Http-Json "http://127.0.0.1:8000/config/validate" 5
if ($null -eq $validate -or -not $validate.ok) {
  @(
    "P0_GATE_PROOF"
    "time=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    "status=FAIL"
    "reason=VALIDATE_FAIL"
    "validate_raw=$(if($validate){($validate | ConvertTo-Json -Depth 10)}else{'NULL'})"
  ) | Out-File $proof -Encoding UTF8

  throw "P0_GATE_FAIL: /config/validate failed. PROOF=$proof"
}

# --- 3) Import check (catches NameError in tools.py etc.) ---
try {
  $importOut = python -c "import app.main; print('IMPORT_OK')" 2>&1
} catch {
  $importOut = "IMPORT_THROW: $($_.Exception.Message)"
}
if ($importOut -notmatch "IMPORT_OK") {
  @(
    "P0_GATE_PROOF"
    "time=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    "status=FAIL"
    "reason=IMPORT_FAIL"
    "import_out=$importOut"
  ) | Out-File $proof -Encoding UTF8

  throw "P0_GATE_FAIL: import app.main failed. PROOF=$proof"
}

# --- 4) P0 pytest contract ---
if (-not (Test-Path ".\tests\test_p0_runtime.py")) {
  @(
    "P0_GATE_PROOF"
    "time=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    "status=FAIL"
    "reason=MISSING_TEST_FILE"
    "missing=tests\test_p0_runtime.py"
  ) | Out-File $proof -Encoding UTF8

  throw "P0_GATE_FAIL: missing tests/test_p0_runtime.py. PROOF=$proof"
}

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$pytestOut = python -m pytest .\tests\test_p0_runtime.py -q 2>&1
$sw.Stop()
$pytestOk = ($LASTEXITCODE -eq 0)

if (-not $pytestOk) {
  @(
    "P0_GATE_PROOF"
    "time=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    "status=FAIL"
    "reason=PYTEST_P0_FAIL"
    "seconds=$($sw.Elapsed.TotalSeconds)"
    "pytest_out=$pytestOut"
  ) | Out-File $proof -Encoding UTF8

  throw "P0_GATE_FAIL: pytest P0 failed. PROOF=$proof"
}

# --- Optional: FULL suite (only if -Full) ---
$fullNote = "skipped"
if ($Full) {
  $fullSw = [System.Diagnostics.Stopwatch]::StartNew()
  $fullOut = python -m pytest -q 2>&1
  $fullSw.Stop()
  $fullOk = ($LASTEXITCODE -eq 0)
  $fullNote = "ok=$fullOk seconds=$($fullSw.Elapsed.TotalSeconds)"
  if (-not $fullOk) {
    @(
      "P0_GATE_PROOF"
      "time=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
      "status=FAIL"
      "reason=PYTEST_FULL_FAIL"
      "p0_seconds=$($sw.Elapsed.TotalSeconds)"
      "full_seconds=$($fullSw.Elapsed.TotalSeconds)"
      "full_out=$fullOut"
    ) | Out-File $proof -Encoding UTF8

    throw "P0_GATE_FAIL: full pytest failed. PROOF=$proof"
  }
}

@(
  "P0_GATE_PROOF"
  "time=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
  "status=PASS"
  "health_ok=True"
  "validate_ok=True"
  "modes_count=$($validate.modes_count)"
  "presets_count=$($validate.presets_count)"
  "p0_pytest_seconds=$($sw.Elapsed.TotalSeconds)"
  "full=$fullNote"
  "proof=$proof"
) | Out-File $proof -Encoding UTF8

Write-Line "P0_GATE=PASS | modes=$($validate.modes_count) presets=$($validate.presets_count) | proof=$proof"
