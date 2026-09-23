# GAP-017 — Phase 5: reaudyt naprawy

FINAL REAUDIT = REVISE
PHASE 5 = NOT READY_FOR_COMMIT
GAP-017 = NOT CLOSED
FULL REGRESSION = NOT RUN — wykryty bloker przed bramką
COMMIT/PUSH = NOT PERFORMED

## Checkpoint i zakres

Branch: `codex/agentpro-stabilization-freeze`.
HEAD: `bfe0bc2e6d35a44b78b6849d4af64ba57cc13e61`.
Preflight: staging EMPTY; 2563 wpisy = 2546 baseline + 17 autoryzowanych
ścieżek. Porównano pełne status/path z baseline fazy 1 i stanem końcowym
pierwszego audytu: obie delty = 0. Raport i katalog `reaudit/` nie istniały.

Odczytano zamrożony kontrakt, oryginalny audyt REVISE, raport naprawy,
diagnostyczny failing test/log, aktualną naprawę runtime/repository oraz
testy hardening. Wykorzystano poprzednią kontrolę niezmienionych komponentów.
SHA256 potwierdza, że od pierwszego audytu zmieniono tylko runtime.py,
project_repository.py i test_gap017_recovery_hardening.py z pierwotnej
listy 16 plików. Pozostałe 13 plików zachowuje tę samą zawartość.

## Wynik niezależnej kontroli naprawy

Pierwotna reprodukcja GAP017-P5-01 przechodzi: dwa identyczne REEVALUATE
ze stabilnym step_id zwracają ten sam nowy step i evaluation_id. Ukończony
wynik jest odtwarzany, bez powtórnego evaluatora; źródłowa ocena pozostaje
odrębnym rekordem. Potwierdzono także pinned ContextPackage, konflikt dla
zmienionego tekstu/reevaluation_of i izolację klucza między projektami/runami.

Naprawa nie obejmuje poprawnie nowej granicy trwałości: zapis mapowania
żądania może zakończyć się przed zapisem wejścia i rozpoczęciem Evaluation.
W tym stanie duplikat jest bezwarunkowo traktowany jak technical_retry,
chociaż nie ma danych potrzebnych do jego odtworzenia ani istniejącego
Evaluation, które można odzyskać. To blokuje akceptację całej poprawki.

## Bloker GAP017-P5-R01 — osierocone przypisanie żądania

Naruszone kryteria: reaudyt §2.C/F; kontrakt §D, §F i §F.1.6; zadanie
naprawcze: przerwanie po trwałym przypisaniu żądania musi zachować reguły
recovery. Przy niedowodliwym bindingu obowiązuje jawna interwencja,
bez automatycznej nowej oceny i bez ACCEPT.

Kod i przyczyna:

- `app/p20_core/project_repository.py:1458`,
  `ProjectRepository.claim_reevaluation_request`: krótka lokalna transakcja
  zapisuje tylko tożsamość, hash wejścia i przydzielony root step/operation.
  Brak fazy inicjalizacji, przypiętego wejścia i bindingu Evaluation.
- `app/p20_core/runtime.py:611`, `run_agent_step`: zatwierdza mapowanie przed
  uzyskaniem book lock (`:686`) i przed wykonaniem executora.
- `app/p20_core/runtime.py:638`: każdy istniejący wpis mapowania ustawia
  technical_retry=True, niezależnie od tego, czy operację rozpoczęto.
- `app/p20_core/executor.py:181`, `_bind_execution_input`: brak wpisu
  p20_execution_input.v1 przy retry daje stałe 409
  `technical retry execution inputs are not recorded` (`:200`).
- `app/p20_core/evaluation.py:872`, `recover_evaluation_by_identity` oraz
  `app/operator_api.py:253`, `recover_quality_evaluation`: recovery wymaga
  istniejącego envelope i evaluation_id. Sam wpis mapowania żądania nie jest
  obsługiwany przez ten mechanizm. Nie można odzyskać nieistniejącej oceny.

Reprodukcje przez rzeczywisty TestClient(app.main.app), P20 i project.db:

1. Po rzeczywistym commicie claim_reevaluation_request wstrzyknięto wyjątek,
   zanim wywołanie zwróciło do runtime. Usunięto wstrzyknięcie; dwa identyczne
   ponowienia nadal zwracają opisane 409.
2. Bez podmiany repozytorium ani runtime: aktywna syntetyczna blokada książki
   daje BOOK_LOCKED. Mapowanie zostało już zapisane. Po zwolnieniu tej blokady
   dwa ponowienia zwracają to samo 409 zamiast obsłużyć zarezerwowaną operację
   lub jawnie zakwalifikować jej niedowodliwy binding do interwencji.

W obu przypadkach: zero wywołań nowego evaluatora, w bazie pozostaje tylko
ocena źródłowa, mapowanie zachowuje przydzielony step/operation, brak
p20_execution_input.v1 dla przydzielonej operacji. Reopen nie usuwa problemu.
Nie stwierdzono podwójnego zapisu ani fałszywego ACCEPT; jest to luka
obsługi przerwania i dostępności, osiągalna także przy zwykłym BOOK_LOCKED.

Nowy probe kończy się **2 failed, 1 passed**, exit **1**. Dwa failures
odpowiadają powyższym granicom. Trzeci przypadek potwierdza poprawny konflikt
po zmianie reevaluation_of pod tym samym kluczem. Kryterium diagnostyczne
dopuszcza bezpieczne zakończenie zarezerwowanej operacji albo istniejący
rodzaj jawnej interwencji; nie narzuca ponownej oceny niedowodliwego bindingu.

Dowody: `.test_storage/gap017_evidence/phase5/reaudit/assignment-boundary.log`
oraz `test_assignment_boundary_probe.py`. Istniejący test
test_interrupted_reevaluation_request_keeps_assigned_evaluation przerywa
dopiero wewnątrz QUALITY, gdy RUNNING i pełny binding już istnieją, więc nie
obejmuje wykrytej granicy.

Minimalny wymagany zakres naprawy: powiązać rezerwację identyfikatorów ze
stanem inicjalizacji i dowodem wejść w obecnym project.db; rozróżniać wpis
zarezerwowany od operacji z gotowymi danymi retry. Obsłużyć odmowę lock oraz
przerwanie po rezerwacji w istniejącej ścieżce, zachowując przydzielone IDs.
Jeżeli bindingu nie da się dowieść, zapewnić jawne NEEDS_INTERVENTION zgodnie
z §F.1.6. Nie usuwać mapowania, nie przydzielać zastępczego evaluation_id,
nie odbudowywać wejść z latest i nie przejmować próby po timeout. Samo
przeniesienie claim za lock nie zamyka granicy przerwania po jego commicie.
Dodać dowody obu granic i współbieżności inicjalizacji. Naprawy nie wykonano
w tej sesji audytowej.

## Reaudyt §D: kryterium → kod → dowód

| Kryterium | Kod | Wynik / dowód |
|---|---|---|
| A: nowa ocena względem źródła | runtime.run_agent_step, find_evaluation | PASS w teście audytora; nowy evaluation_id różni się od źródłowego, duplikat zachowuje nowy ID. |
| B: atomowe mapowanie, jeden przydział | ProjectRepository.claim_reevaluation_request, BEGIN IMMEDIATE | PASS dla atomowego przydziału i konkurujących identycznych żądań; nie oznacza atomowej gotowości Evaluation — R01. |
| C: completed/running/recovery | start_evaluation, finalize_evaluation, recover_evaluation; internal technical_retry | PASS dla zakończonego wyniku i istniejącego RUNNING; REVISE przed utworzeniem bindingu — R01. |
| D: przypięty kontekst / konflikt | runtime._reevaluation_request_hash, ContextBuilder.reuse_for_retry | PASS: nowy test wykonuje duplikat z ContextBuilder.build ustawionym na natychmiastowy failure; działa reuse. Zmieniony tekst i reevaluation_of dają 409 przed evaluatorem. |
| E: brak/różne klucze | runtime: claim tylko przy niepustym requested_step_id | PASS: cztery różne oceny dla dwóch żądań bez klucza i dwóch różnych kluczy. |
| F: reopen / duplikaty / utrata odpowiedzi / przerwanie | claim + runtime + executor | Reopen i konkurencyjne duplikaty PASS. Test naprawy modeluje utratę odpowiedzi przez nieużycie otrzymanego wyniku, bez awarii transportu. Granica przypisanie → start: REVISE, dwa konkretne dowody. |
| G: scope i authority | registry identity, _assert_run_execution_identity, _ensure_identity, operator recovery auth | Nowe testy: izolacja project/run PASS. Kontrole book/series i operator authorization pozostają w kodzie; istniejące dowody negatywne w kompletnym GAP-017. Nie deklaruje się nowego mechanizmu autoryzacji /agent/step. |

## Dokończenie oryginalnego audytu

Pierwszy raport zawierał kontrolę wszystkich grup A–G, ale opierał ją
częściowo na dowodach fazy 4. Nie przedstawiam historycznych PASS jako
nowego uruchomienia. Niezmienione komponenty sprawdzono przez porównanie
SHA256; aktualne krytyczne fragmenty oraz testy naprawy odczytano ponownie.

| Kryterium | Kod / testy | Rozliczenie |
|---|---|---|
| Dokładna wersja artefaktu | runtime._active_quality_evaluation; exact artifact_id/hash i ordinal; test_same_bytes_rewrite_is_new_artifact_not_old_accept, test_runtime_selects_latest_exact_artifact_and_rejects_stale_accept | Kontrola statyczna i istniejące zielone przypadki w JUnit GAP-017. Nie wybiera po nazwie pliku. |
| Binding, cache, hash, LOCAL | evaluation.py: _binding_from_envelope, _record_from_envelope, _output_from_envelope, local_deterministic; test_each_cache_preimage_component_changes_key, test_corrupt_output_rejected_by_every_read | Plik evaluation.py bez zmian względem pierwszego audytu. Frozen preimages nie zmieniono przez naprawę. Nowy request hash jest odrębny od Evaluation/cache hash. |
| Retry/recovery/fencing | evaluation.start_evaluation/recover_evaluation/finalize_evaluation; operator API; hardening recovery/concurrency | Istniejące próby mają kontrolę tokena i brak takeover po timeout. Nowa nieobsłużona granica claim opisana w R01. |
| StylePerformance i historia | executor: finalize po style veto; AdaptiveStyleSession.finalize; test_style_downstream_crashes_are_idempotent, test_style_nonaccept_has_no_performance_after_reopen_retry, test_reevaluation_keeps_historical_performance_binding | Kod właściciela stylu bez zmian; dowody historyczne PASS. Test historii obejmuje też bezpośrednie przygotowanie sesji stylu, co nie jest testem żywego modelu. |
| Projekcje i lineage | _completed_step, CrossStoreRecoveryService, runtime._validate_evaluation_projections, chapter_lineage | Kod tych ownerów bez zmian. Test_projection_replay_or_conflict i chapter conflict w JUnit PASS. Rzeczywista kolejność: chapter/lineage przed audit; nie narzucono odwrotnej kolejności. |
| Canonical authority | runtime zachowuje wynik canon check; CanonService/operator final guard; test_evaluation_accept_has_no_canonical_authority | Dotychczasowe testy approval/verifier/final_guard są zielone w JUnit. Fixture kontroluje provider; dowód uprawnień, nie online transportu. |
| Ograniczone reuse | executor._completed_step i warunek WRITE/EDIT/REWRITE oraz QUALITY w initial_modes | Executor bez zmian względem pierwszego audytu. CRITIC, FACTCHECK i standalone WRITE zachowują wcześniejszą semantykę; końcowy log trzech przypadków fazy 4 nadal 3 passed. |
| SQLite initialization | ProjectRepository.connect/initialize: retry tylko PRAGMA WAL, SQLITE_BUSY, limit 30 s; BEGIN IMMEDIATE obejmuje wersję i schema | Statycznie zachowane. Wpływ obejmuje wszystkich użytkowników wspólnego repo, nie tylko Evaluation. Pakiet naprawy 44 testów repo/runtime/operator i wcześniejszy szerszy pakiet integracyjny są dowodami częściowymi; pełna zgodność repo NOT VERIFIED. |
| Rollback / błędy SQLite | test_real_sqlite_failure_rolls_back_final_record; connect commit/rollback | Istniejący dowód realnego SQLite ABORT w GAP-017. Nie wykonano nowego testu wyczerpania limitu WAL. |
| Jakość testów | pełny aktualny hardening; historyczne testy Evaluation/P20 bez zmian | Nie stwierdzono osłabienia asercji. W sekwencji dwóch świadomych reevaluation nadano różne klucze; zgodne z §D. Brak nowych skipów. Reopen nie jest dowodem odporności na utratę zasilania. |

## Rzeczywiście wykonane testy reaudytu

Wszystkie polecenia uruchomiono z `C:\AI\ai_orchestrator_scaffold`.

```powershell
python -m pytest -q -s -p tests.conftest .test_storage/gap017_evidence/phase5/test_reevaluation_request_identity.py --basetemp=C:\Users\Admin\AppData\Local\Temp\agentpro-gap017-reaudit-5e172745f3084828a44ed61fd1056813
python -m pytest -q tests/test_gap017_recovery_hardening.py -k 'reevaluation_request_identity or reevaluation_without_identity or interrupted_reevaluation_request' --basetemp=C:\Users\Admin\AppData\Local\Temp\agentpro-gap017-reaudit-e6c9aec6f6e94aada31996c50f70226b
python -m pytest -q -s -p tests.conftest .test_storage/gap017_evidence/phase5/reaudit/test_assignment_boundary_probe.py --basetemp=C:\Users\Admin\AppData\Local\Temp\agentpro-gap017-reaudit-71fc6831d2b148caa45b882bd5db8bd5
```

| Gate | Wynik | Exit code | Skips |
|---|---|---|---|
| auditor | 1 passed in 11.72s | 0 | 0 |
| dedup | 6 passed, 61 deselected in 36.60s | 0 | 0 |
| assignment-boundary | 2 failed, 1 passed in 22.88s | 1 | 0 |
| Pełna regresja | NOT RUN — znany bloker | N/A | N/A |

Pełne logi, dokładne polecenia i exit codes zapisano pod
`.test_storage/gap017_evidence/phase5/reaudit/` jako `<gate>.log`,
`<gate>-command.txt`, `<gate>-exit-code.txt`.

Odczytano także historyczne JUnit naprawy: auditor 1/0/0, dedup 6/0/0,
GAP-017 124/0/0, bezpośrednia integracja 44/0/0 (tests/failures/errors;
wszędzie skipped=0). Nie powtarzano tych pakietów. W katalogu naprawy
dostępne są JUnit i raport; nie ma pełnych tekstowych logów tych przebiegów.
Nowe logi reaudytu zachowują pełny stdout/stderr i rzeczywisty exit code.

Pełnej regresji nie uruchomiono zgodnie z warunkiem użytkownika: znany
bloker wyklucza kosztowny przebieg. Historyczny baseline skip dotyczył
legacy PATCH /canon/{book_id} w test_110_canon_api_roundtrip.py:47; nie
przedstawiam go jako wyniku bieżącej regresji. Deselected nie oznacza skip.

## Ochrona danych i ograniczenia dowodów

Użyto wyłącznie neutralnych danych, TestClient oraz istniejącego conftest:
unikalny .test_storage/sessions/... i isolated_agentpro_storage. Sprawdzono
guardy zapisów dla realnych books/runs/novel_runs/audit oraz kontrolę SDK
z syntetyczną konfiguracją. Żaden test tego reaudytu nie wymagał poświadczeń
operatora ani wywołania płatnego modelu. Nie czytano ani nie hashowano
rzeczywistych książek. Porównanie ich zawartości jest OUT OF SCOPE.

Nie zmieniono istniejących plików implementacji, testów, zamrożonego
kontraktu, pierwotnego raportu REVISE, failing log ani raportu naprawy.
Dodano tylko niniejszy raport i nowe izolowane dowody diagnostyczne.
Sonda wstrzykuje awarię po rzeczywistym commicie; wariant BOOK_LOCKED
nie podmienia runtime ani repozytorium. Nie symulowano utraty zasilania.

## Dokładny zakres przyszłego commita

Lista dotychczasowych 17 ścieżek, bez stagingu i bez deklaracji gotowości:

1. app/main.py
2. app/operator_api.py
3. app/p20_core/adaptive_style.py
4. app/p20_core/canon_service.py
5. app/p20_core/chapter_lineage.py
6. app/p20_core/contracts.py
7. app/p20_core/cross_store_recovery.py
8. app/p20_core/evaluation.py
9. app/p20_core/executor.py
10. app/p20_core/project_repository.py
11. app/p20_core/runtime.py
12. docs/GAP017_EVALUATION_CONTRACT.md
13. docs/GAP017_PREFLIGHT.md
14. tests/test_gap017_evaluation_record.py
15. tests/test_gap017_p20_integration.py
16. tests/test_gap017_recovery_hardening.py
17. docs/GAP017_FINAL_AUDIT.md

Nowy raport osobno: 18. docs/GAP017_FINAL_REAUDIT.md.
Dowody w .test_storage są ignorowane przez Git; nie pominięto żadnej obcej
ścieżki, aby uzyskać zerową deltę. Przed przyszłym commitem wymagane są
usunięcie R01, reaudyt oraz wymagana pełna regresja.

## Kontrola końcowa

Wyniki maszynowe: `.test_storage/gap017_evidence/phase5/reaudit/verification.json`.
Pełny `git status --short`: `reaudit/final-status.txt` w tym samym katalogu
dowodów fazy 5. Scoped diff/check obejmuje wyłącznie jawne ścieżki GAP-017.
Standardowy git diff --check: exit 0. Dodatkowe --no-index --check dla
każdego untracked pliku zakresu: brak komunikatów whitespace; exit 1 oznacza
różnicę względem /dev/null, nie błąd whitespace.

Sprawdzony stan po dodaniu raportu: 2564 wpisy = 2546 baseline oraz 18
autoryzowanych ścieżek. Dokładna baseline delta = 0, staging EMPTY,
HEAD bez zmian. SHA256 17 zastanych plików zakresu i 3 chronionych artefaktów
dowodowych nie zmieniły się względem preflight reaudytu. Kontrola nie
obejmuje danych książek. PRE-EXISTING WORKTREE CHANGES PRESERVED = YES
w granicach dokładnego porównania status/path oraz powyższych hashy.
