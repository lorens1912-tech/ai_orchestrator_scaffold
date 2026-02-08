param(
  [switch]$Full
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$script:GuardServer = $null

function Get-ListenPids8000 {
  try {
    @(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction Stop |
      Select-Object -ExpandProperty OwningProcess -Unique)
  } catch {
    @()
  }
}

function Stop-Listen8000 {
  $pids = @(Get-ListenPids8000)
  if ($pids.Count -gt 0) {
    foreach ($pid in $pids) {
      try { Stop-Process -Id $pid -Force -ErrorAction Stop } catch {}
    }
  }

  $deadline = (Get-Date).AddSeconds(8)
  do {
    Start-Sleep -Milliseconds 250
    $left = @(Get-ListenPids8000)
    if ($left.Count -eq 0) { return }
  } while ((Get-Date) -lt $deadline)

  throw "Port 8000 nadal zajęty po kill: $(@(Get-ListenPids8000) -join ', ')"
}

function Start-GuardServer([bool]$UseFastpath) {
  Stop-Listen8000

  $boot = @()
  $boot += "Set-Location '$RepoRoot'"

  if ($UseFastpath) {
    $boot += '$env:PYTEST_FASTPATH="1"'
    $boot += '$env:AGENT_TEST_MODE="0"'
  } else {
    $boot += 'Remove-Item Env:PYTEST_FASTPATH -ErrorAction SilentlyContinue'
    $boot += 'Remove-Item Env:AGENT_TEST_MODE -ErrorAction SilentlyContinue'
  }

  $boot += 'Remove-Item Env:WRITE_MODEL_FORCE -ErrorAction SilentlyContinue'
  $boot += 'python -m uvicorn app.main:app --host 127.0.0.1 --port 8000'

  $bootScript = [string]::Join("; ", $boot)

  $script:GuardServer = Start-Process powershell.exe -PassThru -WindowStyle Hidden -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $bootScript
  )

  $deadline = (Get-Date).AddSeconds(20)
  $lastErr = $null

  do {
    Start-Sleep -Milliseconds 300
    try {
      $cfg = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/config/validate" -TimeoutSec 2

      if ($UseFastpath -and $cfg.source -ne "PYTEST_FASTPATH") {
        throw "SERVER_PROFILE_MISMATCH: source=$($cfg.source)"
      }

      $probeBody = @{
        mode   = "WRITE"
        preset = "DEFAULT"
        input  = "guard_probe"
      } | ConvertTo-Json -Depth 20

      $probe = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/agent/step" `
        -ContentType "application/json" -Body $probeBody -TimeoutSec 8

      if (-not $probe.run_id) {
        throw "PRECHECK_FAIL: /agent/step bez run_id"
      }

      return
    } catch {
      $lastErr = $_.Exception.Message
    }
  } while ((Get-Date) -lt $deadline)

  throw "Server precheck failed: $lastErr"
}

try {
  Write-Host "P14: Continuous Release Guard"

  $dirty = git status --porcelain
  if ($dirty) {
    Write-Host "BLOCKED_DIRTY_TREE:"
    $dirty | ForEach-Object { Write-Host $_ }
    throw "Working tree must be clean before release/push."
  }

  if ($Full) {
    Write-Host "MODE: FULL"
    Start-GuardServer $false
    $cmd = "python -m pytest -q -x --maxfail=1"
  } else {
    Write-Host "MODE: STRICT_002_007_FASTPATH"
    Start-GuardServer $true
    $tests = @(
      "tests/test_002_config_contract.py"
      "tests/test_003_write_step.py"
      "tests/test_004_critic_step.py"
      "tests/test_005_edit_step.py"
      "tests/test_006_pipeline_smoke.py"
      "tests/test_007_artifact_schema.py"
    )
    $cmd = "python -m pytest -q -x --maxfail=1 " + ($tests -join " ")
  }

  Write-Host "RUNNING:" $cmd
  Invoke-Expression $cmd
  if ($LASTEXITCODE -ne 0) { throw "pytest failed." }

  Write-Host "P14_GUARD_PASS"
  exit 0
}
finally {
  if ($script:GuardServer -and -not $script:GuardServer.HasExited) {
    try { Stop-Process -Id $script:GuardServer.Id -Force -ErrorAction SilentlyContinue } catch {}
  }
}
