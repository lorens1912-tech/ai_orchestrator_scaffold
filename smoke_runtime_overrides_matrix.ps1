param(
  [string]$Base = "http://127.0.0.1:8001",
  [int]$TimeoutSec = 240,
  [int]$MaxRetry = 3
)

$ErrorActionPreference = "Stop"

function Invoke-StepWithRetry {
  param([string]$BodyJson)

  for ($i = 1; $i -le $MaxRetry; $i++) {
    try {
      return Invoke-RestMethod -Method Post `
        -Uri "$Base/agent/step" `
        -ContentType "application/json" `
        -Body $BodyJson `
        -TimeoutSec $TimeoutSec
    }
    catch {
      $msg = $_.ErrorDetails.Message
      if (-not $msg) { $msg = $_.Exception.Message }

      $isTransient = (
        ($msg -match "WRITE.*pusty tekst") -or
        ($msg -match "Internal Server Error") -or
        ($msg -match "timeout")
      )

      if ($isTransient -and $i -lt $MaxRetry) {
        Start-Sleep -Seconds (2 * $i)
        continue
      }

      throw
    }
  }
}

function New-ReqBody {
  param(
    [string]$WriteModel,
    [string]$CriticModel,
    [string]$EditModel,
    [string]$QualityModel,
    [string]$Topic
  )

  return (@{
    modes = @("WRITE","CRITIC","EDIT","QUALITY")
    payload = @{
      book_id = "book_runtime_test"
      topic = $Topic
      min_words = 120
      runtime_overrides = @{
        WRITE   = @{ model = $WriteModel }
        CRITIC  = @{ model = $CriticModel }
        EDIT    = @{ model = $EditModel }
        QUALITY = @{ model = $QualityModel }
      }
    }
  } | ConvertTo-Json -Depth 30)
}

function Resolve-RunId {
  param($Response)

  if ($null -ne $Response -and $Response.run_id) { return $Response.run_id }

  if ($Response -is [string]) {
    # np. "...\runs\run_...\steps\001_WRITE.json"
    $stepsDir = Split-Path $Response -Parent
    $runDir = Split-Path $stepsDir -Parent
    return Split-Path $runDir -Leaf
  }

  return (Get-ChildItem .\runs -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1).Name
}

function Assert-Models {
  param(
    [string]$RunId,
    [hashtable]$Expected
  )

  $rows = Get-ChildItem ".\runs\$RunId\steps" -Filter "*.json" |
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

  foreach ($k in $Expected.Keys) {
    $actual = ($rows | Where-Object { $_.mode -eq $k } | Select-Object -First 1).effective_model
    $exp = $Expected[$k]
    if ($actual -ne $exp) {
      throw "ASSERT_FAIL mode=$k expected=$exp actual=$actual run_id=$RunId"
    }
    Write-Host "OK: $k -> $actual"
  }
}

$cases = @(
  @{
    id="A"
    write="gpt-5-mini"
    critic="gpt-4.1-mini"
    edit="gpt-5-nano"
    quality="gpt-4.1-nano"
    topic="Case A: AI a praca programistów"
  },
  @{
    id="B"
    write="gpt-4.1-nano"
    critic="gpt-5-mini"
    edit="gpt-5-nano"
    quality="gpt-4.1-mini"
    topic="Case B: AI a praca programistów"
  },
  @{
    id="C"
    write="gpt-5-mini"
    critic="gpt-4.1-nano"
    edit="gpt-5-nano"
    quality="gpt-4.1-mini"
    topic="Case C: AI a praca programistów"
  }
)

foreach ($c in $cases) {
  $body = New-ReqBody `
    -WriteModel $c.write `
    -CriticModel $c.critic `
    -EditModel $c.edit `
    -QualityModel $c.quality `
    -Topic $c.topic

  $r = Invoke-StepWithRetry -BodyJson $body
  $rid = Resolve-RunId -Response $r

  Assert-Models -RunId $rid -Expected @{
    "WRITE"   = $c.write
    "CRITIC"  = $c.critic
    "EDIT"    = $c.edit
    "QUALITY" = $c.quality
  }

  Write-Host "CASE_PASS[$($c.id)] run_id=$rid"
}

Write-Host "MATRIX_PASS runtime_overrides"
