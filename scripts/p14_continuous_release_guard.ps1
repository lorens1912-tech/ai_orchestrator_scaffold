param(
  [switch]$Full
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

Write-Host "P14: Continuous Release Guard"

function Stop-Port8000Process {
  try {
    $conns = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
    if ($conns) {
      $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
      foreach ($p in $pids) {
        if ($p -and $p -ne $PID) {
          try { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue } catch {}
        }
      }
      Start-Sleep -Milliseconds 500
    }
  } catch {}
}

function Wait-ApiReady {
  param([int]$MaxTries = 80)
  for ($i = 1; $i -le $MaxTries; $i++) {
    try {
      $r = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/config/validate" -TimeoutSec 2
      if ($r) { return $true }
    } catch {}
    Start-Sleep -Milliseconds 500
  }
  return $false
}

function Start-ManagedServer {
  param([bool]$Fastpath)

  Stop-Port8000Process

  if ($Fastpath) {
    $cmd = "Set-Location '$RepoRoot'; `$env:AGENT_TEST_MODE='0'; `$env:PYTEST_FASTPATH='1'; Remove-Item Env:WRITE_MODEL_FORCE -ErrorAction SilentlyContinue; uvicorn app.main:app --host 127.0.0.1 --port 8000"
  } else {
    $cmd = "Set-Location '$RepoRoot'; `$env:AGENT_TEST_MODE='1'; Remove-Item Env:PYTEST_FASTPATH -ErrorAction SilentlyContinue; Remove-Item Env:WRITE_MODEL_FORCE -ErrorAction SilentlyContinue; uvicorn app.main:app --host 127.0.0.1 --port 8000"
  }

  $proc = Start-Process powershell.exe -ArgumentList "-NoProfile","-ExecutionPolicy","Bypass","-Command",$cmd -PassThru -WindowStyle Hidden

  if (-not (Wait-ApiReady -MaxTries 80)) {
    try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
    throw "Managed server failed to start on 127.0.0.1:8000"
  }

  return $proc
}

# dirty-tree check, ale ignoruj lokalne logi/handoff
$dirtyRaw = git status --porcelain --untracked-files=all
$dirty = @()
if ($dirtyRaw) {
  $dirty = $dirtyRaw | Where-Object {
    ($_ -notmatch '^\?\?\s+reports\/') -and
    ($_ -notmatch '^\?\?\s+HANDOFF_EXIT_.*\.md$')
  }
}
if ($dirty.Count -gt 0) {
  Write-Host "BLOCKED_DIRTY_TREE:"
  $dirty | ForEach-Object { Write-Host $_ }
  throw "Working tree must be clean before release/push (excluding local reports/handoff)."
}

$server = $null
try {
  if ($Full) {
    Write-Host "MODE: FULL_REAL"
    $server = Start-ManagedServer -Fastpath:$false
    $cmd = "python -m pytest -q -x --maxfail=1"
  }
  else {
    Write-Host "MODE: STRICT_002_007_FASTPATH"
    $server = Start-ManagedServer -Fastpath:$true
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
  if ($server -and $server.Id) {
    try { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue } catch {}
  }
}
