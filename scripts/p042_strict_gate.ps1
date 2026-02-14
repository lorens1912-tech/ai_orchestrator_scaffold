[CmdletBinding()]
param(
    [switch]$SkipP14
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Command
    )

    Write-Host ""
    Write-Host "==> $Name"
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "FAILED: $Name (exit=$LASTEXITCODE)"
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

New-Item -ItemType Directory -Force -Path ".\reports" | Out-Null

Write-Host "P042_STRICT_GATE_START $(Get-Date -Format o)"

Invoke-Step -Name "python -m py_compile .\app\main.py" -Command {
    python -m py_compile .\app\main.py
}

Invoke-Step -Name "python -m py_compile .\app\orchestrator_stub.py" -Command {
    python -m py_compile .\app\orchestrator_stub.py
}

Invoke-Step -Name "python -m pytest -q .\tests\test_090_runtime_memory_adapter.py .\tests\test_091_agent_step_team_runtime_applied.py -x" -Command {
    python -m pytest -q .\tests\test_090_runtime_memory_adapter.py .\tests\test_091_agent_step_team_runtime_applied.py -x
}

if (-not $SkipP14) {
    Invoke-Step -Name ".\scripts\p14_continuous_release_guard.ps1" -Command {
        .\scripts\p14_continuous_release_guard.ps1
    }
}

Write-Host "P042_STRICT_GATE_PASS $(Get-Date -Format o)"
exit 0
