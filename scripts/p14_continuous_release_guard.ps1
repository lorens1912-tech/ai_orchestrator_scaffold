param(
  [switch]$Full
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

Write-Host "P14: Continuous Release Guard"

# 1) Guard na czyste drzewo
$dirty = git status --porcelain
if ($dirty) {
  Write-Host "BLOCKED_DIRTY_TREE:"
  $dirty | ForEach-Object { Write-Host $_ }
  throw "Working tree must be clean before release/push."
}

function Stop-Server8000 {
  $conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($conn) {
    try { Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue } catch {}
    Start-Sleep -Milliseconds 500
  }
}

function Start-ServerWithProfile {
  param(
    [bool]$UseFastpath,
    [string]$AgentTestMode
  )

  Stop-Server8000

  if ($UseFastpath) { $env:PYTEST_FASTPATH = "1" }
  else { Remove-Item Env:PYTEST_FASTPATH -ErrorAction SilentlyContinue }

  $env:AGENT_TEST_MODE = $AgentTestMode
  Remove-Item Env:WRITE_MODEL_FORCE -ErrorAction SilentlyContinue

  $cmd = "Set-Location '$RepoRoot'; python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
  $p = Start-Process -FilePath "powershell" `
    -ArgumentList "-NoProfile","-ExecutionPolicy","Bypass","-Command",$cmd `
    -PassThru -WindowStyle Hidden

  $deadline = (Get-Date).AddSeconds(45)
  while ((Get-Date) -lt $deadline) {
    try {
      $null = Invoke-RestMethod -Uri "http://127.0.0.1:8000/config/validate" -TimeoutSec 2
      return $p
    } catch {
      Start-Sleep -Milliseconds 300
    }
  }

  try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {}
  throw "Server did not become healthy on :8000"
}

if ($Full) {
  # P15+: bez fastpath, ale TEST MODE ON (deterministycznie, bez timeoutów live provider)
  $modeName = "FULL_NO_FASTPATH_TESTMODE"
  $pytestCmd = "python -m pytest -q -x --maxfail=1"
  $server = Start-ServerWithProfile -UseFastpath:$false -AgentTestMode "1"
}
else {
  # P14 strict pack
  $modeName = "STRICT_002_007_FASTPATH"
  $tests = @(
    "tests/test_002_config_contract.py"
    "tests/test_003_write_step.py"
    "tests/test_004_critic_step.py"
    "tests/test_005_edit_step.py"
    "tests/test_006_pipeline_smoke.py"
    "tests/test_007_artifact_schema.py"
  )
  $pytestCmd = "python -m pytest -q -x --maxfail=1 " + ($tests -join " ")
  $server = Start-ServerWithProfile -UseFastpath:$true -AgentTestMode "0"
}

try {
  Write-Host "MODE:" $modeName
  Write-Host "RUNNING:" $pytestCmd
  Invoke-Expression $pytestCmd
  if ($LASTEXITCODE -ne 0) { throw "pytest failed." }
  Write-Host "P14_GUARD_PASS"
  exit 0
}
finally {
  if ($server -and (Get-Process -Id $server.Id -ErrorAction SilentlyContinue)) {
    try { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue } catch {}
  }
}
