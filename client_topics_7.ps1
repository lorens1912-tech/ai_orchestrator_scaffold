
$ErrorActionPreference = "Stop"

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$BaseUrl = "http://127.0.0.1:8000"
$Root   = "C:\AI\ai_orchestrator_scaffold"

function PostJson($path, $body){
    $json = $body | ConvertTo-Json -Depth 8
    Invoke-RestMethod -Method POST -Uri ($BaseUrl + $path) -ContentType "application/json; charset=utf-8" -Body $json
}

# Tematy i parametry
$topics = @(
    @{ book = "topic1_motywacja";   notes = "Napisz krótki tekst motywacyjny na ok. 120 słów."; max_words = 120 },
    @{ book = "topic2_kroliczek";   notes = "Napisz bajkę o króliczku na ok. 120 słów."; max_words = 120 },
    @{ book = "topic3_trojca";      notes = "Tekst chrześcijański o Trójcy Świętej, ok. 120 słów."; max_words = 120 },
    @{ book = "topic4_dyscyplina";  notes = "Zasady samodyscypliny, poradnik, ok. 120 słów."; max_words = 120 },
    @{ book = "topic5_hymn";        notes = "Pierwsza zwrotka hymnu Nikaragui (oryginał hiszpański)."; max_words = 50 },
    @{ book = "topic6_sny";         notes = "Jak interpretować sny? Wyjaśnij w 120 słowach."; max_words = 120 },
    @{ book = "topic7_baltyk";      notes = "Opisz krajobraz Bałtyku, ok. 120 słów."; max_words = 120 }
)

Write-Host "Health OK"

foreach ($t in $topics) {
    Write-Host "=== $($t.book) ==="
    # Usuń poprzedni draft (jeśli chcesz czysty test)
    $draftPath = Join-Path $Root "books\$($t.book)\draft\master.txt"
    if (Test-Path $draftPath) { Remove-Item $draftPath -Force }

    # Zlecenie
    $resp = PostJson "/books/agent/step" @{
        book = $t.book
        intent = "write_next_scene"
        notes = "$($t.notes) Napisz dokładnie $($t.max_words) słów."
        max_words = $t.max_words
        mode = "buffer"
    }
    $jobId = $resp.job_id

    # Worker
    $worker = PostJson "/books/agent/worker/once" @{
        book = $t.book
        job_id = $jobId
        max_chars_from_prompt = 1000
    }

    # Accept
    $accept = PostJson "/books/agent/accept" @{
        book = $t.book
        clear_buffer = $true
        require_fact_ok = $false
        bypass_gate = $true
    }

    # Policz słowa
    $out = Get-Content $draftPath -Raw
    $words = ($out -split '\s+').Count
    Write-Host "WORDS: $words"
}
Write-Host "DONE 7×120"
Pause
