
# run_book.ps1 (LEGACY)
# Ten plik jest tylko wrapperem: forwards EVERYTHING do run_book_v2.ps1,
# żeby nigdy nie wróciły błędy switch/bool typu "-OpenQC 0".

[CmdletBinding(PositionalBinding=$false)]
param(
  [Parameter(ValueFromRemainingArguments=$true)]
  [object[]] $Args
)

$ErrorActionPreference = "Stop"

$target = Join-Path $PSScriptRoot "run_book_v2.ps1"
if (-not (Test-Path -LiteralPath $target)) {
  throw "Missing target script: $target"
}

& $target @Args
exit $LASTEXITCODE
