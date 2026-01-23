param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [string]$Book    = "gate_test",
  [switch]$Clean
)

$ErrorActionPreference = "Stop"

function Clean-Book([string]$BookId) {
  $root  = "C:\AI\ai_orchestrator_scaffold"
  $trash = Join-Path $root ("_TRASH_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
  New-Item -ItemType Directory -Force $trash | Out-Null
  Move-Item "$root\books\$BookId" $trash -ErrorAction SilentlyContinue
}

if ($Clean) { Clean-Book $Book }

Write-Host "[1] STEP"
$step = Invoke-RestMethod "$BaseUrl/books/agent/step" -Method POST -ContentType "application/json" `
        -Body (@{ book=$Book; mode="buffer" } | ConvertTo-Json)
Write-Host "    job_id=$($step.job_id)"

Write-Host "[2] WORKER/ONCE"
$w = Invoke-RestMethod "$BaseUrl/books/agent/worker/once" -Method POST -ContentType "application/json" `
     -Body (@{ book=$Book; job_id=$step.job_id } | ConvertTo-Json)
Write-Host "    worker.status=$($w.status)"

Write-Host "[3] ACCEPT require_fact_ok=true (BEZ fact_check) — tu bramka ma zadziałać"
$acc1 = Invoke-RestMethod "$BaseUrl/books/agent/accept" -Method POST -ContentType "application/json" `
        -Body (@{ book=$Book; clear_buffer=$false; require_fact_ok=$true } | ConvertTo-Json)
Write-Host "    accept1.status=$($acc1.status) added_chars=$($acc1.added_chars) error=$($acc1.error)"

Write-Host "[4] FACT_CHECK (buffer)"
$fact = Invoke-RestMethod "$BaseUrl/books/agent/fact_check" -Method POST -ContentType "application/json" `
        -Body (@{ book=$Book; source="buffer" } | ConvertTo-Json)
Write-Host "    fact.ok=$($fact.ok) issues=$($fact.issues.Count)"

Write-Host "[5] ACCEPT require_fact_ok=true (PO fact_check) — powinno przejść jeśli fact.ok=true"
$acc2 = Invoke-RestMethod "$BaseUrl/books/agent/accept" -Method POST -ContentType "application/json" `
        -Body (@{ book=$Book; clear_buffer=$true; require_fact_ok=$true } | ConvertTo-Json)
Write-Host "    accept2.status=$($acc2.status) added_chars=$($acc2.added_chars) error=$($acc2.error)"

Write-Host "[6] TAIL master.txt"
$master = "C:\AI\ai_orchestrator_scaffold\books\$Book\draft\master.txt"
if (Test-Path $master) { Get-Content $master -Tail 20 } else { Write-Host "    master.txt not found" }

