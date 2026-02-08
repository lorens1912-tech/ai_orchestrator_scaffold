param(
  [switch]$Full
)

$ErrorActionPreference = "Stop"

function Fail([string]$msg) { throw $msg }

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

Write-Host "P14: Continuous Release Guard"

# 0) Drzewo musi być czyste
$dirty = git status --porcelain
if ($dirty) {
  Write-Host "BLOCKED_DIRTY_TREE:"
  $dirty | ForEach-Object { Write-Host $_ }
  Fail "Working tree must be clean before release/push."
}

function Stop-Listeners8000 {
  $lines = netstat -ano -p tcp | Select-String ":8000"
  $pids = @()
  foreach ($ln in $lines) {
    $parts = (($ln.ToString() -replace "\s+", " ").Trim()).Split(" ")
    if ($parts.Count -ge 5) { $pids += $parts[-1] }
  }

  $pids = $pids | Where-Object { $_ -match '^\d+$' } | Sort-Object -Unique
  foreach ($procId in $pids) {
    if ([int]$procId -gt 0) {
      try {
        Stop-Process -Id $procId -Force -ErrorAction Stop
        Write-Host "KILLED_PID:$procId"
      } catch {}
    }
  }
}

if ($Full) {
  Remove-Item Env:PYTEST_FASTPATH -ErrorAction SilentlyContinue
  Remove-Item Env:AGENT_TEST_MODE -ErrorAction SilentlyContinue
  Remove-Item Env:WRITE_MODEL_FORCE -ErrorAction SilentlyContinue
  $modeLabel = "FULL_SUITE_NO_FASTPATH"
  $pytestCmd = "python -m pytest -q -x --maxfail=1"
}
else {
  $env:PYTEST_FASTPATH = "1"
  $env:AGENT_TEST_MODE = "0"
  Remove-Item Env:WRITE_MODEL_FORCE -ErrorAction SilentlyContinue
  $modeLabel = "STRICT_002_007_FASTPATH"

  $tests = @(
    "tests/test_002_config_contract.py",
    "tests/test_003_write_step.py",
    "tests/test_004_critic_step.py",
    "tests/test_005_edit_step.py",
    "tests/test_006_pipeline_smoke.py",
    "tests/test_007_artifact_schema.py"
  )
  $pytestCmd = "python -m pytest -q -x --maxfail=1 " + ($tests -join " ")
}

Write-Host "MODE: $modeLabel"

# 1) ubij stary listener 8000 (stale profile)
Stop-Listeners8000
Start-Sleep -Milliseconds 500

# 2) start serwera z właściwym profilem
$pyLauncher = if (Get-Command py -ErrorAction SilentlyContinue) { "py -3.11" } else { "python" }

$launch = @"
Set-Location '$RepoRoot'
`$env:PYTEST_FASTPATH = '$($env:PYTEST_FASTPATH)'
`$env:AGENT_TEST_MODE = '$($env:AGENT_TEST_MODE)'
Remove-Item Env:WRITE_MODEL_FORCE -ErrorAction SilentlyContinue
$pyLauncher -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level warning
"@

$server = Start-Process -FilePath "powershell" `
  -ArgumentList "-NoProfile","-ExecutionPolicy","Bypass","-Command",$launch `
  -PassThru -WindowStyle Hidden

Write-Host "SERVER_PID:$($server.Id)"

try {
  # 3) health wait
  $ready = $false
  for ($i=0; $i -lt 60; $i++) {
    try {
      $h = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2
      if ($h) { $ready = $true; break }
    } catch {}
    Start-Sleep -Milliseconds 500
  }
  if (-not $ready) { Fail "PRECHECK_FAIL: /health not ready on 127.0.0.1:8000" }

  # 4) robust precheck /agent/step
  $preBody = @{
    mode   = "WRITE"
    preset = "DEFAULT"
    input  = "guard precheck"
  } | ConvertTo-Json -Depth 10

  $pre = Invoke-RestMethod -Method Post `
    -Uri "http://127.0.0.1:8000/agent/step" `
    -ContentType "application/json" `
    -Body $preBody `
    -TimeoutSec 15

  if ($null -eq $pre) { Fail "PRECHECK_FAIL: empty /agent/step response" }

  $isOk = $false
  if ($pre.PSObject.Properties.Name -contains "ok") {
    $isOk = [bool]$pre.ok
  } elseif ($pre.PSObject.Properties.Name -contains "status") {
    $isOk = ([string]$pre.status -eq "ok")
  }

  if (-not $isOk) {
    $raw = $pre | ConvertTo-Json -Compress -Depth 20
    Fail "PRECHECK_FAIL: /agent/step non-ok payload: $raw"
  }

  if (-not ($pre.PSObject.Properties.Name -contains "run_id") -or [string]::IsNullOrWhiteSpace([string]$pre.run_id)) {
    $raw = $pre | ConvertTo-Json -Compress -Depth 20
    Fail "PRECHECK_FAIL: /agent/step bez run_id: $raw"
  }

  Write-Host "PRECHECK_OK: run_id=$($pre.run_id)"

  # 5) tests
  Write-Host "RUNNING: $pytestCmd"
  Invoke-Expression $pytestCmd
  if ($LASTEXITCODE -ne 0) { Fail "pytest failed." }

  Write-Host "P14_GUARD_PASS"
  exit 0
}
finally {
  if ($server -and -not $server.HasExited) {
    Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
  }
  Stop-Listeners8000
}
