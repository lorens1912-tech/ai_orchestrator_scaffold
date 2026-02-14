param(
  [string]$base="http://127.0.0.1:8001",
  [string]$writeModel="gpt-5-mini",
  [string]$criticModel="gpt-4.1-mini",
  [string]$editModel="gpt-5-nano",
  [string]$qualityModel="gpt-4.1-nano"
)

Set-Location "C:\AI\ai_orchestrator_scaffold"

$body = @{
  modes = @("WRITE","CRITIC","EDIT","QUALITY")
  payload = @{
    book_id = "book_runtime_test"
    topic = "Krótki akapit o wpływie AI na pracę programistów"
    min_words = 120
    runtime_overrides = @{
      WRITE   = @{ model = $writeModel }
      CRITIC  = @{ model = $criticModel }
      EDIT    = @{ model = $editModel }
      QUALITY = @{ model = $qualityModel }
    }
  }
} | ConvertTo-Json -Depth 40

$r = Invoke-RestMethod -Method Post `
  -Uri "$base/agent/step" `
  -ContentType "application/json" `
  -Body $body `
  -TimeoutSec 240

if (-not $r.ok) { throw "API zwróciło ok=false" }
$rid = $r.run_id
"RUN_ID=$rid"

$expected = @{
  WRITE   = $writeModel
  CRITIC  = $criticModel
  EDIT    = $editModel
  QUALITY = $qualityModel
}

$rows = Get-ChildItem ".\runs\$rid\steps" -Filter "*.json" |
  Sort-Object Name |
  ForEach-Object {
    $j = Get-Content $_.FullName -Raw | ConvertFrom-Json
    [pscustomobject]@{
      file = $_.Name
      mode = $j.mode
      effective_model = $j.effective_model_id
    }
  }

$rows | Format-Table -AutoSize

$ok = $true
foreach($mode in $expected.Keys){
  $row = $rows | Where-Object { $_.mode -eq $mode } | Select-Object -Last 1
  if(-not $row){
    Write-Host "MISSING: $mode" -ForegroundColor Red
    $ok = $false
    continue
  }
  if($row.effective_model -ne $expected[$mode]){
    Write-Host "MISMATCH: $mode expected=$($expected[$mode]) got=$($row.effective_model)" -ForegroundColor Red
    $ok = $false
  } else {
    Write-Host "OK: $mode -> $($row.effective_model)" -ForegroundColor Green
  }
}

if(-not $ok){ throw "SMOKE_FAIL runtime_overrides" }
Write-Host "SMOKE_PASS runtime_overrides"
