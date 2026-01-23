param(
  [Parameter(Mandatory=$true)][string]$Root,
  [Parameter(Mandatory=$true)][string]$Book,
  [Parameter(Mandatory=$true)][string]$Job
)

$bookDir  = Join-Path $Root "books\$Book"
$draftDir = Join-Path $bookDir "draft"
$runsDir  = Join-Path $bookDir "runs"
$runDir   = Join-Path $runsDir $Job

$curTxt   = Join-Path $bookDir "current.txt"
$lastJson = Join-Path $bookDir "last_openai_response.json"
$jobsDir  = Join-Path $bookDir "jobs"
$jobJson  = Join-Path $jobsDir "$Job.json"

$analysisDir = Join-Path $bookDir "analysis"
$critLatestJson = Join-Path $analysisDir "critic_report_latest.json"
$critLatestMd   = Join-Path $analysisDir "critic_report_latest.md"

$masterPath = Join-Path $draftDir "master.txt"
$marker = "--- RUN $Job ---"

New-Item -ItemType Directory -Force $draftDir, $runsDir, $runDir | Out-Null

# jeśli nie ma current -> NIE wywalaj się, tylko ostrzeż
if (!(Test-Path $curTxt)) {
  Write-Host "WARN: brak current.txt -> finalize pominięty (job nie wygenerował tekstu?)"
  exit 0
}

# copy artefakty
Copy-Item $curTxt (Join-Path $runDir "current.txt") -Force
if (Test-Path $lastJson) { Copy-Item $lastJson (Join-Path $runDir "last_openai_response.json") -Force }
if (Test-Path $jobJson)  { Copy-Item $jobJson  (Join-Path $runDir "job.json") -Force }

# QC (opcjonalnie)
if (Test-Path $critLatestJson) { Copy-Item $critLatestJson (Join-Path $runDir "critic_report_latest.json") -Force }
if (Test-Path $critLatestMd)   { Copy-Item $critLatestMd   (Join-Path $runDir "critic_report_latest.md")   -Force }

# meta.json (minimal)
@{
  run_id = $Job
  book   = $Book
  created_at = (Get-Date).ToString("s")
} | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 (Join-Path $runDir "meta.json")

# dopisz do master tylko raz
if (!(Test-Path $masterPath)) { "" | Set-Content -Encoding UTF8 $masterPath }
$masterRaw = Get-Content $masterPath -Raw
if ($masterRaw -notmatch [regex]::Escape($marker)) {
  Add-Content -Encoding UTF8 $masterPath "`n`n$marker`n"
  Add-Content -Encoding UTF8 $masterPath (Get-Content $curTxt -Raw)
  Write-Host "OK: dopisano do master"
} else {
  Write-Host "SKIP: master już ma marker tego RUN"
}

Write-Host "OK: run zapisany -> $runDir"
