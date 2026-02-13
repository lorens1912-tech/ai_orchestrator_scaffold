param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [string]$RepoRoot = "C:\AI\ai_orchestrator_scaffold"
)

$ErrorActionPreference = "Stop"
Set-Location $RepoRoot

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$reportTxt  = ".\reports\P13_STRICT_TEST_$ts.txt"
$reportJson = ".\reports\P13_STRICT_TEST_$ts.json"
$pytestLog  = ".\reports\P13_STRICT_PYTEST_$ts.txt"

$state = [ordered]@{
  TIMESTAMP             = (Get-Date -Format s)
  BRANCH                = ((git rev-parse --abbrev-ref HEAD) 2>$null).Trim()
  HEAD                  = ((git rev-parse --short HEAD) 2>$null).Trim()
  COMPILE_OK            = $false
  HEALTH_OK             = $false
  PRESETS_OK            = $false
  PRESETS_SOURCE        = ""
  PRESETS_COUNT         = 0
  OPENAPI_OK            = $false
  DUPLICATE_ROUTE_COUNT = 9999
  UNITTEST_OK           = $false
  P13_GATE_PASS         = $false
  ERROR                 = ""
  PYTEST_LOG            = $pytestLog
}

# 1) compile
try {
  python -m compileall -q app tests | Out-Null
  if ($LASTEXITCODE -eq 0) { $state.COMPILE_OK = $true } else { throw "compileall failed" }
} catch { $state.ERROR = "compile: $($_.Exception.Message)" }

# 2) health
try {
  Invoke-RestMethod -Method Get -Uri "$BaseUrl/health" -TimeoutSec 8 | Out-Null
  $state.HEALTH_OK = $true
} catch { if (-not $state.ERROR) { $state.ERROR = "health: $($_.Exception.Message)" } }

# 3) presets contract
try {
  $p = Invoke-RestMethod -Method Get -Uri "$BaseUrl/config/presets" -TimeoutSec 10
  if ($p.PSObject.Properties.Name -contains "source") { $state.PRESETS_SOURCE = [string]$p.source }

  if ($p.PSObject.Properties.Name -contains "presets_count") {
    $state.PRESETS_COUNT = [int]$p.presets_count
  } elseif ($p.PSObject.Properties.Name -contains "presets") {
    if ($p.presets -is [System.Collections.IDictionary]) { $state.PRESETS_COUNT = $p.presets.Keys.Count }
    else { $state.PRESETS_COUNT = @($p.presets).Count }
  } else {
    throw "missing presets_count/presets"
  }

  $state.PRESETS_OK = ($state.PRESETS_SOURCE -eq "config_registry") -and ($state.PRESETS_COUNT -ge 1)
  if (-not $state.PRESETS_OK) { throw "source=$($state.PRESETS_SOURCE), count=$($state.PRESETS_COUNT)" }
} catch { if (-not $state.ERROR) { $state.ERROR = "presets: $($_.Exception.Message)" } }

# 4) openapi
try {
  $o = Invoke-RestMethod -Method Get -Uri "$BaseUrl/openapi.json" -TimeoutSec 10
  if (-not ($o.openapi -and $o.paths)) { throw "not parseable" }
  $paths = @($o.paths.PSObject.Properties.Name)
  if ($paths -notcontains "/config/presets") { throw "missing /config/presets" }
  if ($paths -notcontains "/config/validate") { throw "missing /config/validate" }
  $state.OPENAPI_OK = $true
} catch { if (-not $state.ERROR) { $state.ERROR = "openapi: $($_.Exception.Message)" } }

# 5) duplicate routes (runtime, fail hard on import)
try {
  $dupPy = Join-Path $env:TEMP ("p13_dup_" + $ts + ".py")
@"
import os,sys,collections
sys.path.insert(0, os.getcwd())
from app.main import app
sig=[]
for r in app.routes:
    methods = sorted(m for m in (getattr(r, "methods", None) or []) if m not in {"HEAD","OPTIONS"})
    path = getattr(r, "path", None)
    if path and methods:
        for m in methods:
            sig.append((m, path))
c = collections.Counter(sig)
print(sum(1 for v in c.values() if v > 1))
"@ | Set-Content -Path $dupPy -Encoding UTF8

  $dupRaw = python $dupPy 2>&1
  $ec = $LASTEXITCODE
  Remove-Item $dupPy -Force -ErrorAction SilentlyContinue

  if ($ec -ne 0) { throw "python import failed: $dupRaw" }

  $state.DUPLICATE_ROUTE_COUNT = [int]$dupRaw
  if ($state.DUPLICATE_ROUTE_COUNT -ne 0) { throw "duplicates=$($state.DUPLICATE_ROUTE_COUNT)" }
} catch { if (-not $state.ERROR) { $state.ERROR = "duplicate-routes: $($_.Exception.Message)" } }

# 6) pytest strict (tylko releasowe)
try {
  python -m pytest -q tests/test_p13_regression.py tests/test_p13_contracts_strict.py --maxfail=1 *>&1 | Tee-Object -FilePath $pytestLog | Out-Null
  if ($LASTEXITCODE -eq 0) { $state.UNITTEST_OK = $true } else { throw "pytest failed (see $pytestLog)" }
} catch { if (-not $state.ERROR) { $state.ERROR = "pytest: $($_.Exception.Message)" } }

$state.P13_GATE_PASS = $state.COMPILE_OK -and $state.HEALTH_OK -and $state.PRESETS_OK -and ($state.PRESETS_SOURCE -eq "config_registry") -and ($state.PRESETS_COUNT -ge 1) -and $state.OPENAPI_OK -and ($state.DUPLICATE_ROUTE_COUNT -eq 0) -and $state.UNITTEST_OK

$state | ConvertTo-Json -Depth 10 | Set-Content $reportJson -Encoding UTF8
$lines = @()
foreach ($k in $state.Keys) { $lines += "{0}: {1}" -f $k, $state[$k] }
$lines += "JSON_REPORT: $reportJson"
$lines += "PYTEST_LOG: $pytestLog"
$lines | Set-Content $reportTxt -Encoding UTF8

Write-Host "STRICT_TXT_REPORT: $reportTxt"
Write-Host "STRICT_JSON_REPORT: $reportJson"
Get-Content $reportTxt -Raw
