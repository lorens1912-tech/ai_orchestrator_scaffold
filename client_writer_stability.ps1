param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [string]$Book    = "writer_test",
  [int]   $Runs    = 3,
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

function Run-Once([string]$BookId) {
  $step = Invoke-RestMethod "$BaseUrl/books/agent/step" -Method POST -ContentType "application/json" `
          -Body (@{ book=$BookId; mode="buffer" } | ConvertTo-Json)

  Invoke-RestMethod "$BaseUrl/books/agent/worker/once" -Method POST -ContentType "application/json" `
          -Body (@{ book=$BookId; job_id=$step.job_id } | ConvertTo-Json) | Out-Null

  $acc = Invoke-RestMethod "$BaseUrl/books/agent/accept" -Method POST -ContentType "application/json" `
          -Body (@{ book=$BookId; clear_buffer=$true; require_fact_ok=$false } | ConvertTo-Json)

  if ($null -eq $acc.added_chars) { return 0 }
  return [int]$acc.added_chars
}

$vals = @()
1..$Runs | ForEach-Object {
  $v = Run-Once $Book
  $vals += $v
  Write-Host "[RUN $_] added_chars=$v"
}

$nz = $vals | Where-Object { $_ -gt 0 }
if (-not $nz) {
  Write-Host "[FAIL] accept.added_chars=0 (nic nie dopięto)" -ForegroundColor Red
  exit 1
}

$avg = ($nz | Measure-Object -Average).Average
$THRESH = 20

foreach ($v in $nz) {
  if ([math]::Abs($v - $avg) / $avg * 100 -gt $THRESH) {
    Write-Host "[FAIL] variance >$THRESH% vals=$($nz -join ', ')" -ForegroundColor Red
    exit 1
  }
}

Write-Host "[OK] writer stable vals=$($nz -join ', ') avg=$([int]$avg)" -ForegroundColor Green
