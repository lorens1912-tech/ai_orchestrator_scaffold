$ErrorActionPreference = "Stop"

$f = "C:\AI\ai_orchestrator_scaffold\run_book_v2_impl.ps1"
$t = $null
$e = $null

[System.Management.Automation.Language.Parser]::ParseFile($f, [ref]$t, [ref]$e) | Out-Null

if ($e.Count -gt 0) {
  $e | Format-List | Out-String
  exit 1
}

"PARSE_OK"
exit 0
