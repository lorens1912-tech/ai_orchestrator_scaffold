param([switch]$Full)
$ErrorActionPreference = "Stop"
function Fail([string]$m){ throw $m }

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot
Write-Host "P14: Continuous Release Guard"

function Get-DirtyLines {
  $raw = git status --porcelain
  if (-not $raw) { return @() }
  @($raw -split "`r?`n" | Where-Object { $_ -and $_.Trim() -ne "" })
}

function Stop-Stale8000 {
  try {
    $conns = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
    if ($conns) {
      $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
      foreach ($procId in $pids) {
        try {
          Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
          Write-Host "KILLED_PID:$procId"
        } catch {}
      }
      Start-Sleep -Milliseconds 400
    }
  } catch {}
}

$dirty = Get-DirtyLines
if ($dirty.Count -gt 0) {
  Write-Host "BLOCKED_DIRTY_TREE:"
  $dirty | ForEach-Object { Write-Host $_ }
  Fail "Working tree must be clean before release/push."
}

if ($Full) {
  Write-Host "MODE: FULL_SUITE_NO_FASTPATH"
  Remove-Item Env:PYTEST_FASTPATH -ErrorAction SilentlyContinue
  Remove-Item Env:AGENT_TEST_MODE -ErrorAction SilentlyContinue
  Remove-Item Env:WRITE_MODEL_FORCE -ErrorAction SilentlyContinue
  $cmd = "python -m pytest -q -x --maxfail=1"
}
else {
  Write-Host "MODE: STRICT_002_007_FASTPATH"
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
}

# managed server precheck (deterministyczny)
Stop-Stale8000
$server = Start-Process -FilePath "python" -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000" -PassThru -WindowStyle Hidden
Write-Host "SERVER_PID:$($server.Id)"
Start-Sleep -Seconds 2

try {
  $body = @{
    mode   = "WRITE"
    preset = "DEFAULT"
    input  = "guard precheck"
  } | ConvertTo-Json -Depth 10

  $resp = Invoke-WebRequest -Method Post -Uri "http://127.0.0.1:8000/agent/step" -ContentType "application/json" -Body $body -TimeoutSec 20
  $raw  = [string]$resp.Content

  if ([string]::IsNullOrWhiteSpace($raw)) {
    Fail "PRECHECK_FAIL: /agent/step empty response"
  }

  $okRegex1  = [regex]::IsMatch($raw, '"ok"\s*:\s*true')
  $okRegex2  = [regex]::IsMatch($raw, '\\"ok\\"\s*:\s*true')
  $ridRegex1 = [regex]::IsMatch($raw, '"run_id"\s*:\s*"[^"]+"')
  $ridRegex2 = [regex]::IsMatch($raw, '\\"run_id\\"\s*:\s*\\"[^\\"]+\\"')

  if ((-not ($okRegex1 -or $okRegex2)) -or (-not ($ridRegex1 -or $ridRegex2))) {
    Fail ("PRECHECK_FAIL: /agent/step non-ok payload: " + $raw)
  }
}
finally {
  try { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue } catch {}
  Start-Sleep -Milliseconds 250
}

Write-Host "RUNNING:" $cmd
Invoke-Expression $cmd
if ($LASTEXITCODE -ne 0) { throw "pytest failed." }

Write-Host "P14_GUARD_PASS"
exit 0
