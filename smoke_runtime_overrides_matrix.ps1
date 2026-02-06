param(
  [string]$Base = "http://127.0.0.1:8001"
)
$ErrorActionPreference = "Stop"

function Run-Case {
  param(
    [hashtable]$Case,
    [string]$Tag
  )

  $body = @{
    modes = @("WRITE","CRITIC","EDIT","QUALITY")
    payload = @{
      book_id = "book_runtime_test"
      topic = "Krótki akapit o wpływie AI na pracę programistów"
      min_words = 100
      runtime_overrides = @{
        WRITE   = @{ model = $Case.WRITE }
        CRITIC  = @{ model = $Case.CRITIC }
        EDIT    = @{ model = $Case.EDIT }
        QUALITY = @{ model = $Case.QUALITY }
      }
    }
  } | ConvertTo-Json -Depth 30

  $r = Invoke-RestMethod -Method Post `
    -Uri "$Base/agent/step" `
    -ContentType "application/json" `
    -Body $body `
    -TimeoutSec 240

  if (-not $r.ok) { throw "CASE_FAIL[$Tag]: API returned ok=false" }

  $rid = $r.run_id
  if (-not $rid) { throw "CASE_FAIL[$Tag]: missing run_id" }

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

  $map = @{}
  foreach ($row in $rows) {
    if ($row.mode -and $row.effective_model) {
      $map[$row.mode] = $row.effective_model
    }
  }

  foreach ($m in @("WRITE","CRITIC","EDIT","QUALITY")) {
    if ($map[$m] -ne $Case[$m]) {
      throw "CASE_FAIL[$Tag]: $m expected=$($Case[$m]) got=$($map[$m])"
    }
  }

  "CASE_PASS[$Tag] run_id=$rid"
}

# 3 zestawy regresyjne (tanie modele testowe)
$cases = @(
  @{ TAG="A"; WRITE="gpt-5-mini";  CRITIC="gpt-4.1-mini"; EDIT="gpt-5-nano";  QUALITY="gpt-4.1-nano" },
  @{ TAG="B"; WRITE="gpt-4.1-nano";CRITIC="gpt-5-mini";   EDIT="gpt-5-nano";  QUALITY="gpt-4.1-mini" },
  @{ TAG="C"; WRITE="gpt-5-nano";  CRITIC="gpt-4.1-mini"; EDIT="gpt-4.1-nano";QUALITY="gpt-5-mini" }
)

foreach ($c in $cases) {
  Run-Case -Case $c -Tag $c.TAG
}

"REGRESSION_PASS runtime_overrides_matrix"
