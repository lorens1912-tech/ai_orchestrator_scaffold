
# monitor_jobs.ps1
# Monitoruje status per-book na podstawie plików:
# - books\<book>\_active.lock
# - books\<book>\jobs\*.json (najnowszy)

$base = "C:\AI\ai_orchestrator_scaffold"
$api  = "http://127.0.0.1:8000"

# ustaw listę książek tutaj
$books = @("test1","test2","test3","test4","test5")

function Read-Utf8Json([string]$path) {
  if (!(Test-Path -LiteralPath $path)) { return $null }
  try {
    return (Get-Content -Raw -Encoding UTF8 -LiteralPath $path | ConvertFrom-Json)
  } catch { return $null }
}

function Get-LockInfo([string]$book) {
  $lock = Join-Path $base ("books\{0}\_active.lock" -f $book)
  if (!(Test-Path -LiteralPath $lock)) { return @{ locked=$false; job_id=$null; path=$lock } }
  $jid = (Get-Content -Raw -Encoding UTF8 -LiteralPath $lock).Trim()
  return @{ locked=$true; job_id=$jid; path=$lock }
}

function Get-LatestJobLocal([string]$book) {
  $jobsDir = Join-Path $base ("books\{0}\jobs" -f $book)
  if (!(Test-Path -LiteralPath $jobsDir)) { return $null }
  $f = Get-ChildItem -LiteralPath $jobsDir -Filter "*.json" -File -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (!$f) { return $null }
  $j = Read-Utf8Json $f.FullName
  if (!$j) { return $null }
  $j | Add-Member -NotePropertyName "_file" -NotePropertyValue $f.FullName -Force
  return $j
}

function Get-JobStatusApi([string]$jobId) {
  try {
    return (Invoke-RestMethod -Uri "$api/books/jobs/$jobId" -Method Get -ErrorAction Stop)
  } catch { return $null }
}

while ($true) {
  Clear-Host
  $ts = Get-Date -Format "HH:mm:ss"
  "=== JOB MONITOR $ts ==="
  ""

  foreach ($b in $books) {
    $lock = Get-LockInfo $b
    $latest = Get-LatestJobLocal $b

    $jid = $null
    $st  = "NO_JOB"
    $rc  = $null

    if ($latest) {
      $jid = $latest.job_id
      $st  = $latest.status
      $rc  = $latest.ps_rc
      # opcjonalnie odśwież status z API (jeśli serwer działa)
      $apiJob = if ($jid) { Get-JobStatusApi $jid } else { $null }
      if ($apiJob -and $apiJob.status) { $st = $apiJob.status }
    }

    $lockTxt = if ($lock.locked) { "LOCK=YES($($lock.job_id))" } else { "LOCK=NO" }
    "{0,-10} | {1,-28} | JOB={2,-14} | ST={3,-8} | RC={4}" -f $b, $lockTxt, ($jid ?? "-"), $st, ($rc ?? "-")
  }

  Start-Sleep -Seconds 3
}
