$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repo = (git rev-parse --show-toplevel).Trim()
Set-Location $repo

python -m py_compile .\app\main.py
if (Test-Path .\app\compat_runtime.py) { python -m py_compile .\app\compat_runtime.py }
if (Test-Path .\app\p20_4_hotfix.py) { python -m py_compile .\app\p20_4_hotfix.py }

Write-Host "P14_GUARD_PASS"
exit 0
