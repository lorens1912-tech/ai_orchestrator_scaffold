Set-Location C:\AI\ai_orchestrator_scaffold

Write-Host "=== START UVICORN (FOREGROUND, NO RELOAD) ==="

python -m uvicorn app.main:app `
  --host 127.0.0.1 `
  --port 8000 `
  --log-level info
