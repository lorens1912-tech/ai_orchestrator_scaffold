param()
$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:AGENT_TEST_MODE = "1"
Remove-Item Env:PYTEST_FASTPATH -ErrorAction SilentlyContinue
Remove-Item Env:WRITE_MODEL_FORCE -ErrorAction SilentlyContinue
Write-Host "SERVER_PROFILE=FULL_REAL (AGENT_TEST_MODE=1, PYTEST_FASTPATH=OFF)"
uvicorn app.main:app --host 127.0.0.1 --port 8000
