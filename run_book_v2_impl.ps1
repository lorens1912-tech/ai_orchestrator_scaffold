

# run_book_v2_impl.ps1
param(
    [Parameter(Mandatory = $true)][string]$Book,
    [int]$Delta = 3000,
    [string]$Model = "gpt-4.1-mini",
    [int]$MaxOutputTokens = 1200,

    [string]$PromptFile = "",
    [string]$PromptText = "",

    [bool]$OpenQC = $false,
    [bool]$OpenCurrent = $false
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# UTF-8 w konsoli (żeby nie było krzaków)
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}

# TLS 1.2 (PS 5.1)
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$ROOT = $PSScriptRoot

function Safe-Name([string]$s) {
    if (-not $s) { return "book" }
    return ($s -replace "[^a-zA-Z0-9_\-]+", "_").Trim("_")
}

function Read-Prompt {
    if ($PromptText -and $PromptText.Trim().Length -gt 0) {
        return $PromptText
    }
    if ($PromptFile -and (Test-Path -LiteralPath $PromptFile)) {
        return (Get-Content -LiteralPath $PromptFile -Raw -Encoding UTF8)
    }
    throw "Brak promptu: podaj -PromptText albo -PromptFile."
}

function Write-HttpErrorDetails($err) {
    try {
        Write-Host ""
        Write-Host "=========================" -ForegroundColor Yellow
        Write-Host "HTTP ERROR DETAILS" -ForegroundColor Yellow
        Write-Host "=========================" -ForegroundColor Yellow
        Write-Host ""

        if ($err.Exception -and $err.Exception.Response) {
            $resp = $err.Exception.Response
            try {
                Write-Host ("StatusCode: " + [int]$resp.StatusCode)
                Write-Host ("StatusDescription: " + $resp.StatusDescription)
            } catch {}

            try {
                if ($resp.GetResponseStream()) {
                    $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
                    $body = $reader.ReadToEnd()
                    Write-Host ""
                    Write-Host "HTTP RESPONSE BODY:" -ForegroundColor Yellow
                    Write-Host $body
                }
            } catch {}
        }

        try {
            if ($err.ErrorDetails -and $err.ErrorDetails.Message) {
                Write-Host ""
                Write-Host "ERRORDETAILS:" -ForegroundColor Yellow
                Write-Host $err.ErrorDetails.Message
            }
        } catch {}
    } catch {}
}

# --- ścieżki projektu ---
$bookSafe = Safe-Name $Book
$bookDir = Join-Path $ROOT ("books\" + $bookSafe)
$null = New-Item -ItemType Directory -Force -Path $bookDir | Out-Null

$currentPath = Join-Path $bookDir "current.txt"
$qcPath      = Join-Path $bookDir "qc.txt"
$lastJson    = Join-Path $bookDir "last_openai_response.json"

# --- prompt ---
$prompt = Read-Prompt

# --- API key ---
$apiKey = $env:OPENAI_API_KEY
if (-not $apiKey -or $apiKey.Trim().Length -lt 20) {
    throw "Brak OPENAI_API_KEY w zmiennych środowiskowych."
}

$baseUrl = $env:OPENAI_BASE_URL
if (-not $baseUrl -or $baseUrl.Trim().Length -eq 0) {
    $baseUrl = "https://api.openai.com/v1"
}

# Responses API
$uri = ($baseUrl.TrimEnd("/") + "/responses")

# WAŻNE: bez Content-Type w headers; ustawimy go parametrem -ContentType
$headers = @{
    "Authorization" = "Bearer $apiKey"
}

if ($env:OPENAI_PROJECT -and $env:OPENAI_PROJECT.Trim().Length -gt 0) {
    $headers["OpenAI-Project"] = $env:OPENAI_PROJECT
}
if ($env:OPENAI_ORG -and $env:OPENAI_ORG.Trim().Length -gt 0) {
    $headers["OpenAI-Organization"] = $env:OPENAI_ORG
}

# Body: Responses API
$body = @{
    model = $Model
    input = $prompt
    max_output_tokens = [int]$MaxOutputTokens
}

$json  = $body | ConvertTo-Json -Depth 10 -Compress

# CRITICAL FIX: PS 5.1 wysyła string często jako UTF-16LE -> OpenAI widzi invalid_json.
# Wysyłamy BYTES UTF-8.
$bytes = [System.Text.Encoding]::UTF8.GetBytes($json)

try {
    $resp = Invoke-RestMethod `
        -Method Post `
        -Uri $uri `
        -Headers $headers `
        -ContentType "application/json; charset=utf-8" `
        -Body $bytes `
        -TimeoutSec 300
} catch {
    Write-HttpErrorDetails $_
    throw
}

try {
    ($resp | ConvertTo-Json -Depth 50 -Compress) | Set-Content -LiteralPath $lastJson -Encoding UTF8
} catch {}

# Wyciągnięcie tekstu z Responses API
$text = $null
try {
    $parts = @()
    foreach ($o in $resp.output) {
        foreach ($c in $o.content) {
            if ($c.type -eq "output_text" -and $c.text) {
                $parts += $c.text
            }
        }
    }
    if ($parts.Count -gt 0) {
        $text = ($parts -join "")
    }
} catch {}

# fallback
if (-not $text -or $text.Trim().Length -eq 0) {
    try { if ($resp.output_text) { $text = [string]$resp.output_text } } catch {}
}

if (-not $text -or $text.Trim().Length -eq 0) {
    throw "Brak tekstu w odpowiedzi OpenAI. Sprawdź: $lastJson"
}

$text | Set-Content -LiteralPath $currentPath -Encoding UTF8

Write-Host ("CURRENT: " + $currentPath)
Write-Host ("QC: " + $qcPath)
Write-Host ("LAST_JSON: " + $lastJson)

if ($OpenCurrent) {
    try { Start-Process notepad.exe $currentPath } catch {}
}
if ($OpenQC) {
    try {
        if (-not (Test-Path -LiteralPath $qcPath)) { "" | Set-Content -LiteralPath $qcPath -Encoding UTF8 }
        Start-Process notepad.exe $qcPath
    } catch {}
}

exit 0



