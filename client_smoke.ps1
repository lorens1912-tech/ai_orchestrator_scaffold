
# client_smoke.ps1
param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [string]$Book = "test_book_smoke",
  [string]$Mode = "buffer"
)

$ErrorActionPreference = "Stop"

function Ok($m){ Write-Host "[OK] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Fail($m){ Write-Host "[FAIL] $m" -ForegroundColor Red; throw $m }

try {

  Invoke-RestMethod "$BaseUrl/openapi.json" -TimeoutSec 3 | Out-Null
  Ok "Server reachable"

  # STEP
  $step = Invoke-RestMethod "$BaseUrl/books/agent/step" -Method POST `
    -ContentType "application/json" `
    -Body (@{
      book = $Book
      mode = $Mode
    } | ConvertTo-Json)

  Ok "STEP job_id=$($step.job_id)"

  # WORKER
  Invoke-RestMethod "$BaseUrl/books/agent/worker/once" -Method POST `
    -ContentType "application/json" `
    -Body (@{
      book = $Book
      job_id = $step.job_id
    } | ConvertTo-Json) | Out-Null

  Ok "WORKER done"

  # FACT CHECK
  try {
    $fact = Invoke-RestMethod "$BaseUrl/books/agent/fact_check" -Method POST `
      -ContentType "application/json" `
      -Body (@{
        book = $Book
        source = $Mode
      } | ConvertTo-Json)

    if ($fact.ok) { Ok "FACT_CHECK ok" }
    else { Warn "FACT_CHECK issues=$($fact.issues.Count)" }
  }
  catch {
    Warn "FACT_CHECK skipped (no text)"
  }

  # ACCEPT
  Invoke-RestMethod "$BaseUrl/books/agent/accept" -Method POST `
    -ContentType "application/json" `
    -Body (@{
      book = $Book
      clear_buffer = $true
      require_fact_ok = $false
    } | ConvertTo-Json) | Out-Null

  Ok "ACCEPT done"

  # TAIL master
  $master = "C:\AI\ai_orchestrator_scaffold\books\$Book\draft\master.txt"
  if (Test-Path $master) {
    Ok "TAIL master.txt"
    Get-Content $master -Tail 30
  } else {
    Warn "master.txt not found"
  }

  Ok "SMOKE TEST FINISHED"
}
catch {
  Write-Host "[ERROR] $_" -ForegroundColor Red
}
finally {
  Read-Host "ENTER = zamknij okno"
}
