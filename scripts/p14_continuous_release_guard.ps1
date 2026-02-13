param()
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$targets = @(
    (Join-Path $repo "scripts\p14_release_guard.ps1"),
    (Join-Path $repo "scripts\p14_release_guard_with_snapshot.ps1")
) | Where-Object { Test-Path $_ }

if ($targets.Count -lt 1) {
    Write-Host "P14 guard missing under $repo\scripts" -ForegroundColor Red
    exit 1
}

& pwsh -NoProfile -ExecutionPolicy Bypass -File $targets[0]
exit $LASTEXITCODE
