Set-Location -LiteralPath $PSScriptRoot
$env:AGENT_TEST_MODE = "0"
Remove-Item Env:PYTEST_FASTPATH -ErrorAction SilentlyContinue
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001