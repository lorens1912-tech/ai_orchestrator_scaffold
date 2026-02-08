param(
  [switch]$Full
)

$ErrorActionPreference = "Stop"

function Fail([string]$msg) { throw $msg }

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

Write-Host "P14: Continuous Release Guard"

function Stop-Listeners8000 {
  try {
    $pids = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction Stop |
      Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $pids) {
      if ($procId -and $procId -ne $PID) {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        Write-Host "KILLED_PID:$procId"
      }
    }
  } catch { }
}

function To-Obj($x) {
  if ($null -eq $x) { return $null }
  if ($x -is [string]) {
    $s = $x.Trim()
    if ($s.StartsWith("{") -or $s.StartsWith("[")) {
      try { return ($s | ConvertFrom-Json -Depth 100) } catch { return $null }
    }
  }
  return $x
}

function Is-OkResponse($o) {
  if ($null -eq $o) { return $false }
  $names = @($o.PSObject.Properties | ForEach-Object { $_.Name.ToLowerInvariant() })

  if ($names -contains "ok") {
    $v = $o.ok
    if ($v -is [bool]) { return $v }
    return (([string]$v).Trim().ToLowerInvariant() -eq "true")
  }

  if ($names -contains "status") {
    return (([string]$o.status).Trim().ToLowerInvariant() -eq "ok")
  }

  return $false
}

# Clean tree required
$dirty = git status --porcelain
if ($dirty) {
  Write-Host "BLOCKED_DIRTY_TREE:"
  $dirty | ForEach-Object { Write-Host $_ }
  Fail "Working tree must be clean before release/push."
}

# Mode + env
if ($Full) {
  Remove-Item Env:PYTEST_FASTPATH -ErrorAction SilentlyContinue
  Remove-Item Env:AGENT_TEST_MODE -ErrorAction SilentlyContinue
  Remove-Item Env:WRITE_MODEL_FORCE -ErrorAction SilentlyContinue
  $cmd = "python -m pytest -q -x --maxfail=1"
  $modeLabel = "FULL_SUITE_NO_FASTPATH"
}
else {
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
  $modeLabel = "STRICT_002_007_FASTPATH"
}

Write-Host "MODE: $modeLabel"

# Ensure no stale listener
Stop-Listeners8000

$server = $null
try {
  # Start managed server
  $server = Start-Process `
    -FilePath "python" `
    -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000" `
    -WorkingDirectory $RepoRoot `
    -PassThru `
    -WindowStyle Hidden

  Write-Host "SERVER_PID:$($server.Id)"

  # Wait /health
  $deadline = (Get-Date).AddSeconds(20)
  $healthOk = $false
  do {
    Start-Sleep -Milliseconds 400
    try {
      $hRaw = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2
      $h = To-Obj $hRaw
      if ($h -ne $null) { $healthOk = $true }
    } catch { }
  } while ((-not $healthOk) -and ((Get-Date) -lt $deadline))

  if (-not $healthOk) {
    Fail "PRECHECK_FAIL: /health unavailable"
  }

  # /agent/step precheck
  $body = @{
    mode   = "WRITE"
    preset = "DEFAULT"
    input  = "precheck"
  } | ConvertTo-Json -Depth 20

  $preRaw = Invoke-RestMethod -Method Post `
    -Uri "http://127.0.0.1:8000/agent/step" `
    -ContentType "application/json" `
    -Body $body `
    -TimeoutSec 12

  $pre = To-Obj $preRaw
  if ($null -eq $pre) {
    Fail "PRECHECK_FAIL: /agent/step invalid JSON payload"
  }

  if (-not (Is-OkResponse $pre)) {
    Fail ("PRECHECK_FAIL: /agent/step non-ok payload: " + ($pre | ConvertTo-Json -Compress -Depth 50))
  }

  if (-not $pre.run_id) {
    Fail ("PRECHECK_FAIL: /agent/step bez run_id: " + ($pre | ConvertTo-Json -Compress -Depth 50))
  }

  Write-Host "RUNNING:" $cmd
  Invoke-Expression $cmd
  if ($LASTEXITCODE -ne 0) { Fail "pytest failed." }

  Write-Host "P14_GUARD_PASS"
}
finally {
  if ($server -and -not $server.HasExited) {
    Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
  }
  Stop-Listeners8000
}

exit 0
