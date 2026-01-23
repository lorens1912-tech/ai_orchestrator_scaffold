$ErrorActionPreference = "Stop"
$ProjectRoot = "C:\AI\ai_orchestrator_scaffold"
Set-Location $ProjectRoot

Write-Host "[GATE] cwd = $((Get-Location).Path)"

Write-Host "[GATE] py_compile tests..."
Get-ChildItem -Path .\tests -Filter "test_0*.py" | ForEach-Object {
  python -m py_compile $_.FullName
}

Write-Host "[GATE] /health..."
try {
  Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 3 | Out-Null
} catch {
  Write-Host "[GATE] FAIL: server not reachable on http://127.0.0.1:8000"
  exit 2
}

Write-Host "[GATE] unittest discover..."
python -m unittest discover -s tests -p "test_0*.py" -v
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[GATE] PASS"
