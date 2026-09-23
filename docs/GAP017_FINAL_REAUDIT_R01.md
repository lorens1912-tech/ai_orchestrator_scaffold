# GAP-017 — Phase 5: reaudyt R01 i końcowa weryfikacja

GAP017-P5-R01 = VERIFIED_FIXED
GAP017-P5-R02 = VERIFIED_FIXED
INDEPENDENT FINAL REAUDIT = ACCEPT
FULL REGRESSION = PASS
PHASE 5 = READY_FOR_COMMIT
GAP-017 = NOT CLOSED
COMMIT/PUSH = NOT PERFORMED

Ten raport nie zastępuje historycznych raportów REVISE. Brak otwartych blokerów
w sprawdzonym zakresie zamrożonego kontraktu. Gotowość nie oznacza zamknięcia GAP-017.

- Checkpoint: `bfe0bc2e6d35a44b78b6849d4af64ba57cc13e61`.
- Branch: `codex/agentpro-stabilization-freeze`.
- GAP-017 = NOT CLOSED. COMMIT/PUSH = NOT PERFORMED.
- Dowody: `.test_storage/gap017_evidence/phase5/r01_reaudit/`.
- Zakres decyzji: zamrożony `GAP017_EVALUATION_CONTRACT.md`, w tym RECOVERY v1; bez zmiany authority, routingu ani progów.

## Kontynuacja i preflight

Potwierdzono checkpoint, branch i pusty staging. Porównanie status/path z
`r01_fix/final-status.txt` oraz `final-porcelain.txt`: dokładna delta 0.
2564 wpisy oznaczały 2546 zastanych wpisów baseline oraz 18 jawnych ścieżek
GAP-017. Nie zastępowano porównania ścieżek zgodnością liczby.
Odczytano kontrakt, oba historyczne raporty, raport r01_fix, surowe logi,
sondy i aktualny kod. Hash czterech plików naprawy odpowiadał r01_fix.

## Wykryte problemy i naprawy

### GAP017-P5-R01 — rezerwacja nie jest rozpoczęciem oceny

Naprawa zastana, niezależnie sprawdzona, bez odtwarzania implementacji.
`ProjectRepository.claim_reevaluation_request` utrwala RESERVED z przydzielonymi
IDs; checkpoint wejścia i kontekstu powstaje w tej samej transakcji co dane.
`evaluation_transaction` utrwala STARTED razem z pełnym envelope oceny.
`runtime.run_agent_step` sprawdza stan po zdobyciu book/run locks.

Oryginalna sonda audytora: 3 passed. Bezpośrednie 14 przypadków naprawy:
14 passed. Sprawdzono awarię po rezerwacji, BOOK_LOCKED, przypięty kontekst,
współbieżne duplikaty, reopen, recovery, fencing oraz trwałą przyczynę
NEEDS_INTERVENTION również bez EvaluationRecord.

### GAP017-P5-R02 — częściowa migracja starszej rezerwacji przez retry/recovery

Wykryto i odtworzono dodatkowy błąd przejścia. Starsza rezerwacja z poprawnym
COMPLETED+VALID mogła wejść przez explicit technical retry. Checkpointy dopisywały
input_hash i STARTED/evaluations, lecz bez contexts, bo kontekst był odczytywany.
Następny keyed replay uznawał własny niepełny checkpoint za uszkodzony i trwale
przechodził do NEEDS_INTERVENTION. Sonda: 1 failed, exit 1; evaluator nie był
ponawiany. To regresja dostępności/deduplikacji, a nie nieuprawnione ACCEPT.

Minimalna naprawa:

- `runtime.run_agent_step`: walidacja pełnej rezerwacji także przed explicit technical retry.
- `ProjectRepository.checkpoint_reevaluation_request`: checkpoint nie wykonuje częściowej migracji starszego formatu, także podczas operator recovery.
- `ProjectRepository.reevaluation_request_requires_retry`: starszy stan jest przyjmowany wyłącznie po udowodnieniu całego bindingu; zwykły retry bez rezerwacji zachowuje istniejącą ścieżkę.
- Explicit retry RESERVED pozostaje retry: nie może stać się pierwszym wykonaniem zmienionego żądania bez oryginalnego request/hash bindingu.

Sonda po poprawce: 1 passed; dwukrotny keyed replay odtwarza identyczny rekord,
0 wywołań evaluatora. Nowe 5 przypadków regresyjnych obejmuje iloczyn
running/completed × technical/operator oraz odmowę zmienionego wejścia przed START.
Wszystkie 5 passed. Poprawiono tylko trzy pliki wskazane dalej.

## Pokrycie zamrożonego kontraktu

| Sekcja / kryterium | Właściciel i dowód |
|---|---|
| A: jeden owner i scope | ProjectRepository.evaluation_transaction; testy EvaluationRecord oraz isolation/reopen. project.db pozostaje właścicielem. |
| B: rzeczywisty wykonawca, tożsamość, hashe | evaluation.EvaluationBinding i walidatory; complete_record, local_deterministic_has_no_fictitious_model_identity, exact whitespace oraz same_bytes_rewrite. Lokalny QUALITY nie fabrykuje tożsamości modelu. |
| C: dokładny cache i integralność | evaluation.start_evaluation, executor._completed_step/_bind_execution_input; completed retry, changed binding/input/queue, corrupt output oraz record_hash tests. |
| D: retry vs REEVALUATE | runtime.run_agent_step i trwała rezerwacja; testy klucza, nowej operacji, konfliktu, reopen, no/distinct identities, duplikatów współbieżnych; nowe R01/R02. |
| E: niezależne statusy | execution_status, validation_status, decision zachowują rozdzielenie; invalid/corrupt binding nie daje ACCEPT. RESERVED jest stanem żądania, nie Evaluation. |
| F/F.1: atomowość, recovery, fencing | BEGIN IMMEDIATE, envelope + STARTED w jednej transakcji; crash matrix, SQLite ABORT/rollback, operator auth, same evaluation_id/binding, nowe tokeny, late finalization, brak timeout takeover. |
| G: konsumenci i authority | executor → trwała ocena → StylePerformance; runtime → chapter lineage → canonical pipeline → run_state → audit. Testy REVISE/REJECT, retry effects, history, projekcji brakujących/sprzecznych i canon approval/verifier/final guard. |
| H: dowody integracji | TestClient(app.main.app), rzeczywiste repozytorium SQLite, pełny pakiet GAP-017 i integracja; wyniki końcowe poniżej. |

Nie powtarzano projektowania fazy 4. Dokończono brakującą bramkę kompatybilności
całego repo oraz ponownie sprawdzono kryteria dotknięte rezerwacją/retry.
AST comparison: wszystkie 38 wcześniejszych funkcji hardening zachowało treść;
brak osłabionych asercji i dodanych skipów (`static-check.json`).

## Testy i niezależny przegląd

| Bramka | Wynik | Exit |
|---|---|---:|
| auditor-r01 — oryginalna sonda | 3 passed, 0 skipped | 0 |
| r01-direct — dokładne node IDs z logów naprawy | 14 passed, 0 skipped | 0 |
| dedup-regression — 6 wcześniejszych przypadków | 6 passed, 0 skipped | 0 |
| retry-transition — reprodukcja R02 przed naprawą | 1 failed | 1 |
| retry-transition-fixed — ta sama sonda po naprawie | 1 passed, 0 skipped | 0 |
| transition-targeted — nowe przypadki naprawy | 5 passed, 0 skipped | 0 |
| gap017-final — komplet GAP-017 po ostatniej zmianie kodu/testów | 143 passed, 0 skipped, 474.25 s | 0 |
| integration-final — 9 plików zależności | 100 passed, 0 skipped, 123.37 s | 0 |
| full-regression — jeden przebieg na finalnym kodzie | 838 passed, 1 skipped, 1755.86 s | 0 |

Integracja: project repository, storage isolation, runtime context/identity,
executor boundary, local operator, run/book locks, ContextBuilder.
Pełny przebieg używa dokładnie `python -m pytest -q --disable-warnings -rs`
oraz unikalnego `--basetemp` z prefiksem `agentpro-gap017-r01-final-`.
Dokładne komendy z rozwiniętymi ścieżkami: `TEST_COMMANDS.md` w dowodach.
Każda bramka zachowuje pełny `<gate>.log`, dokładny `<gate>-command.txt`
oraz `<gate>-exit-code.txt`. Nie sumujemy pakietów do wyniku pełnej regresji.

Jedyny skip: `tests/test_110_canon_api_roundtrip.py:47`, brak legacy
`PATCH /canon/{book_id}` w aktywnym app.main. Dokładnie odpowiada wcześniej
odnotowanemu baseline; nie dodano skipów. Wszystkie bramki końcowe po ostatniej
zmianie kodu/testów przeszły. Nie było potrzeby powtarzania pełnej regresji.

Osobna sesja `/root/gap017_review` wykonała niezależny przegląd tylko do odczytu.
Nie edytowała kodu/testów i nie uruchamiała testów. Sprawdziła kontrakt A–H,
pełny lifecycle, scoped diff, trzy repair diff oraz 18 hashy finalnego snapshotu
(0 rozbieżności). CODE REVIEW = PASS. Po pełnej regresji reviewer niezależnie
odczytał końcowe logi, komendę, exit codes i baseline oraz ponownie sprawdził
18 hashy. Werdykt: INDEPENDENT FINAL REAUDIT = ACCEPT, R01/R02 = VERIFIED_FIXED,
PHASE 5 = READY_FOR_COMMIT. Brak otwartych uwag. Własny przegląd implementatora
nie jest przedstawiany jako niezależny audyt. Dowody:
`independent-code-review.txt` oraz `independent-final-review.txt`.

## Dokładne ścieżki do przyszłego commita

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
18. docs/GAP017_FINAL_REAUDIT.md
19. docs/GAP017_FINAL_REAUDIT_R01.md

W bieżącym przejściu zmieniono tylko project_repository.py, runtime.py,
test_gap017_recovery_hardening.py oraz dodano ten raport. Pozostałe ścieżki są
zastanym zakresem GAP-017; starych raportów REVISE i kontraktu nie zmieniano.
Nowy raport jest jawną dziewiętnastą autoryzowaną ścieżką. Dowody i sondy
znajdują się wyłącznie w ignorowanym katalogu testowym, poza listą commita.

## Ochrona danych i granice dowodów

Wyłącznie neutralne projekty syntetyczne, izolowany storage i unikalny basetemp.
Istniejący conftest blokuje zapisy do rzeczywistych books/runs/novel_runs/audit;
transport modelu jest kontrolowany na granicy zewnętrznego SDK. Recovery używa
syntetycznej autoryzacji operatora w TEMP z ACL Windows; nie inicjalizowano
rzeczywistego poświadczenia. Nie czytano ani nie hashowano realnych książek.

Dowody nie obejmują żywego dostawcy modelu ani fizycznej utraty zasilania.
Reopen oznacza nowe repozytoria/połączenia; awarie są wstrzykiwane na konkretnych
granicach trwałego zapisu. Kontrola baseline to dokładny status/path i hashe
wyłącznie jawnych plików GAP-017/dowodów, nie treści danych użytkownika.

Końcowy status Git i weryfikacja: `final-status.txt`, `final-porcelain.txt`,
`verification.json`; diff tylko z jawnym pathspec. Bez stagingu, commita i pushu.

## Końcowa kontrola worktree

- HEAD i branch bez zmian.
- 2565 wpisów = 2546 baseline + 19 jawnie autoryzowanych ścieżek.
- Dokładne porównanie status/path baseline: delta 0.
- Staging = EMPTY; brak zmian indeksu i konfliktów indeksu.
- Scoped `git diff --check -- <19 jawnych ścieżek>`: exit 0.
- Dodatkowe `git diff --no-index --check -- /dev/null <jawny untracked plik>`:
  exit 1 dla każdej różnicy, bez błędów whitespace. Ostrzeżenie CRLF/LF dla
  historycznego raportu nie oznacza zmiany jego treści.
- Z 23 hashy zastanych plików/dowodów zmieniły się wyłącznie trzy pliki
  naprawy R02. Pozostałe 20 bez zmian, w tym kontrakt i historyczne raporty.
- Finalny snapshot 18 zastanych plików GAP-017 przed końcowymi bramkami:
  0 rozbieżności po testach. Nowy raport doliczono osobno.
- PRE-EXISTING UNRELATED WORKTREE CHANGES PRESERVED = YES w powyższych
  zweryfikowanych granicach; dane realnych książek pozostawały OUT OF SCOPE.
