$BaseUrl = "http://127.0.0.1:8000"
$Prefix  = "test_book_parallel"
$Count   = 7

function Call($method, $url, $body){
    Invoke-RestMethod -Method $method -Uri $url -ContentType "application/json" -Body ($body | ConvertTo-Json)
}

Write-Host "Health check"
Invoke-RestMethod "$BaseUrl/openapi.json" | Out-Null

for($i=1; $i -le $Count; $i++){
    $book = "$Prefix`_$i"
    Write-Host "=== BOOK $book ==="

    $step = Call POST "$BaseUrl/books/agent/step" @{
        book = $book
        intent = "write_next_scene"
        mode = "buffer"
    }

    $job = $step.job_id
    if(-not $job){ throw "NO JOB_ID for $book" }

    Call POST "$BaseUrl/books/agent/worker/once" @{
        book = $book
        job_id = $job
    }

    try {
        Call POST "$BaseUrl/books/agent/fact_check" @{
            book = $book
            source = "buffer"
        }
    } catch {}

    Call POST "$BaseUrl/books/agent/accept" @{
        book = $book
        clear_buffer = $true
        require_fact_ok = $false
    }

    $p = "C:\AI\ai_orchestrator_scaffold\books\$book\draft\master.txt"
    if(Test-Path $p){
        Write-Host "--- TAIL $book ---"
        Get-Content $p -Tail 5
    }
}
Write-Host "DONE: 7 books"
