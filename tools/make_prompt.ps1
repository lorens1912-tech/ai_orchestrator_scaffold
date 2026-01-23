param(
  [Parameter(Mandatory=$true)][string]$Root,
  [Parameter(Mandatory=$true)][string]$Book,
  [Parameter(Mandatory=$true)][string]$BasePromptRel
)

$bookDir = Join-Path $Root ("books\{0}" -f $Book)
$bible   = Join-Path $bookDir "memory\book_bible.json"
$style   = Join-Path $bookDir "style\style_profile.json"
$base    = Join-Path $Root $BasePromptRel

if (!(Test-Path $base)) { throw ("Brak base prompt: {0}" -f $base) }

$bibleTxt = "{}"
if (Test-Path $bible) { $bibleTxt = Get-Content $bible -Raw }

$styleTxt = "{}"
if (Test-Path $style) { $styleTxt = Get-Content $style -Raw }

$baseTxt  = Get-Content $base -Raw

$outDir = Join-Path $Root "prompts\generated"
New-Item -ItemType Directory -Force $outDir | Out-Null

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$outName = "prompt_{0}_{1}.txt" -f $Book, $ts
$outPath = Join-Path $outDir $outName

@"
[PAMIĘĆ_KSIĄŻKI book_bible.json]
$bibleTxt

[PROFIL_STYLU style_profile.json]
$styleTxt

[PROMPT_BAZOWY]
$baseTxt
"@ | Set-Content -Encoding UTF8 $outPath

# WYJŚCIE: tylko jedna linia (string) z prompt_file jak chce API
$outRel = $outPath.Replace($Root + "\", "").Replace("\","/")
Write-Output $outRel
