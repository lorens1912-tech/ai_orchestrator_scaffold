# GAP-017 — Phase 5: niezależny audyt końcowy

FINAL AUDIT = REVISE
PHASE 5 = NOT READY_FOR_COMMIT
GAP-017 = NOT CLOSED
FULL REGRESSION = NOT RUN
COMMIT/PUSH = NOT PERFORMED

## Zakres i checkpoint

Audyt zastanego kodu i dowodów fazy 4, bez napraw implementacji i bez zmiany kontraktu.
Branch: `codex/agentpro-stabilization-freeze`.
BASE HEAD i końcowy HEAD: `bfe0bc2e6d35a44b78b6849d4af64ba57cc13e61`.
Preflight: staging pusty; 2562 wpisy status/path = baseline 2546 + 16 autoryzowanych ścieżek. Porównano rzeczywiste wpisy, nie tylko liczby; delta = 0.
Raport nie istniał przed audytem. Jest osobną, siedemnastą autoryzowaną ścieżką.

Źródła: zamrożony GAP017_EVALUATION_CONTRACT (w tym RECOVERY v1), GAP017_PREFLIGHT, pełny scoped diff i nowe pliki, surowe logi i macierze fazy 4, właściwe fragmenty Master Canonu, Architektura §86–92, kontrakty GAP-016/F-004/F-005 oraz Canonical Change i autoryzacja operatora. Dla F-007 zweryfikowano aktywny ContextBuilder i istniejący test deduplikacji; raport fazy 4 wskazuje ten test, nie osobny nowy kontrakt F-007.

## Bloker GAP017-P5-01 — brak deduplikacji nowego żądania REEVALUATE

**Wymaganie:** GAP017_EVALUATION_CONTRACT.md §D, linia 129: klient może podać nową stabilną tożsamość żądania do deduplikacji duplikatów. Bez niej odrębne żądania są odrębnymi operacjami. Nowa operacja nie może odziedziczyć operation_id oceny źródłowej.

**Kod:** `app/p20_core/runtime.py::run_agent_step`, linie 518–530. Każde REEVALUATE dostaje losowy `step-reevaluate-{uuid4}`, nawet gdy klient poda nowy stabilny step_id. `app/p20_core/contracts.py::AgentStepRequest` i aktywna obsługa payload nie udostępniają innego mechanizmu wiązania stabilnego identyfikatora żądania z nową operacją. Samo extra=allow nie implementuje deduplikacji.

**Precyzja wniosku:** kontrakt nie narzuca nazwy pola wire API. Nie twierdzę, że musi nią być step_id. Brakuje obiecanej możliwości; test używa istniejącego step_id jako kandydata i pokazuje jego ignorowanie. Technical retry ze zwróconymi IDs działa, ale nie rozwiązuje duplikacji pierwotnego żądania przed otrzymaniem odpowiedzi.

**Reprodukcja:** TestClient(app.main.app), rzeczywisty /agent/step → P20 → lokalny QUALITY → project.db, neutralne dane w istniejącym isolated_agentpro_storage. Najpierw ocena źródłowa, potem dwa identyczne REEVALUATE wskazujące tę ocenę i ten sam nowy step_id. Obie odpowiedzi HTTP 200; powstają różne oceny:

- EVAL-6c49a614d58c46db8d48e7191ea91188
- EVAL-b89c5a8996cb412aa73c75a8de68bb49

Repozytorium zawiera trzy trwałe oceny wraz ze źródłową. Nowy test kończy się oczekiwanym wykryciem rozbieżności: **1 failed in 10.37s**, exit code **1**.
Dowody: `.test_storage/gap017_evidence/phase5/test_reevaluation_request_identity.py`, `targeted-command.txt`, `targeted.log`, `targeted-exit-code.txt`.
Nie mockowano runtime, evaluatora ani repozytorium. Załadowano istniejący tests.conftest dla izolacji i kontroli zewnętrznego SDK; lokalny QUALITY nie wywołuje płatnego modelu.

**Minimalna naprawa:** w istniejących granicach API i project.db obsłużyć opcjonalną nową stabilną tożsamość żądania, trwale związaną z nową operacją REEVALUATE i jej wejściami. Duplikat tego samego wiązania odtwarza tę samą nową ocenę; zmienione wiązanie powoduje konflikt. Brak tożsamości nadal oznacza osobne operacje. Zachować nową tożsamość względem źródła oraz późniejszy technical_retry. Dodać testy duplikatu, konfliktu, współbieżności, reopen oraz żądań bez identyfikatora. Nie potrzeba nowego recovery engine ani zmiany decyzji architektonicznej.

Implementacji i kontraktu nie naprawiano w tej sesji audytowej.

## Kryterium → kod → test / dowód

Wyniki testów poniżej, poza reprodukcją P5-01, są **dowodami fazy 4**, których surowe końcowe logi odczytano. Nie przedstawiam ich jako ponownego uruchomienia w fazie 5.

| Kryterium | Kod / właściciel | Test i wniosek |
|---|---|---|
| A: owner, statusy, hashe, brak fikcyjnego modelu | ProjectRepository.evaluation_transaction; evaluation.py: EvaluationBinding.local_deterministic, walidatory envelope/record/output, start_evaluation/finalize_evaluation | test_complete_record_has_exact_contract_fields_and_valid_decision; test_local_deterministic_has_no_fictitious_model_identity; test_each_cache_preimage_component_changes_key; test_record_hash_integrity_fails_closed. Właściciel project.db, oddzielne osie, lokalne model/invocation nie są fabrykowane. Kod + faza 4. |
| B: reuse i zmieniony binding | executor._bind_execution_input/_completed_step; evaluation.start_evaluation; runtime._active_quality_evaluation | test_completed_retry_reuses_output_and_skips_evaluator; test_same_operation_changed_binding_is_conflict_not_cache_miss; test_retry_changed_input_before_projection_is_rejected. Zmienione wiązanie nie jest cichym cache miss. |
| B: aktualna wersja tekstu | runtime._active_quality_evaluation; exact artifact identity/hash w executor | test_runtime_selects_latest_exact_artifact_and_rejects_stale_accept; test_same_bytes_rewrite_is_new_artifact_not_old_accept; test_quality_preserves_exact_whitespace_artifact_binding. Nie wybiera według nazw plików ani samych bajtów. |
| C: ograniczenie completed-step reuse | executor: warunek WRITE/EDIT/REWRITE z QUALITY w initial_modes | test_completed_writer_is_not_reexecuted_by_quality_retry oraz trzy wcześniejsze przypadki CRITIC, FACTCHECK i standalone WRITE. Końcowy surowy log resume-three: 3 passed. Testów istniejących kontraktów nie zmieniono. |
| D: recovery/fencing | evaluation.recover_evaluation/recover_evaluation_by_identity; operator_api.recover_quality_evaluation, authenticated_operator i _with_operator | test_operator_recovery_states_reopen_and_fencing; test_operator_recovery_authorization_identity_and_duplicates; test_concurrent_recovery_and_late_finalization; test_old_running_attempt_is_not_taken_over_without_operator_recovery. Ten sam evaluation_id/binding, nowy token, bez takeover po timeout. Hardening używa syntetycznej autoryzacji DPAPI; wcześniejszy test z mockiem autoryzacji nie jest samodzielnym dowodem zabezpieczenia. |
| D: REEVALUATE | runtime.run_agent_step | test_reevaluation_has_new_identity_and_retry_intent_is_invalid potwierdza nową operację. Nowy test fazy 5 wykrywa brak opcjonalnej deduplikacji: REVISE. |
| E: StylePerformance i historyczne powiązania | executor: finalizacja oceny po style veto; adaptive_style.finalize; runtime i chapter_lineage | test_style_downstream_crashes_are_idempotent; test_style_nonaccept_has_no_performance_after_reopen_retry; test_reevaluation_keeps_historical_performance_binding. Historyczny rekord nie jest przepisywany. Test historii przygotowuje sesję stylu bezpośrednio, a następnie korzysta z QUALITY API; nie jest pełnym testem żywego Writera. |
| E: projekcje/lineage | executor._completed_step; cross_store_recovery; runtime; chapter_lineage._evaluation_ref | test_projection_replay_or_conflict; test_chapter_projection_conflict_requires_intervention; test_actual_lineage_boundary_precedes_audit. Rzeczywista kolejność runtime: chapter/lineage, canonical pipeline, run_state, audit; nie zakładano hipotetycznego audit przed chapter. |
| E: authority | runtime canonical refusal; canon_service i istniejący pipeline propozycji/zgody/final guard | test_accept_does_not_override_canon_check; test_evaluation_accept_has_no_canonical_authority. ACCEPT nie zastępuje ekstrakcji, weryfikacji i uprawnień. Fixture kontroluje provider ekstrakcji/weryfikacji: to dowód authority, nie żywego transportu. |
| F: SQLite | ProjectRepository.connect (1178), initialize (1218), evaluation_transaction (1373) | Statycznie: timeout/busy_timeout 30 s, ograniczony czasowo retry tylko idempotentnego PRAGMA WAL na SQLITE_BUSY; inne błędy nie są zamieniane w sukces. BEGIN IMMEDIATE poprzedza inicjalizację schematu i wersji; walidacja wersji pozostaje. test_real_sqlite_failure_rolls_back_final_record dowodzi rollback przez rzeczywisty SQLite ABORT. Faza 4 concurrency/integration potwierdza otwieranie i współbieżność; nie ma osobnego dowodu wyczerpania limitu WAL. |
| F: pozostali użytkownicy repozytorium | współdzielone connect/initialize używane też przez context/style/research/canon/model provenance | Zmiana nie jest evaluation-only: szersza serializacja inicjalizacji dotyczy wszystkich tych ścieżek. Pakiet integracyjny fazy 4 to 249 passed; brak pełnej regresji oznacza, że zgodność całego repo pozostaje NOT VERIFIED. |
| G: jakość dowodów | trzy nowe pliki testów GAP017, conftest i surowe logi | Brak nowych skipów i osłabienia istniejących testów w audytowanym zakresie. Testy concurrent korzystają z rzeczywistych wątków/barier i SQLite. Reopen oznacza nowe połączenia/repozytoria; nie dowodzi odporności na utratę zasilania. |

## Testy i ograniczenia

- Faza 4, ponownie odczytane końcowe logi: resume-three **3 passed**, exit 0; gap017-suite **118 passed**, exit 0; integration **249 passed**, exit 0. Pakiety mogą się pokrywać; nie sumowano ich do wyniku regresji.
- Faza 5: wyłącznie test konkretnej nierozstrzygniętej wątpliwości, **1 failed**, exit 1.
- FULL REGRESSION = NOT RUN; exit code = N/A; skipy bieżącego pełnego przebiegu = N/A. Użytkownik nakazuje nie uruchamiać kosztownej regresji przy znanym blokerze. Pełna zgodność = NOT VERIFIED.
- Historyczny skip baseline: tests/test_110_canon_api_roundtrip.py:47, brak legacy PATCH /canon/{book_id} w aktywnym app. Nie jest wynikiem bieżącego przebiegu.
- Brak wywołań żywego dostawcy, brak ręcznego serwera. Nie odczytywano ani nie haszowano rzeczywistych książek. Ochrona danych: istniejące izolowane fixture i write guards; porównanie baseline wyłącznie status/path, bez twierdzenia o porównaniu zawartości wszystkich danych.

## Zakres przyszłego commita (nie jest jeszcze gotowy)

Pierwotne 16 autoryzowanych plików; ich zawartości nie zmieniono podczas fazy 5:

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

Osobno: 17. docs/GAP017_FINAL_AUDIT.md.

Test diagnostyczny i logi znajdują się w ignorowanym phase5 evidence. Naprawa powinna przenieść odpowiedni test regresyjny do istniejącego autoryzowanego pliku testów. Przed przyszłym commitem wymagane są naprawa P5-01, ponowna ocena oraz wymagane bramki; nie wykonano stagingu, commita ani pushu.

## Końcowa kontrola

Dokładne wyniki: `.test_storage/gap017_evidence/phase5/verification.json`.
Pełny końcowy git status --short: `.test_storage/gap017_evidence/phase5/final-status.txt`.
Porównanie baseline wyłącza wyłącznie powyższe 17 ścieżek. Dowody fazy 4 pozostawiono bez zmian.

Końcowy wynik kontroli: 2563 wpisy = 2546 baseline + 17 autoryzowanych ścieżek; dokładna baseline delta = 0. SHA256 pierwotnych 16 plików kodu/dokumentacji/testów bez zmian względem preflight fazy 5. Staging = EMPTY. Scoped git diff --check = PASS (exit 0; standardowy diff nie obejmuje treści untracked). HEAD i branch bez zmian. Dane realnych książek nie były objęte hashowaniem. PRE-EXISTING WORKTREE CHANGES PRESERVED = YES w zweryfikowanych granicach status/path oraz integralności 16 plików zakresu.
