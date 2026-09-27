# GAP-018 — audyt końcowy fazy 5/5

## Decyzja i granice

**FINAL AUDIT: ACCEPT / PASS.**
**GAP-018: CLOSED / FROZEN.**

Audyt dotyczy worktree `C:\AI\agentpro_gap018_phase2`, gałęzi
`codex/gap018-phase2`, bazowego HEAD
`1bfa735f47bf6b6d2d945d8c78860a426e7c3a4b`. Punktem odniesienia jest
zamrożony `docs/GAP018_PREFLIGHT.md` oraz aktywna droga `app.main` →
`app.operator_api` → usługi P20. Kontrakt preflight nie został zmieniony.
Naprawy findingów fazy 5 wykonano na mocy upoważnienia do cyklu
AUDIT → REPAIR → TEST → PRACTICAL GATE → REGRESSION → RE-AUDIT.

Zakres obejmuje nowe usługi ManuscriptVersion, Book QA, CandidateMaster,
SourceMaster i promocję; integrację operator API, ProjectRepository v7 oraz
F-004/Memory Ledger; testy GAP-018 i minimalne dostosowania historycznych
fixture migracyjnych P20. Nie obejmuje P0, GAP-019, tłumaczenia ani danych
rzeczywistej książki. `series.db` pozostaje w schemacie 4.

## Findingi audytu i naprawy

| ID | Reprodukcja i przyczyna | Najmniejsza naprawa | Dowód |
|---|---|---|---|
| P5-01 | Candidate odrzucony przez autora mógł otrzymać nowy review lub APPROVE przez wcześniej wystawiony challenge. Kontrola odrzucenia nie była trwałym warunkiem promocji. | Odczyt project-scoped `CANDIDATE_STATE` w `ProjectRepository`; kontrola przed review i w transakcji decyzji. | `test_rejected_candidate_cannot_be_approved_with_an_existing_or_new_challenge`: najpierw fail, potem pass. |
| P5-02 | Hierarchiczny finding nie wiązał wprost źródłowego `ContextPackage` i jego hasha; nie dało się wykazać pochodzenia konkretnego problemu. | `Finding.context_ref/context_hash`, wymagane w reduktorze i weryfikowane względem wywołania, które wytworzyło finding. | Negatywne warianty `test_hierarchical_book_qa_never_averages_a_fundamental_issue`: fail, potem pass; Practical Gate sprawdza binding. |
| P5-03 | F-004 `RecoveryInterventionRequired` było prezentowane jako retryable `RECOVERY_REQUIRED`. | Jawne `NEEDS_INTERVENTION`; ten sam status przy trwałym stale input po approval. | `test_source_recovery_intervention_is_not_reported_as_retryable`: fail, potem pass. |
| P5-04 | `versions[-1]` o typie innym niż obiekt JSON powodowało `AttributeError` zamiast kontraktowego blokera manuskryptu. | Kontrola typu ostatniego wpisu lineage przed `.get`. | `test_malformed_chapter_lineage_blocks_without_crashing`: fail, potem pass. |
| P5-05 | Po skutecznym Source commit trwały `SourcePromotionOperation.execution_status` pozostawał `RECORDED`; stan techniczny nie odpowiadał Source/CAS. | CAS rekordu `PROMOTION`, `SOURCE_PENDING` przed F-004, trwałe recovery/intervention, terminalny status i referencje w tej samej transakcji co Source/current/terminal receipt. | `test_author_challenge_approval_source_commit_and_replay`: fail, potem pass; 8 targeted status/recovery/CAS tests passed. |

Nie zmieniono decyzji jakości, zgody autora ani frozen contractu w celu
uzyskania zielonych testów.

## Macierz dowodów akceptacyjnych

| Kryterium | Wykonany dowód i wynik |
|---|---|
| Pozytywny przepływ przez oficjalne API | `TestClient(app.main.app)` z lokalnym operatorem i kontrolowanym transportem: accepted chapters → sealed ManuscriptVersion → Book QA COMPLETED/VALID/ACCEPT → Candidate → jednorazowy review/challenge → trwały APPROVE → F-004 → CAS → jeden SourceMaster/current. Odczyt po reopen, hash artefaktu i wydarzenia Memory Ledger sprawdzone. PASS. |
| QA negatywne i zgoda | REVISE, REJECT i BLOCKED nie tworzą Candidate/Source. USER REJECT utrwala odmowę i nie tworzy Source; nie można później zatwierdzić tego Candidate. Brak, wygasły, użyty lub niezgodny challenge oraz inny operator są odrzucane. PASS. |
| Crash/recovery/retry | Pięć punktów awarii F-004, cztery granice domain commit, utrata odpowiedzi HTTP i reopen. Oryginalny approval oraz expected head pozostają przypięte; retry nie tworzy drugiej zgody ani drugiego Source. `NEEDS_INTERVENTION` pozostaje bez Source. PASS. |
| Współbieżność i CAS | Dwie promocje tego samego expected head: jeden current Source, przegrana operacja `SOURCE_CONFLICT`; historia i replay zwycięzcy nie przestawiają nowszego head. Trwały `PROMOTION.execution_status` zgadza się z wynikiem. PASS. |
| Migracja i izolacja | Kontrolowane `project.db` 6→7, reopen, zachowanie identity, metadanych i Memory Ledger; `series.db` schema 4 i identyczne bajty. Dwa projekty nie widzą cudzych approval, Source ani F-004. PASS. |
| Stale i immutable | Nowy current ManuscriptVersion blokuje dawny Candidate. Zmiana bieżącego Book Bible/Canon nie przepisuje sealed snapshotów ani historii Source. FAIL CLOSED/PASS. |
| Długi manuskrypt | 200001 neutralnych słów; hierarchiczny Book QA: 59 wywołań kontrolowanego transportu, 85 par coverage, limit ContextPackage, kompletne provenance i jeden Source po osobnym approval. PASS. |

**Practical Gate** jest osobnym wykonywalnym audytem artefaktów i project.db:
`python tests/gap018_practical_gate.py`. Ostatni przebieg po P5-05:
exit 0; `concurrent_current_sources=1`, `fault_cases=5`,
`long_manuscript.words=200001`, `model_invocations=59`,
`coverage_pairs=85`, `migration.schema=7`, `series_schema=4`; wszystkie
kontrole `PASS`. Gate powtórzono po końcowej pełnej regresji oraz po
dodatkowym live verification; oba przebiegi miały exit 0.

## Rzeczywisty provider i Model Router

W osobnym, neutralnym przebiegu przez oficjalne `TestClient` API `/book-qa`
użyto istniejącego Model Routera i skonfigurowanego providera OpenAI bez
odczytu lub ujawnienia wartości credential. Trwały audit wywołania potwierdził:
`requested_model=null`, `effective_model=gpt-4.1-mini`, routing `default`,
provider-reported model `gpt-4.1-mini-2025-04-14`, identyfikator odpowiedzi
providera obecny, attempt `RECEIVED` i transport `TRANSPORT_COMPLETED`.
Jest to udana rzeczywista odpowiedź providera, a nie sam test połączenia.
Odpowiedź modelu nie spełniła kontraktu output Book QA: walidacja `INVALID`,
raport `BLOCKED` z `COVERAGE_INCOMPLETE` i `MODEL_PROVENANCE_INVALID`, bez
Candidate i Source. Pozytywny przepływ do Source jest dowiedziony przez
kontrolowany transport na tej samej oficjalnej granicy API. Live Book QA
ACCEPT nie jest twierdzony.

Dla rozstrzygającego dowodu poprawnej walidacji provenance wykonano jeszcze
jedno rzeczywiste wywołanie na neutralnym promptcie przez `resolve_model(None)`
i trwały `ModelInvocationAudit` P20. Wynik: provider `openai`, routing `default`,
`requested_model=null`, `effective_model=gpt-4.1-mini`, provider-reported model
`gpt-4.1-mini-2025-04-14`, response ID obecny, attempt `RECEIVED`, transport
`TRANSPORT_COMPLETED`, validation `VALID` i zgodny utrwalony `result_hash`.
Exit code 0. Kontrola `VALID` oznacza w tym probe niepustą, rzeczywistą
odpowiedź bez refusal; nie jest to literacki werdykt Book QA. Ten przebieg
wraz z wcześniejszym live API dowodzi działającego providera, oficjalnego
Model Routera i potwierdzonego provenance bez sztucznego PASS raportu QA.

Wcześniejsze błędy `APIConnectionError` w sandboxie nie były liczone jako
dowód wywołania; udany przebieg wykonano w dozwolonym realnym środowisku.
Nie dodano providera, nie ominięto Routera i nie użyto danych książki.

## Bramki i zamknięcie

Working directory wszystkich komend: `C:\AI\agentpro_gap018_phase2`.

| Bramka | Wynik po ostatniej zmianie kodu |
|---|---|
| AST 15 zmienianych modułów/testów oraz `git diff --check` | PASS |
| `python -m pytest -q -x tests/test_gap018_manuscript_version.py tests/test_gap018_book_qa_contract.py tests/test_gap018_storage_migration.py` | 47 passed, 762.45 s |
| `python tests/gap018_practical_gate.py` | PASS, exit 0 |
| `python -m pytest -q -x tests` | 1228 passed, 2 skipped, exit 0, 3114.26 s |
| Final Practical Gate po pełnej regresji i live verification | PASS, exit 0 |
| Final re-audit scoped diff, AST, frozen preflight, brak P0/GAP-019/danych książki | PASS |

Końcowy `git status --short` przed stagingiem pokazuje dokładnie 23 ścieżki
GAP-018/P20 opisane powyżej, w tym ten raport; staging jest pusty.
`docs/GAP018_PREFLIGHT.md` nie ma diffu, `git diff --check` przechodzi,
AST 15 modułów/testów przechodzi, skan nowych modułów i testów nie znajduje
P0, GAP-019 ani nazwy rzeczywistej książki. Nie stwierdzono nierozwiązanych
findingów w zamrożonym zakresie. Końcowy commit obejmuje wyłącznie te
23 ścieżki; push nie jest wykonywany.
