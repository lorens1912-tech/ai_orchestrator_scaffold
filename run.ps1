
# run.ps1 — deterministic wrapper: always prefer PS7 (pwsh.exe) by absolute path.
# It forwards ALL arguments to pwsh.exe (including -Command, -File, etc).
# If PS7 missing, it falls back to Windows PowerShell 5.1.

[CmdletBinding(PositionalBinding=$false)]
param(
    [Parameter(ValueFromRemainingArguments=$true)]
    [object[]] $Args
)

$ErrorActionPreference = "Stop"

$pwsh = "C:\Program Files\PowerShell\7\pwsh.exe"

if (Test-Path -LiteralPath $pwsh) {
    & $pwsh @Args
    exit $LASTEXITCODE
}

& "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" @Args
exit $LASTEXITCODE
