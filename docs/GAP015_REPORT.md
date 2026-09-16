# GAP-015 — raport wdrożenia

GAP-015 STATUS = CLOSED (2026-09-16; wdrożony przepływ tekstowy PROJECT)

AUDITED BASE HEAD = `01da0b007a1e6c96839f46515c99d1d1a0bcfe2c`.
Zakres: PROJECT, zaakceptowane rozszerzenie Research → Canon.
GAP-016 = NOT STARTED.

## Przyczyna i zmiany

Dotychczasowy `app/tools.py::tool_factcheck` zwracał pustą listę ISSUES bez
weryfikacji. Repo nie miało aktywnego procesu ResearchRecord/ResearchSource/
ResearchClaim ani zatwierdzonego wcześniej wejścia research do CanonService.
Decyzja wykonawcza z 2026-09-16 rozstrzygnęła authority i wariant proposal.

Zaimplementowano:

- `app/p20_core/research.py`: kontrakty, import tekstu, wersje, cytaty, osobne
  wykonania ekstraktora/weryfikatora, konflikty, kontekst, retry i recovery.
- `app/p20_core/project_repository.py`: research w istniejącym project.db,
  lokalne transakcje i finalna kontrola dowodów przy fizycznym zapisie.
- `app/p20_core/canon_service.py`: addytywny wariant research w tym samym
  proposal/review/decision/commit; atomowy zapis historii, decyzji i audytu.
- `app/p20_core/domain_mutation_guard.py`: nowy proces authority, obowiązkowa
  zgoda na wszystkie mutacje researchu, blokada starszej ścieżki bez approval.
- `app/operator_api.py`: oficjalne operacje questions/sources/execute/read/
  recover/proposals, istniejący lokalny operator; bez nowego systemu zgody.
- `app/p20_core/context_runtime.py`, `app/tools.py`, `app/p20_core/runtime.py`:
  aktywny FACTCHECK, role-aware context, jawny stan wykonania i audit artifacts.
- `tests/test_gap015_research.py`: deterministyczne dowody API, P20, storage,
  ochrony, stale, izolacji, recovery i rollbacku.
- Canonical Change Contract §15 i instrukcja `docs/GAP015_RESEARCH.md`.
- `ROADMAPA_AGENTPRO.md`: bieżący status etapu 10 i odnośniki do dowodów.

## Granice i interpretacja dowodu

RESEARCH AUTHORITY PROCESS = `P20_VERIFIED_RESEARCH_V1`.
RESEARCH APPROVAL REQUIRED FOR ALL MUTATIONS = YES.
CONTRACT DOCUMENT / SCHEMA VERSION = dokument 1.2; proposal research 2.0;
dotychczasowa ekstrakcja 1.0 i jej preimage/hash zachowane.

Producent claimów i niezależny weryfikator wywołują produkcyjny adapter
`app.llm_provider_openai.call_text`; wyłącznie `Responses.create` na granicy
zewnętrznego SDK jest zastąpione w testach. CanonService, guard, repository,
ContextBuilder, operator authentication i runtime nie są atrapami.

EVIDENCE / PROPOSAL HASH BINDING = źródła/wersje/cytaty, claim/wersja,
oddzielne wywołania, kryteria i wynik weryfikacji, rzeczywiste konteksty,
dokładne mapowanie twierdzenia na FactRecord. Rozszerzone twierdzenie jest
odrzucane także po przeliczeniu hashy. Backend ponownie odczytuje dowody.

STALE APPROVAL REJECTION = zmiany source, claim, verification, target i grafu
invalidują podstawę. Nowa wersja proposal nie przejmuje starej zgody.
Oryginalne decyzje i wersje pozostają w historii.

SOURCE IMPORT / ACQUISITION = tekst, zachowane whitespace, hash faktycznej
treści, bibliografia i wersje. Sam URL/PDF label pozostaje REFERENCE_ONLY.
Automatyczne pobieranie URL, binarny parser PDF i OCR nie zostały dodane.
To jawne ograniczenie formatów wejścia, bez deklarowania ich obsługi.

CLAIM EXTRACTION / EVIDENCE-BOUND VERIFICATION = cały zestaw atomowy;
brak confidence pozostaje null przed oceną; CONFIRMED wymaga oceny i cytatów.
Malformed JSON, niepełny zestaw, brak dowodu/konfiguracji lub błąd transportu
nie daje sukcesu. Source content jest danymi, nie instrukcjami.

CONFLICT HANDLING / AUTHOR DECISION / FICTIONAL OVERLAY = obie strony
i cytowania zachowane. Jawna fikcja ma powód, scope, hash propozycji i
wiąże konkretny stan faktu. Nie zamienia źródłowego statusu w CONFIRMED.
Retry nie zgłasza ponownie niezmienionego, zatwierdzonego wyjątku.

CANON MUTATION GUARD = CREATE bez zgody nie zapisuje faktu; protected UPDATE
zachowuje frozen i author_locked. CURRENT_PROTECTION_UNKNOWN i
DERIVED_REBUILD_UNSUPPORTED nadal blokują zapis. SERIES bez rozszerzenia.

PERSISTENCE / REOPEN / IDEMPOTENCY / TECHNICAL RETRY = project.db; wersje
append-only, przypięte wejścia i ContextPackage, powtórny commit zwraca receipt.
Jawne recovery odgradza poprzednią próbę; późna odpowiedź nie zapisuje claimów.
Final guard, rekordy, historia, AuthorDecision i audit commit są w jednej
lokalnej transakcji. Awaria drugiego elementu wycofuje cały zestaw.

MIGRATION / RECOVERY = nie zmieniono schematu SQL; użyto project_metadata.
Nie uruchomiono migracji rzeczywistych danych. Research nie wykonuje zapisu
cross-store; istniejący F-004 jest zachowany dla rzeczywistych takich operacji.

CONTEXT BUILDER / P20 FACTCHECK = jawny research_id w payload, właściwy projekt,
role-aware claims/źródła/konflikty/decyzje, hash i zapisany kontekst dla retry.
NOT_PERFORMED i FAILED nie są raportowane jako udany FACTCHECK.
OPERATOR AUTHORIZATION = istniejący loopback/Bearer + review/challenge/decision;
uwierzytelnienie nie jest approval. Sieć/LLM poza transakcją operatorską i zapisu.
P0 RUNTIME PARTICIPATION = NO.

LIVE ADAPTER SMOKE = NOT RUN. Zgodnie z poleceniem użyto kontrolowanego SDK;
nie odczytywano ani nie używano rzeczywistych sekretów, nie wykonano płatnych
wywołań. Wyniki nie dowodzą jakości ocen żywego dostawcy.

## Testy i evidence

TARGETED TESTS = 36 passed in 148.36s, exit code 0.
INTEGRATION TESTS = 129 passed in 280.84s, exit code 0.
FULL REGRESSION = 657 passed, 1 skipped in 612.48s, exit code 0.
Pełny suite wykonano jeden raz po finalnej zmianie kodu/testów.
Istniejący skip: `test_110_canon_api_roundtrip.py:47` — legacy PATCH /canon/{book_id}
nie jest udostępniany przez aktywne app.main. Nie dodano nowych skipów.
STYLE LIBRARY REGRESSION = PASS (test_style_library_24 + test_gap014_adaptive_style).
OLD EXTRACTION CONTRACT COMPATIBILITY = PASS (PROJECT/SERIES i transport).
PROJECT ISOLATION / MEMORY LEAKAGE = PASS (obcy research, source scope, kontekst).

Komendy PowerShell (każda bramka miała nowy, syntetyczny katalog TEMP):

```powershell
$testTemp = Join-Path $env:TEMP ('agentpro-gap015-' + [guid]::NewGuid().ToString('N'))
python -m pytest tests/test_gap015_research.py -q --disable-warnings --basetemp=$testTemp

$testTemp = Join-Path $env:TEMP ('agentpro-gap015-integration-' + [guid]::NewGuid().ToString('N'))
python -m pytest tests/test_canonical_pipeline.py tests/test_canonical_project_records.py tests/test_canonical_series_pipeline.py tests/test_memory_transport_integration.py tests/test_local_operator.py tests/test_p20_domain_mutation_guard.py tests/test_p20_runtime_context_integration.py tests/test_p20_runtime_identity_integration.py tests/test_p20_cross_store_recovery.py tests/test_gap014_adaptive_style.py tests/test_style_library_24.py tests/test_p20_active_legacy_boundary.py -q --disable-warnings --basetemp=$testTemp

$testTemp = Join-Path $env:TEMP ('agentpro-gap015-full-' + [guid]::NewGuid().ToString('N'))
python -m pytest -q --disable-warnings -rs --basetemp=$testTemp
```

Logi: `.test_storage/gap015_evidence/targeted-final.txt`, `integration.txt`,
`full-regression.txt`. Dowód API zapisuje test
`test_active_p20_factcheck_context_audit_retry_and_fiction` w izolowanym storage.
Dokładne komendy oraz kody wyjścia: `.test_storage/gap015_evidence/gates.json`.
Skopiowany dowód API: `.test_storage/gap015_evidence/api-proof.json`.

## Dane i Git

REAL USER DATA / CANON MUTATED DURING TESTS = NO.
Testy używają neutralnych projektów i fixture `isolated_agentpro_storage`;
istniejący autouse guard blokuje zapisy do rzeczywistych books/runs/audit.
Operator DPAPI jest wyłącznie syntetyczny, w osobnym TEMP (kontrakt zakazuje
poświadczenia wewnątrz repo). Sandbox blokował ACL; testy DPAPI wykonano poza nim.

Baseline obejmuje 2546 wpisów `git status --porcelain=v1 -uall` (3 tracked
zmiany i 2543 osobne pliki untracked; standardowy status grupuje katalogi).
Lista zachowana; nie hashowano ani nie porównywano treści rzeczywistych książek.
PRE-EXISTING WORKTREE CHANGES PRESERVED = YES (kontrola listy + guard zapisu).
Staging i commit obejmują wyłącznie 13 jawnych plików GAP-015.
Commit/checkpoint i końcowy status: raport sesji po przejściu bramek.
