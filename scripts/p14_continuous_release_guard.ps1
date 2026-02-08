$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Write-Host "P14: Continuous Release Guard"

# 1) Repo sanity
$inside = git rev-parse --is-inside-work-tree 2>$null
if ($LASTEXITCODE -ne 0 -or "$inside".Trim() -ne "true") { throw "Not a git repo" }

$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -eq "HEAD") { throw "Detached HEAD is not allowed for release." }

# 2) Dirty tree policy (ignorujemy tylko handoff/reports)
$dirty = git status --porcelain
$blocked = @()
foreach ($l in $dirty) {
  if ($l -match 'HANDOFF_EXIT_' -or $l -match '^\?\?\s+reports\\' -or $l -match '^\s*[MADRCU]+\s+reports\\') { continue }
  $blocked += $l
}
if ($blocked.Count -gt 0) {
  Write-Host "BLOCKED_DIRTY_TREE:"
  $blocked | ForEach-Object { Write-Host $_ }
  throw "Working tree must be clean before release/push."
}

# 3) Strict tests (P13 script -> fallback pytest)
$strict = Get-ChildItem .\scripts -Filter "P13*STRICT*TEST*.ps1" -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

if (-not $strict) {
  $strict = Get-ChildItem .\scripts -Filter "*strict*test*.ps1" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
}

if ($strict) {
  Write-Host "RUN_STRICT_SCRIPT:" $strict.FullName
  & $strict.FullName
  if ($LASTEXITCODE -ne 0) { throw "Strict test script failed." }
}
else {
  Write-Host "RUN_PYTEST_FALLBACK"
  python -m pytest -q
  if ($LASTEXITCODE -ne 0) { throw "pytest failed." }
}

# 4) API smoke (opcjonalnie, jeśli serwer działa)
$apiOk = $false
foreach ($u in @("http://127.0.0.1:8000/health","http://127.0.0.1:8000/healthz","http://127.0.0.1:8000/docs")) {
  try {
    Invoke-WebRequest -Uri $u -TimeoutSec 8 | Out-Null
    Write-Host "API_SMOKE_OK:" $u
    $apiOk = $true
    break
  } catch {}
}
if (-not $apiOk) {
  Write-Host "API_SMOKE_SKIP: server not reachable on :8000 (allowed)."
}

Write-Host "P14_CONTINUOUS_RELEASE_GUARD=PASS"
