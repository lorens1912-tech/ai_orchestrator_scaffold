Set-Location C:\AI\ai_orchestrator_scaffold
$ErrorActionPreference = "Stop"

# SANITY BRANCH
$branch = (git branch --show-current).Trim()
if ($branch -ne "p26-pro-writer-runtime") { throw "UNEXPECTED_BRANCH: $branch (expected p26-pro-writer-runtime)" }
Write-Host "P26_RECOVER_SANITY_OK branch=$branch"

# CLEAN JUNK THAT OFTEN POISONS WRAPPER
Get-ChildItem .\app\main.py.bak_* -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
Remove-Item -Force -ErrorAction SilentlyContinue .\scripts\p25_verify_truth_once.ps1

# 1) REWRITE response_contract.py (PURE PYTHON ONLY)
@'
from __future__ import annotations
from typing import Any, Dict, Optional

ALLOWED_STATUS = {"ok", "error"}

def build_response(
    status: str = "ok",
    data: Optional[Dict[str, Any]] = None,
    errors: Optional[Any] = None
) -> Dict[str, Any]:
    if data is None:
        data = {}
    if errors is None:
        errors = []
    elif not isinstance(errors, list):
        errors = [errors]
    return {"status": status, "data": data, "errors": errors}

def validate_response(resp: Any) -> bool:
    if not isinstance(resp, dict):
        return False
    if set(resp.keys()) != {"status", "data", "errors"}:
        return False
    if resp["status"] not in ALLOWED_STATUS:
        return False
    if not isinstance(resp["data"], dict):
        return False
    if not isinstance(resp["errors"], list):
        return False
    return True
