
# grow_to_3000_words.ps1
# Cel: dopisać tekst w book_text.txt aż do TARGET_WORDS (domyślnie 6000)
# Liczenie słów: Microsoft Word (ComputeStatistics)
# WAŻNE: Word COM wymaga STA -> uruchamiamy zawsze w Windows PowerShell 5.1 -STA

$ErrorActionPreference = "Stop"

# Jeśli ktoś odpalił to w PS7/pwsh (Core/MTA) -> relaunch do PS5.1 STA
$ps5 = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
if ($PSVersionTable.PSEdition -eq "Core" -or [System.Threading.Thread]::CurrentThread.ApartmentState -ne "STA") {
    & $ps5 -NoProfile -ExecutionPolicy Bypass -STA -File $PSCommandPath @args
    exit $LASTEXITCODE
}

# UTF-8 w konsoli i dla Pythona (żeby nie było UnicodeEncodeError / cp1250)
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
chcp 65001 > $null
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = New-Object System.Text.UTF8Encoding($false)
} catch {}

$root = $PSScriptRoot
$out  = Join-Path $root "book_text.txt"

# >>> USTAW TU CEL (albo ustaw env:TARGET_WORDS) <<<
$target = if ($env:TARGET_WORDS) { [int]$env:TARGET_WORDS } else { 6000 }

# Model do dopisywania
$model = if ($env:MODEL_NAME) { $env:MODEL_NAME } else { "gpt-4.1-mini" }

# Ile tokenów max ma dopisać na jeden krok
$maxTokens = if ($env:MAX_OUTPUT_TOKENS) { [int]$env:MAX_OUTPUT_TOKENS } else { 1200 }

function Ensure-File([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) {
        Set-Content -LiteralPath $path -Value "" -Encoding UTF8
    }
}

function Get-WordCountWord([string]$path) {
    Ensure-File $path

    $word = $null
    $doc  = $null
    try {
        $word = New-Object -ComObject Word.Application
        $word.Visible = $false
        $word.DisplayAlerts = 0

        $doc = $word.Documents.Open((Resolve-Path -LiteralPath $path).Path, $false, $true)
        $cnt = [int]$doc.ComputeStatistics(0) # 0 = wdStatisticWords
        return $cnt
    }
    finally {
        if ($doc) {
            try { $doc.Close($false) | Out-Null } catch {}
            try { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($doc) } catch {}
        }
        if ($word) {
            try { $word.Quit() | Out-Null } catch {}
            try { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($word) } catch {}
        }
        try { [GC]::Collect(); [GC]::WaitForPendingFinalizers() } catch {}
    }
}

function Call-LLM([string]$prompt, [string]$model, [int]$maxTokens) {
    $tmp = Join-Path $env:TEMP ("prompt_" + [guid]::NewGuid().ToString("N") + ".txt")
    Set-Content -LiteralPath $tmp -Value $prompt -Encoding UTF8

    try {
        $py = @"
import sys, pathlib, os
os.environ.setdefault("PYTHONUTF8","1")
os.environ.setdefault("PYTHONIOENCODING","utf-8")
p = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
from llm_client import generate_text
txt = generate_text(p, model=sys.argv[2], max_output_tokens=int(sys.argv[3]))
print(txt)
"@

        Push-Location $root
        try {
            return (python -c $py $tmp $model $maxTokens)
        } finally {
            Pop-Location
        }
    }
    finally {
        Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
    }
}

Ensure-File $out

$cnt = Get-WordCountWord $out
Write-Host "START SLOWA_WORD=$cnt"
Write-Host "PLIK=$out"
Write-Host "TARGET=$target  MODEL=$model  MAX_TOKENS=$maxTokens"
Write-Host ""

while ($cnt -lt $target) {
    $text = Get-Content -LiteralPath $out -Raw -Encoding UTF8

    # bierzemy końcówkę do kontekstu
    $tailLen = 2500
    $tail = if ($text.Length -gt $tailLen) { $text.Substring($text.Length - $tailLen) } else { $text }

    $prompt = @"
Kontynuuj dokładnie ten tekst (bez powtarzania). Zachowaj styl i wątek.
Nie dodawaj nagłówków, meta ani podsumowań. Pisz dalej spójnie.

Ostatni fragment:
$tail

Dalej:
"@

    $chunk = Call-LLM -prompt $prompt -model $model -maxTokens $maxTokens

    if ([string]::IsNullOrWhiteSpace($chunk)) {
        throw "LLM zwrócił pusty tekst – przerywam."
    }

    Add-Content -LiteralPath $out -Value ("`r`n`r`n" + $chunk.Trim()) -Encoding UTF8

    $cnt = Get-WordCountWord $out
    Write-Host "SLOWA_WORD=$cnt"
}

Write-Host ""
Write-Host "OK. PLIK: $out"
