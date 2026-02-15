
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

function Test-Health {
    try {
        $h = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8001/health" -TimeoutSec 2
        return $true
    } catch {
        return $false
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot
New-Item -ItemType Directory -Force -Path ".\reports" | Out-Null

$serverOwned = $false
$serverProc = $null
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$serverOut = ".\reports\P042_UVICORN_OUT_$ts.log"
$serverErr = ".\reports\P042_UVICORN_ERR_$ts.log"

try {
    Write-Host "P042_STRICT_GATE_START $(Get-Date -Format o)"

    if (-not (Test-Health)) {
        Write-Host "Starting local uvicorn for strict gate on 127.0.0.1:8001 ..."
        $serverProc = Start-Process `
            -FilePath "python" `
            -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8001") `
            -PassThru `
            -RedirectStandardOutput $serverOut `
            -RedirectStandardError $serverErr
        $serverOwned = $true

        $up = $false
        for ($i = 0; $i -lt 80; $i++) {
            Start-Sleep -Milliseconds 250
            if (Test-Health) {
                $up = $true
                break
            }
            if ($serverProc.HasExited) {
                break
            }
        }

        if (-not $up) {
            throw "Uvicorn did not become healthy on 127.0.0.1:8001. Check logs: $serverOut / $serverErr"
        }
    } else {
        Write-Host "Health endpoint already available. Reusing existing server."
    }

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
            pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\p14_continuous_release_guard.ps1
        }
    }

    Write-Host "P042_STRICT_GATE_PASS $(Get-Date -Format o)"
    exit 0
}
finally {
    if ($serverOwned -and $serverProc -and -not $serverProc.HasExited) {
        Stop-Process -Id $serverProc.Id -Force
        Write-Host "Stopped owned uvicorn process PID=$($serverProc.Id)"
    }
}
