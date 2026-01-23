# client_parallel.ps1
param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [int]   $Books   = 5      # 5-7 zgodnie ze specyfikacją
)

$ErrorActionPreference = "Stop"
Import-Module ThreadJob

function Step-Flow([string]$BookId) {
  $BodyBase = @{ book = $BookId; mode = "buffer" }

  $step = Invoke-RestMethod "$using:BaseUrl/books/agent/step" -Method POST `
          -ContentType "application/json" -Body ($BodyBase | ConvertTo-Json)
  Invoke-RestMethod "$using:BaseUrl/books/agent/worker/once" -Method POST `
          -ContentType "application/json" -Body (@{ book=$BookId; job_id=$step.job_id }|ConvertTo-Json)
  try {
    Invoke-RestMethod "$using:BaseUrl/books/agent/fact_check" -Method POST `
          -ContentType "application/json" -Body (@{ book=$BookId; source="buffer"}|ConvertTo-Json) | Out-Null
  } catch { }
  Invoke-RestMethod "$using:BaseUrl/books/agent/accept" -Method POST `
          -ContentType "application/json" -Body (@{ book=$BookId; clear_buffer=$true; require_fact_ok=$false }|ConvertTo-Json)
  return $BookId
}

$jobs = 1..$Books | ForEach-Object {
  Start-ThreadJob -ScriptBlock ${function:Step-Flow} -ArgumentList ("book_parallel_$($_)")
}

Receive-Job -Job $jobs -Wait | ForEach-Object { Write-Host "[OK] $_ finished" -ForegroundColor Green }
