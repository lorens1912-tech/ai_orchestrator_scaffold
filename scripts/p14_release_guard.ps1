param()
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repo

python -m py_compile .\app\main.py
if (Test-Path .\app\compat_runtime.py) { python -m py_compile .\app\compat_runtime.py }
if (Test-Path .\app\response_contract.py) { python -m py_compile .\app\response_contract.py }

Write-Host "P14_GUARD_PASS"
exit 0
