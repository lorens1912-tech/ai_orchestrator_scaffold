# Memory Ledger v1 — Phase 4 resistance and regression report

STATUS = TEST SUITE PASS

Data: 2026-09-23. Wszystkie mutacje storage wykonano wyłącznie na neutralnych,
syntetycznych fixtures. Realnych książek nie czytano ani nie haszowano.

## Naprawione defekty

1. **Odczyt i paginacja nie weryfikowały całego bindingu historii.** Kursor nie
   wiązał activation/filter/high-watermark hash, a odczyt nie porównywał wszystkich
   kolumn indeksowych z immutable record. Dodano closed cursor, snapshot transaction,
   walidację record/hash/scope/index oraz fail-closed list/get/idempotent replay.
2. **Retry aktywnego bootstrapu ufał wyłącznie activation event.** Teraz ponownie
   odczytuje wszystkie zapisane baseline events, wylicza manifest i porównuje count,
   hash oraz coverage z control i activation. Późniejszy current state nie zmienia
   zapisanego baseline.
3. **Migracja miała dwa problemy współbieżności backupu.** Drugi migrator mógł
   zobaczyć P6 podczas backupu rozpoczętego dla P5; target jest teraz akceptowany
   wyłącznie po ponownym locku i pełnej walidacji. Ogólny pierwszy
   `SchemaMigration.backup` działał po `BEGIN IMMEDIATE`, co blokowało Windows
   SQLite backup bez końca. Początkowy hook przeniesiono przed lock; wersja jest
   nadal ponownie czytana pod lockiem przed DDL.

## Macierz kryteriów §7 planu migracji

| Kryterium | Symbol / test | Dowód Phase 4 | Brak |
|---|---|---|---|
| Append-only | `memory_ledger.create_memory_ledger_schema`; `test_append_only_guards_hold_with_recursive_triggers_off_and_on` | UPDATE/DELETE/REPLACE eventu i entity index odrzucone przy OFF/ON | brak |
| Integralność | `read_memory_event`; `test_read_fails_closed_for_corrupt_record_index_and_scope` | JSON/hash/scope/index fail-closed w get i list | brak |
| Idempotencja | `append_memory_event`; Phase 2 duplicate/content conflict, Phase 3 producer retries | ten sam key odtwarza record, zmieniony payload konflikt, sloty osobne | brak |
| Współbieżność | Phase 2 concurrent append; Phase 3 canonical/GAP017; migration race regression | pojedynczy event/owner, brak success fallback | fizyczna utrata zasilania |
| Izolacja | Phase 2 SERIES membership; Phase 3 PROJECT/SERIES P20; canonical suites | brak leakage i PROJECT fallback | brak |
| Authority | Phase 3 author decision fault; canonical pipeline suite | WAIT/REJECT/STALE/verifier fail bez canonical success | live identity provider poza testowym DPAPI |
| Atomic batch | Phase 3 `test_active_p20_canonical_pipeline_writes_one_complete_event_set` | append fault cofa encje, historię, receipt i eventy | brak |
| Decision | ten sam test Phase 3 | fault po challenge use cofa decision i `used`; retry jeden event | brak |
| P20 coverage | Phase 3 PROJECT/SERIES API tests, research tests | producenci §2 obecni w aktywnym P20 | live provider |
| F4 granice | `test_f4_intent_recovery_confirmation_and_chapter_versions`; Phase 4 final-audit rollback test | INTENDED != CONFIRMED; final audit + confirmed + chapters atomowe | fizyczna utrata zasilania |
| BOOK_LOCKED | `test_gap017_recovery_hardening.py` R01/API matrix | IDs zachowane, brak fikcyjnego startu | brak |
| Late result | GAP017 recovery/fencing tests; research recovery tests | stary attempt nie finalizuje wyniku/eventu | brak |
| Reopen | canonical, research, F4/F5, GAP017 suites | trwały receipt/event, retry bez nowych wersji | brak |
| Paginacja | `test_cursor_freezes_snapshot_and_rejects_every_binding_change` | snapshot high-watermark; scope/filter/activation/hash tamper odrzucony | brak |
| Legacy schema | Phase 2 P5/S3 i Phase 3 cutover tests | dawne dane zachowane, UNKNOWN fail-closed | realna migracja danych |
| Bootstrap | Phase 2 bootstrap/race; Phase 4 crash/retry/manifest test | rollback przed activation, jeden retry, saved manifest | realna migracja danych |
| Migration crash | `test_migration_rollback_schema_ready_gate_and_target_validation` | old schema albo pełny SCHEMA_READY; producer blocked | fizyczna utrata zasilania |
| Dwa migratory | Phase 2 race + naprawa Phase 4 | reread pod lockiem; osiągnięty target walidowany | brak |
| Legacy operations | Phase 2 S3 alias/raw payload; Phase 3 cutover classification | operation_id i raw result zachowane; nowe braki intervention | realna migracja danych |
| Backup/restore | `test_wal_backup_contains_committed_state`, corrupt backup refusal, graph backup hook | WAL uwzględniony; zła kopia odmowa; brak deadlocku | automatyczny restore celowo poza kontraktem |
| Frozen hashes | szerokie istniejące pakiety GAP014/015/016/017/F4/F5/context | pełna regresja PASS; hashy kontraktowych nie zmieniono | brak |
| Read-only | `test_history_read_never_creates_or_migrates_storage` | absent/P5 read nie tworzy DB, migracji, control ani eventu | brak |

## Macierz producentów §2 kontraktu

| Zdarzenie | Owner / hook | Test / wynik |
|---|---|---|
| LEDGER_BASELINE_OBJECT / LEDGER_ACTIVATED | PROJECT/SERIES bootstrap | Phase 2 P5/S3, Phase 4 crash/retry/manifest: PASS |
| EXTRACTION_STARTED / ATTEMPT / FINISHED | `canon_service.process_accepted_artifact` | Phase 3 PROJECT/SERIES P20 + canonical suites: PASS |
| PROPOSAL_RECORDED / STATE_RECORDED | CanonService proposal transaction | Phase 3 + 96-test canonical/research gate: PASS |
| AUTHOR_DECISION_RECORDED | `record_operator_decision` | challenge rollback/retry i protected workflow: PASS |
| CANONICAL_ENTITY_CHANGED / COMMITTED | canonical owner transaction | mixed PROJECT/SERIES, batch rollback, reopen/retry: PASS |
| RESEARCH_COMMAND_RECORDED / RESULT_RECORDED | `research._save_command`, run/recover | GAP015 + Phase 3 research: PASS |
| ARTIFACT_WRITE_INTENDED | F4 durable intent | F4 crash boundary: PASS |
| SERIES_VOLUME_CLOSED | `SeriesRepository.close_volume` | close/alias/retry/isolation: PASS |
| ARTIFACT_WRITE_CONFIRMED | F4 final audit | file hash verification i rollback final transaction: PASS |
| CHAPTER_VERSION_RECORDED | F5 hook w final audit | version lineage/reopen/retry i forced N-th append fault: PASS |
| MEMORY_EVENT_CORRECTION | controlled repository append | Phase 4 immutable target + new correction event: PASS |

## Scenariusz autorski

`tests/test_canonical_pipeline.py::test_protected_operator_commit_and_legacy_file_cannot_bypass`
przechodzi rzeczywisty syntetyczny canonical workflow z ochroną, decyzją autora,
commitem, historią, retry oraz dowodem, że wcześniejsza ścieżka chapter-file nie
obchodzi ochrony. `tests/test_chapter_artifact_lineage.py` potwierdza trwały
artefakt rozdziału, reopen, lineage i izolację. Nie oceniano jakości literackiej.

## Testy

- Phase 4 targeted: `12 passed`, exit 0.
- Storage dependencies: `82 passed`, exit 0.
- Canonical/research/operator: `96 passed`, exit 0.
- F4/F5/GAP017: `157 passed`, exit 0.
- Final graph + Memory Ledger stabilization: `76 passed`, exit 0.
- Pełna regresja po ostatniej zmianie: `866 passed, 1 skipped`, exit 0,
  `1507.78s`. Skip to istniejący legacy PATCH `/canon/{book_id}`, którego aktywny
  `app.main` nie wystawia.

Dokładne komendy, pierwszy przerwany log, finalny log i exit codes znajdują się w
`.test_storage/memory_ledger/phase4/TEST_COMMANDS.md`.

## Git, baseline i granice

- Branch: `codex/agentpro-stabilization-freeze`.
- HEAD: `b8003d114174eaae5012dab642d22681042c3032`.
- Bazowe 2546 status/path i osobny wcześniejszy coverage report zachowane.
- Staging pozostaje pusty; commit/push nie wykonano.
- Real data migration nie została wykonana.
- Własna kontrola nie jest niezależnym audytem.

PHASE 4 = TEST SUITE PASS

FULL REGRESSION = PASS

PHASE 5 = NOT STARTED

INDEPENDENT FINAL AUDIT = NOT PERFORMED

REAL DATA MIGRATION = NOT RUN

MEMORY LEDGER V1 = NOT CLOSED

COMMIT/PUSH = NOT PERFORMED

LIVE PROVIDER = NOT TESTED

PHYSICAL POWER LOSS = NOT TESTED
