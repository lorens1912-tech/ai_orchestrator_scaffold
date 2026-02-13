$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repo = (git rev-parse --show-toplevel).Trim()
if (-not $repo) {
  Write-Host "P14_GUARD_FAIL: cannot resolve repo root"
  exit 1
}

$guard = Join-Path $repo "scripts\p14_release_guard.ps1"
if (-not (Test-Path $guard)) {
  Write-Host "P14 guard missing: $guard"
  exit 1
}

& $guard
$rc = $LASTEXITCODE
if ($null -eq $rc) { $rc = 0 }
exit $rc
