# Memory Ledger v1 — Phase 5 final audit

Data: 2026-09-23

## Werdykt

FINAL INDEPENDENT AUDIT = REVISE

PHASE 5 = BLOCKED_BY_CONTRACT_CONFLICT

READY_FOR_COMMIT = NO

MEMORY LEDGER V1 = NOT CLOSED

REAL DATA MIGRATION = NOT RUN

COMMIT/PUSH = NOT PERFORMED

Branch i checkpoint są zgodne z poleceniem:
`codex/agentpro-stabilization-freeze` / `b8003d114174eaae5012dab642d22681042c3032`.
Staging pozostał pusty. Nie czytano ani nie haszowano danych realnych książek.

## Blokery i ustalenia

### P5-01 — HIGH — backup poza zamrożoną granicą transakcji

Zamrożony `docs/MEMORY_LEDGER_MIGRATION_PLAN_v1.md` §3 wymaga kolejności:

1. czysty connection z `foreign_keys=ON` i bounded `busy_timeout`;
2. `BEGIN IMMEDIATE`;
3. ponowny odczyt schema i identity;
4. backup przez osobne read-only connection pod write reservation;
5. walidacja backupu, DDL, control marker, wersje identity/schema i commit.

`SchemaMigrationRunner.migrate` w `app/p20_core/project_repository.py` wykonuje
`SchemaMigration.backup` i `before_migrate` przed `BEGIN IMMEDIATE`. Komentarz
uzasadnia to uniknięciem deadlocku SQLite/WAL na Windows, lecz plan nie dopuszcza
takiego wariantu. Pomiędzy backupem i lockiem istnieje okno na commit writera.
Migracja może objąć ten zapis, a backup nie, więc backup nie jest dowodem dokładnego
stanu migrowanego magazynu. Ponowny odczyt wersji pod lockiem chroni decyzję o DDL,
ale nie wiąże backupu ze snapshotem użytym przez migrację.

Dodatkowe braki tej ścieżki:

- `ProjectRepository.migrate_schema` i `SeriesRepository.migrate_schema` nie ustawiają
  `foreign_keys=ON` ani `_DOMAIN_DB_BUSY_TIMEOUT_MS` i nie mapują timeoutu na
  `MAINTENANCE_BUSY`;
- kod zakłada maintenance exclusivity, ale jej nie dowodzi ani nie egzekwuje;
- `_create_verified_sqlite_backup` sprawdza `integrity_check` i schema version,
  lecz nie identity;
- nie powstaje wymagany manifest: scope, source schema, release/HEAD, hash backupu
  i wynik walidacji.

Rozwiązanie P5-01 wymaga osobnej decyzji, jak zachować zamrożoną write reservation
bez powrotu Windows deadlocku. Audyt nie zmienił planu ani kodu.

### P5-02 — HIGH — codec nie wymusza zamkniętego registry per event

Kontrakt §3 wymaga zamkniętego payloadu zależnego od `event_type` i wersjonowanych
slotów. `_validate_payload` stosuje jedną wspólną allowlistę, a obowiązkowe pola
ma tylko dla `LEDGER_BASELINE_OBJECT`, `LEDGER_ACTIVATED` i
`MEMORY_EVENT_CORRECTION`. Pozostałe typy mogą przyjąć pusty lub semantycznie
niepasujący payload; test Phase 2 utrwala `EXTRACTION_STARTED` z `{}`.

Codec nie wymusza również pełnego kontraktu dla: trwałego evidence operatora,
Domain IDs, slot schema per event, zakresu signed int64, confined locatorów,
allowlist `reason_code` i obowiązkowych typed refs. Rekord może mieć poprawny hash,
ale nie spełniać znaczenia wymaganego przez frozen registry.

### P5-03 — HIGH — częściowy target może przejść walidację schema/control

`_validate_project_schema_v6` i `_validate_series_schema_v4` akceptują brak
`memory_ledger_control.v1`, ponieważ sprawdzają stan tylko, gdy row istnieje.
Nie potwierdzają też wymaganej wersji identity.

`validate_memory_ledger_schema` sprawdza możliwość `SELECT`, zbiór nazw triggerów
i wybrane fragmenty ich SQL. Nie sprawdza dokładnych kolumn, constraints, FK,
indeksów ani pełnych definicji triggerów wymaganych przez plan. Ścieżka
target-already-reached i drugi migrator mogą zatem zaakceptować częściowy store.
Normalny read/append sprawdza `control.state == ACTIVE`, ale nie wiąże go za każdym
razem z activation eventem i zapisanym manifestem.

### P5-04 — MEDIUM — bootstrap nie spełnia stabilności locatorów i preflightu

PROJECT i SERIES czytają tabele przez `ORDER BY rowid` i tworzą locator
`table:<table>:<ordinal>` zamiast locatora opartego na rzeczywistym stabilnym
kluczu. Metadata są wybierane szerokimi wzorcami `canonical_%` i
`cross_store_%` zamiast dokładnego registry. Brakuje pełnego preflightu identity,
`integrity_check`, `foreign_key_check`, pending operations oraz odmowy przy sekrecie
lub nierozpoznanej strukturze. `series_operations` zachowuje wymagane pięć kolumn
i surowy `result_payload_json`, lecz locator nadal opiera się na ordinalu.

## Macierz kryteriów

| Kryterium | Kod / symbol | Test lub dowód | Wynik |
|---|---|---|---|
| Append-only, REPLACE, recursive triggers OFF/ON | `create_memory_ledger_schema` | `test_append_only_guards_hold_with_recursive_triggers_off_and_on` | PASS |
| Hash rekordu, indeks encji, scope i references | `read_memory_event`, `append_memory_event` | `test_read_fails_closed_for_corrupt_record_index_and_scope` | PASS dla sprawdzanych bindingów; P5-02/P5-03 blokują pełną zgodność |
| Deterministyczne ID/hash i dawne preimages | `memory_event_id`, `comparison_payload`, producer hooks | testy codec/idempotency Phase 2 i pakiety GAP-015/GAP-016 | PASS |
| Zamknięty payload i slot per event | `_validate_payload`, `MemoryEventRecord.__post_init__` | brak testów negatywnych per registry; `{}` dla `EXTRACTION_STARTED` jest akceptowane | FAIL — P5-02 |
| Pełne producer coverage, odmowy i wyjątki | CanonService, research, F-004/F-005, SERIES close | testy Phase 3 i pakiety integracyjne wskazane w raportach | PASS dla hooków; codec pozostaje FAIL |
| Domena + eventy + receipt; decyzja + used challenge | transakcje repository/CanonService/operator | fault injection Phase 2/3 | PASS |
| Retry, równoległe duplikaty, reopen, late attempt fencing | append/research/recovery | `test_concurrent_duplicate_append_returns_one_durable_event`, testy Phase 3 oraz GAP-017 | PASS |
| F-004/F-005: INTENDED nie oznacza CONFIRMED | `CrossStoreRecoveryService` | `test_f4_intent_recovery_confirmation_and_chapter_versions` | PASS |
| Final audit + CONFIRMED + CHAPTER_VERSION_RECORDED atomowo | final-audit transaction | `test_final_audit_confirmation_and_chapter_events_rollback_as_one_unit` | PASS |
| SERIES transfer/alias bez drugiego close | `SeriesRepository.close_volume` | `test_series_close_and_alias_share_one_closed_event` | PASS |
| Bootstrap `series_operations`, receipts i raw JSON | SERIES bootstrap | `test_series_v3_bootstrap_preserves_all_operation_receipts` | PASS dla zachowania danych; locator FAIL — P5-04 |
| Legacy terminal/pending vs nowy brak eventu | replay owners i baseline lookup | `test_cutover_distinguishes_legacy_terminal_pending_and_new_missing_event` | PASS |
| Pusta SERIES i normalny membership | internal bootstrap / scoped access | `test_series_requires_membership_and_empty_store_activation` | PASS |
| Stabilna paginacja i history read bez side effects | `list_memory_events`, history readers | testy cursor/history Phase 4 | PASS |
| CanonService, approval, final guard, author protection | canonical transactions | Phase 3 oraz canonical PROJECT/SERIES integration suites | PASS |
| Backup i lokalna migracja §3 | `SchemaMigrationRunner.migrate`, `_create_verified_sqlite_backup`, `migrate_schema` | WAL/rollback/race tests Phase 2/4 | FAIL — P5-01; testy nie obejmują okna writer-between-backup-and-lock |
| Exact schema/control/identity validation | validators P6/S4 i `validate_memory_ledger_schema` | target validation Phase 4 | FAIL — P5-03; test nie odrzuca wszystkich częściowych wariantów |
| Stabilny bootstrap registry i preflight | bootstrap PROJECT/SERIES | bootstrap crash/retry/manifest tests | FAIL — P5-04 |

Coverage pozostaje `MEMORY_PIPELINES_V1`. As-of reconstruction, retcony,
ReaderKnowledge, workflow i Skills są poza zakresem i nie są findings tego audytu.

## Niezależny przegląd

Jeden osobny reviewer wykonał read-only review frozen contract/plan, raportów
Phase 2–4, całego scoped diffu i nowych plików, codec/migracji/backupu/bootstrapu,
producer hooks, legacy classification, history read oraz finalnych dowodów.
Reviewer nie zmieniał plików i nie uruchamiał testów. Werdykt: **REVISE**.
Wszystkie P5-01..P5-04 zostały następnie sprawdzone bezpośrednio w bieżącym kodzie
i dokumentach przez autora tego raportu.

## Powiązanie pełnej regresji z finalnym snapshotem

- komenda: `python -m pytest -q --disable-warnings -rs --basetemp=<isolated>`;
- wynik: **866 passed, 1 skipped**, exit **0**, 1507.78 s;
- jedyny skip: historyczny legacy `PATCH /canon/{book_id}` w
  `tests/test_110_canon_api_roundtrip.py:47`;
- `FULL_REGRESSION.log` ma czas wcześniejszy niż snapshot 12 hashy code/test;
- wszystkie 12 bieżących SHA-256 są identyczne ze snapshotem
  `AUTHORIZED_CODE_TEST_HASHES_SHA256.txt`;
- po regresji nie zmieniono kodu ani testów.

Regresja jest aktualna dla audytowanego snapshotu, ale nie dowodzi przypadków
negatywnych P5-01..P5-04. Nie uruchomiono jej ponownie.

## Kryterium jakości literackiej

LITERARY QUALITY EVALUATION = NOT APPLICABLE. Zakres zmienia storage, audit i
integrację Memory Ledger. Nie powstała nowa wersja Skill ani procedury pisania,
redakcji lub krytyki. Zmiany testów style/context/graph dotyczą oczekiwanych wersji
schema. Nie wykonywano publikacji Skill ani płatnych wywołań.

## Dokładna allowlista przyszłego commita

1. `docs/AGENTPRO_GROK_KREION_NARRATIVE_COVERAGE.md`
2. `docs/MEMORY_LEDGER_CONTRACT_v1.md`
3. `docs/MEMORY_LEDGER_MIGRATION_PLAN_v1.md`
4. `app/p20_core/memory_ledger.py`
5. `app/p20_core/project_repository.py`
6. `tests/test_memory_ledger_phase2.py`
7. `tests/test_gap014_adaptive_style.py`
8. `tests/test_p20_context_builder.py`
9. `tests/test_p20_project_graph.py`
10. `docs/MEMORY_LEDGER_PHASE2_REPORT.md`
11. `app/operator_api.py`
12. `app/p20_core/canon_service.py`
13. `app/p20_core/cross_store_recovery.py`
14. `app/p20_core/research.py`
15. `tests/test_memory_ledger_phase3.py`
16. `docs/MEMORY_LEDGER_PHASE3_REPORT.md`
17. `tests/test_memory_ledger_phase4.py`
18. `docs/MEMORY_LEDGER_PHASE4_REPORT.md`
19. `docs/MEMORY_LEDGER_FINAL_AUDIT.md`

To jest lista kontroli zakresu, nie zgoda na commit. Pliki dowodowe w
`.test_storage/memory_ledger/` są ignorowane i nie należą do przyszłego commita.

## Git, baseline i ograniczenia

- status przed raportem: 2564 = 2546 baseline + 18 autoryzowanych ścieżek;
- status po raporcie: 2565 = 2546 baseline + 19 autoryzowanych ścieżek;
- dokładne status/path baseline po odjęciu allowlisty: bez zmian, delta 0;
- scoped diff/check: PASS;
- staging: EMPTY;
- nie wykonano reset/restore/clean/stash/checkout/pull/add/commit/push.

Dowody: `.test_storage/memory_ledger/phase5/`.

REAL DATA MIGRATION = NOT RUN

LIVE PROVIDER = NOT TESTED

PHYSICAL POWER LOSS = NOT TESTED

Następna wymagana bramka: decyzja kontraktowa dla P5-01, a następnie naprawy
P5-01..P5-04, nowe testy negatywne, ponowna pełna regresja i niezależny review
finalnego kodu.
