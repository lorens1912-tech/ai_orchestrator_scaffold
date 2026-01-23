param(
  [string]$BookPrefix = "test_book_parallel",
  [int]$N = 7
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$jobs = @()
for ($i=1; $i -le $N; $i++) {
  $bid = "{0}_{1}" -f $BookPrefix, $i
  $jobs += Start-Job -ScriptBlock {
    param($b)
    pwsh -NoProfile -ExecutionPolicy Bypass -File .\client_smoke.ps1 -BookId $b -TimeoutPost 900 -Mode buffer
  } -ArgumentList $bid
}

$jobs | Wait-Job | Out-Null
$jobs | Receive-Job
$jobs | Remove-Job
