param()
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:AGENT_TEST_MODE = "0"
Remove-Item Env:PYTEST_FASTPATH -ErrorAction SilentlyContinue
Remove-Item Env:WRITE_MODEL_FORCE -ErrorAction SilentlyContinue
Write-Host "SERVER_PROFILE=FULL_REAL (AGENT_TEST_MODE=0, PYTEST_FASTPATH=OFF)"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000