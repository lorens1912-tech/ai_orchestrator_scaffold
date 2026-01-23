
param(
  [Parameter(Mandatory=$true)][string]$Book,
  [string]$Delta = "3000",
  [string]$Model = "gpt-4.1-mini",
  [int]$MaxOutputTokens = 1200,
  [Parameter(Mandatory=$true)][string]$PromptFile,
  [string]$OpenQC = "False",
  [string]$OpenCurrent = "False"
)

function To-Bool([string]$s) {
  if ($null -eq $s) { return $false }
  $t = $s.Trim()
  return ($t -match '^(?i:true|1|yes|y)$')
}

$Root = Split-Path -Parent $PSCommandPath

$OpenQC_bool      = To-Bool $OpenQC
$OpenCurrent_bool = To-Bool $OpenCurrent

# 1) odpal właściwy impl
$impl = Join-Path $Root "run_book_v2_impl.ps1"
if (!(Test-Path $impl)) { throw "Brak: $impl" }

$params = @{
  Book = $Book
  Delta = $Delta
  Model = $Model
  MaxOutputTokens = $MaxOutputTokens
  PromptFile = $PromptFile
  OpenQC = $OpenQC_bool
  OpenCurrent = $OpenCurrent_bool
}

& $impl @params
$rc = $LASTEXITCODE
if ($rc -ne 0) { exit $rc }

# 2) finalize (runs/<job_id> + master) — bierz najnowszy job z books\<book>\jobs
$finalize = Join-Path $Root "tools\finalize_run.ps1"
if (!(Test-Path $finalize)) { throw "Brak: $finalize" }

$jobsDir = Join-Path $Root "books\$Book\jobs"
if (!(Test-Path $jobsDir)) {
  Write-Host "WARN: brak jobsDir: $jobsDir (finalize skipped)"
  exit 0
}

$jobFile = Get-ChildItem $jobsDir -Filter "*.json" -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (!$jobFile) {
  Write-Host "WARN: brak job json w $jobsDir (finalize skipped)"
  exit 0
}

$jobId = [IO.Path]::GetFileNameWithoutExtension($jobFile.Name)
powershell -ExecutionPolicy Bypass -File $finalize -Root $Root -Book $Book -Job $jobId
exit $LASTEXITCODE
