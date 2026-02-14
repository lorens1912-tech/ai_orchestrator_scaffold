Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Set-Location C:\AI\ai_orchestrator_scaffold

$ts    = Get-Date -Format "yyyyMMdd_HHmmss"
$start = Get-Date
$log   = ".\reports\P20_2_AUTO_RETRY_VERIFY_$ts.txt"

New-Item -ItemType Directory -Force -Path ".\reports\handoff\telemetry" | Out-Null
New-Item -ItemType Directory -Force -Path ".\reports\handoff" | Out-Null

function Get-LatestFreshFile([string]$pattern,[datetime]$from){
    $all = Get-ChildItem $pattern -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending
    if (-not $all) { return $null }
    $fresh = $all | Where-Object { $_.LastWriteTime -ge $from.AddSeconds(-5) } | Select-Object -First 1
    if ($fresh) { return $fresh }
    return ($all | Select-Object -First 1)
}

if (-not (Test-Path ".\scripts\p20_2_auto_retry_gate.ps1")) {
    throw "BRAK: .\scripts\p20_2_auto_retry_gate.ps1"
}

$gateOk = $true
try {
    .\scripts\p20_2_auto_retry_gate.ps1 *>&1 | Tee-Object -FilePath $log
} catch {
    $gateOk = $false
    ("P20_2_GATE_EXCEPTION: " + $_.Exception.Message) | Tee-Object -FilePath $log -Append | Out-Host
}
$gateExit = $LASTEXITCODE

$state=[ordered]@{
  ok=$true
  policy=""
  events=$null
  policy_file=""
  summary_file=""
  events_file=""
  reason="PASS"
}

# --- POLICY
$policyFile = Get-LatestFreshFile ".\reports\handoff\telemetry\P20_ALERT_POLICY_*.json" $start
if (-not $policyFile) {
  $state.ok=$false
  $state.reason="NO_FRESH_POLICY_FILE"
} else {
  $state.policy_file = (Resolve-Path $policyFile.FullName).Path
  # -AsHashtable naprawia przypadek policy/POLICY równocześnie
  $pj = Get-Content $policyFile.FullName -Raw | ConvertFrom-Json -AsHashtable

  $policyRaw = $null
  foreach($k in @("policy","POLICY","dominant_policy","DOMINANT_POLICY")){
    if($pj.ContainsKey($k) -and $null -ne $pj[$k] -and "$($pj[$k])".Trim() -ne ""){
      $policyRaw=[string]$pj[$k]
      break
    }
  }

  if([string]::IsNullOrWhiteSpace($policyRaw)){
    $state.ok=$false
    $state.reason="POLICY_UNKNOWN_OR_EMPTY"
  } else {
    $state.policy = $policyRaw.ToUpperInvariant()
    if($state.policy -eq "UNKNOWN"){
      $state.ok=$false
      $state.reason="POLICY_UNKNOWN_OR_EMPTY"
    }
  }
}

# --- SUMMARY + EVENTS
$summaryFile = Get-LatestFreshFile ".\reports\handoff\telemetry\P20_QUALITY_SUMMARY_*.json" $start
if (-not $summaryFile) {
  $state.ok=$false
  if($state.reason -eq "PASS"){ $state.reason="NO_FRESH_SUMMARY_FILE" }
} else {
  $state.summary_file = (Resolve-Path $summaryFile.FullName).Path
  $sj = Get-Content $summaryFile.FullName -Raw | ConvertFrom-Json -AsHashtable

  $events = $null
  foreach($k in @("events","EVENTS","total_events","TOTAL_EVENTS","count","COUNT")){
    if($sj.ContainsKey($k) -and $null -ne $sj[$k]){
      try { $events = [int]$sj[$k] } catch {}
      if($null -ne $events){ break }
    }
  }

  $eventsFile = Get-LatestFreshFile ".\reports\handoff\telemetry\P20_QUALITY_EVENTS_*.jsonl" $start
  if($eventsFile){
    $state.events_file = (Resolve-Path $eventsFile.FullName).Path
    if($null -eq $events -or $events -le 0){
      $lineCount = (Get-Content $eventsFile.FullName -ErrorAction SilentlyContinue | Measure-Object -Line).Lines
      if($lineCount -gt 0){ $events = [int]$lineCount }
    }
  }

  $state.events = $events
  if($null -eq $events -or $events -le 0){
    $state.ok=$false
    if($state.reason -eq "PASS"){ $state.reason="EVENTS_LE_ZERO_OR_NULL" }
  }
}

# --- GUARD (tylko jeśli gate + validation przeszły)
$guardOk = $false
if ($gateOk -and $gateExit -eq 0 -and $state.ok) {
  try {
    .\scripts\p14_continuous_release_guard.ps1 *>&1 | Tee-Object -FilePath $log -Append
    $guardOk = ($LASTEXITCODE -eq 0)
  } catch {
    $guardOk = $false
    ("P14_GUARD_EXCEPTION: " + $_.Exception.Message) | Tee-Object -FilePath $log -Append | Out-Host
  }
}

$branch = (git rev-parse --abbrev-ref HEAD).Trim()
$head   = (git rev-parse --short HEAD).Trim()

if (-not ($gateOk -and $gateExit -eq 0 -and $state.ok -and $guardOk)) {
  $outFail = "C:\AI\ai_orchestrator_scaffold\reports\handoff\HANDOFF_P20_2_AUTO_RETRY_FAILED_$ts.md"
  @(
    "PROJECT: AgentAI PRO"
    "PHASE: P20.2 AUTO RETRY POLICY FAILED"
    "TIME: $(Get-Date -Format s)"
    "BRANCH: $branch"
    "HEAD: $head"
    "GATE: p20_2_auto_retry_gate => FAIL"
    "GUARD: P14_GUARD_FAIL"
    "VALIDATION: policy=$($state.policy); events=$($state.events); reason=$($state.reason)"
    "POLICY_FILE: $($state.policy_file)"
    "SUMMARY_FILE: $($state.summary_file)"
    "EVENTS_FILE: $($state.events_file)"
    "STATUS: RED"
    "NEXT_TARGET: FIX_P20_2_AND_RERUN"
    "LOG: $log"
  ) | Set-Content $outFail -Encoding UTF8

  Write-Host "HANDOFF_READY: $outFail"
  Get-Content $outFail -Raw
  throw "P20_2_NOT_VERIFIED: see $log"
}

$outOk = "C:\AI\ai_orchestrator_scaffold\reports\handoff\HANDOFF_P20_2_AUTO_RETRY_VERIFIED_$ts.md"
@(
  "PROJECT: AgentAI PRO"
  "PHASE: P20.2 AUTO RETRY POLICY VERIFIED"
  "TIME: $(Get-Date -Format s)"
  "BRANCH: $branch"
  "HEAD: $head"
  "GATE: p20_2_auto_retry_gate => PASS"
  "GUARD: P14_GUARD_PASS"
  "VALIDATION: policy=$($state.policy); events=$($state.events); reason=$($state.reason)"
  "POLICY_FILE: $($state.policy_file)"
  "SUMMARY_FILE: $($state.summary_file)"
  "EVENTS_FILE: $($state.events_file)"
  "STATUS: GREEN"
  "NEXT_TARGET: P20.3_RETRY_OUTCOME_TELEMETRY_FEEDBACK_LOOP"
  "LOG: $log"
) | Set-Content $outOk -Encoding UTF8

Write-Host "HANDOFF_READY: $outOk"
Get-Content $outOk -Raw
