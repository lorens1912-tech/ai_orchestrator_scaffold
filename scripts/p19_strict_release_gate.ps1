param()

Set-Location C:\AI\ai_orchestrator_scaffold
$ErrorActionPreference = "Stop"

Write-Host "P19: STRICT RELEASE GATE"

# 0) Local cleanup (żeby nie fałszować guarda)
Remove-Item .\runs\_tmp\uniqueness_registry.jsonl -Force -ErrorAction SilentlyContinue
if (Test-Path .\runs\_tmp) {
  if (-not (Get-ChildItem .\runs\_tmp -Force -ErrorAction SilentlyContinue)) {
    Remove-Item .\runs\_tmp -Force -ErrorAction SilentlyContinue
  }
}

# 1) Regresja kontraktów jakości (musi być zielona)
python -m pytest -q `
  .\tests\test_p16_quality_contract.py `
  .\tests\test_p17_pipeline_quality_contract.py `
  .\tests\test_p18_preset_matrix_quality_contract.py
if ($LASTEXITCODE -ne 0) {
  throw "P19_GATE_FAIL: P16/P17/P18 failed"
}

# 2) Dotychczasowy guard (szybki smoke + czystość drzewa)
try {
  .\scripts\p14_continuous_release_guard.ps1
} catch {
  throw ("P19_GATE_FAIL: P14 guard failed: " + $_.Exception.Message)
}

# 3) Dodatkowy check czystości (bez raportów/handoff)
$dirty = git status --porcelain
if ($dirty) {
  $filtered = @()
  foreach ($line in $dirty) {
    if ($line -match "reports[\\/].*") { continue }
    $filtered += $line
  }
  if ($filtered.Count -gt 0) {
    throw ("P19_GATE_FAIL: dirty tree`n" + ($filtered -join "`n"))
  }
}

Write-Host "P19_STRICT_RELEASE_GATE_PASS"
