param([switch]$Full)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

function Fail([string]$msg) { throw $msg }

Write-Host "P14: Continuous Release Guard"
$mode = if ($Full) { "FULL" } else { "STRICT_002_007_FASTPATH" }
Write-Host "MODE:" $mode

$dirty = git status --porcelain
if ($dirty) {
  Write-Host "BLOCKED_DIRTY_TREE:"
  $dirty | ForEach-Object { Write-Host $_ }
  Fail "Working tree must be clean before release/push."
}

# Snapshot env
$trackedEnv = @("PYTEST_FASTPATH","AGENT_TEST_MODE","WRITE_MODEL_FORCE")
$envBackup = @{}
foreach ($name in $trackedEnv) {
  $item = Get-Item "Env:$name" -ErrorAction SilentlyContinue
  if ($item) { $envBackup[$name] = $item.Value } else { $envBackup[$name] = $null }
}

function Restore-Env {
  param([hashtable]$Backup)
  foreach ($k in $Backup.Keys) {
    if ($null -eq $Backup[$k]) { Remove-Item "Env:$k" -ErrorAction SilentlyContinue }
    else { Set-Item "Env:$k" $Backup[$k] }
  }
}

function Stop-Listener8000 {
  try {
    $listeners = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
  } catch {
    $listeners = @()
  }

  if ($listeners) {
    $procIds = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $procIds) {
      try { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue } catch {}
    }
    Start-Sleep -Milliseconds 600
  }
}

function Wait-Health {
  param([int]$TimeoutSec = 25)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -Method Get -TimeoutSec 2 | Out-Null
      return $true
    } catch {
      Start-Sleep -Milliseconds 300
    }
  }
  return $false
}

function Precheck-AgentStep {
  $body = @{
    mode   = "WRITE"
    preset = "DEFAULT"
    input  = "guard precheck"
  } | ConvertTo-Json -Depth 10

  $resp = Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8000/agent/step" `
    -ContentType "application/json" `
    -Body $body `
    -TimeoutSec 20

  if ($null -eq $resp) { Fail "PRECHECK_FAIL: empty response from /agent/step" }

  $okFlag = (($resp.PSObject.Properties.Name -contains "ok" -and $resp.ok -eq $true) -or
             ($resp.PSObject.Properties.Name -contains "status" -and $resp.status -eq "ok"))

  $hasRunId = ($resp.PSObject.Properties.Name -contains "run_id" -and -not [string]::IsNullOrWhiteSpace([string]$resp.run_id))
  $hasArtifactPath = ($resp.PSObject.Properties.Name -contains "artifact_path" -and -not [string]::IsNullOrWhiteSpace([string]$resp.artifact_path))
  $hasArtifacts = ($resp.PSObject.Properties.Name -contains "artifacts" -and $null -ne $resp.artifacts -and $resp.artifacts.Count -gt 0)

  if (-not $okFlag) {
    $raw = $resp | ConvertTo-Json -Depth 8 -Compress
    Fail "PRECHECK_FAIL: /agent/step returned non-ok payload: $raw"
  }

  if (-not ($hasRunId -or $hasArtifactPath -or $hasArtifacts)) {
    $raw = $resp | ConvertTo-Json -Depth 8 -Compress
    Write-Warning "PRECHECK_WARN: /agent/step without run/artifact markers. Continue, pytest decides. payload=$raw"
  }
}

$serverProc = $null
try {
  if ($Full) {
    Remove-Item Env:PYTEST_FASTPATH -ErrorAction SilentlyContinue
    Remove-Item Env:AGENT_TEST_MODE -ErrorAction SilentlyContinue
    Remove-Item Env:WRITE_MODEL_FORCE -ErrorAction SilentlyContinue
  } else {
    $env:PYTEST_FASTPATH = "1"
    $env:AGENT_TEST_MODE = "0"
    Remove-Item Env:WRITE_MODEL_FORCE -ErrorAction SilentlyContinue
  }

  Stop-Listener8000
  $serverProc = Start-Process -FilePath "python" -ArgumentList @("-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000") -PassThru -WindowStyle Hidden

  if (-not (Wait-Health -TimeoutSec 25)) { Fail "Server precheck failed: health timeout on 127.0.0.1:8000" }
  Precheck-AgentStep

  if ($Full) {
    $cmd = "python -m pytest -q -x --maxfail=1"
  } else {
    $tests = @(
      "tests/test_002_config_contract.py",
      "tests/test_003_write_step.py",
      "tests/test_004_critic_step.py",
      "tests/test_005_edit_step.py",
      "tests/test_006_pipeline_smoke.py",
      "tests/test_007_artifact_schema.py"
    )
    $cmd = "python -m pytest -q -x --maxfail=1 " + ($tests -join " ")
  }

  Write-Host "RUNNING:" $cmd
  Invoke-Expression $cmd
  if ($LASTEXITCODE -ne 0) { Fail "pytest failed." }

  Write-Host "P14_GUARD_PASS"
  exit 0
}
finally {
  if ($serverProc -and -not $serverProc.HasExited) {
    try { Stop-Process -Id $serverProc.Id -Force -ErrorAction SilentlyContinue } catch {}
  }
  Restore-Env -Backup $envBackup
}
