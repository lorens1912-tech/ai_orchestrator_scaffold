param()
$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:AGENT_TEST_MODE = "0"
$env:PYTEST_FASTPATH = "1"
Remove-Item Env:WRITE_MODEL_FORCE -ErrorAction SilentlyContinue
Write-Host "SERVER_PROFILE=STRICT_FASTPATH (AGENT_TEST_MODE=0, PYTEST_FASTPATH=1)"
uvicorn app.main:app --host 127.0.0.1 --port 8000
